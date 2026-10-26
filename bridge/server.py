"""桥接服务：连接 EEG 服务、Agent 服务与 Unity 客户端。

- /ws/eeg   : eeg_service 推送脑波状态
- /ws/world : Unity / Agent 订阅与交互"世界参数 + 引导语"
- /ws/video/pub : Unity 推 JPEG 帧（二进制）
- /ws/video/sub : 控制台收帧（二进制，原样转发）

另外在同端口的普通 HTTP 上提供只读状态接口（握手前拦下来，不进 WebSocket）：
- /status  : 控制台点开顶部任何一盏灯时来问的"现在到底什么情况"，JSON

为什么要 /status：
控制台顶部那些灯以前是前端自己猜的（"6 秒没数据就显示预览"），
点开也没有任何东西可看。这里把桥接真正知道的事——谁连上了、多久以前
收到过消息、最近一条状态长什么样、智能体最后说了什么——原样吐出来，
前端只负责排版，不再猜。

使用 websockets 新版 asyncio API，按路径分发。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import socket
import time
from urllib.parse import parse_qs, urlparse
from datetime import datetime

from websockets.asyncio.server import serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Response

HOST = os.getenv("BRIDGE_HOST", "0.0.0.0")
PORT = int(os.getenv("BRIDGE_PORT", "8765"))
SEND_TIMEOUT = 1.0   # 广播给单个客户端的超时（秒）

STARTED_AT = time.time()
PROCESS_PID = os.getpid()

eeg_clients: set = set()
world_clients: set = set()

# ── 视频通道 ────────────────────────────────────────────────
# Unity 每帧推一张 JPEG（二进制），桥接原样转发给控制台。
# 不解析、不转码、不落盘：这里只是个"搬运工"，越薄越不容易出问题。
video_pubs: set = set()      # 推流端（Unity 里的 CameraStreamer）
video_subs: set = set()      # 观看端（控制台网页）
wave_subs: set = set()       # 16 通道波形观看端（控制台「信号与判据」页）
video_busy: set = set()      # 正在发帧的观看端；发不完就丢帧，绝不排队
latest_frame: bytes | None = None
latest_frame_at: float = 0.0   # 这一帧是什么时候来的（用来判断还新不新鲜）

# 多少秒没来帧就算"画面没了"。推流是 ~11fps，3 秒足够判断；
# 原来这里用的是 20 秒 —— 结果停止后 20 秒内再点开始，服务端以为画面还在，
# 直接返回 already-playing 什么都不做，界面就永远停在"Unity 还没开始播放"。
FRESH_FRAME_S = 3.0
last_frame_bytes = 0
# 最近一条真实状态。在收到第一条之前是 None。
# ⚠️ 不能拿 {"label": "unknown"} 这种占位当默认值往下发：
# Unity 那边用 JsonUtility 解析，**缺失的字段会被填成全 0**，
# 于是新接入的 Unity 会先把画面拉到「全黑 + 小花」，看着像坏了。
latest_state: dict | None = None
latest_state_at: float = 0.0     # 上一条真实状态到达的时刻（回放时判新鲜度用）

# ── 给 /status 用的账本 ─────────────────────────────────────
# 全是"发生过什么"的流水账，只用来回答"现在到底什么情况"，不参与业务。
stats = {
    "eeg_msgs": 0,          # 累计收到多少条脑电状态
    "eeg_last_at": 0.0,     # 最近一条的时刻（monotonic）
    "world_msgs": 0,        # world 总线累计消息数
    "world_last_at": 0.0,
}
# 每个 world 连接的来头：谁、什么时候来的、发了多少条
world_meta: dict = {}
latest_guidance: dict | None = None
latest_guidance_at: float = 0.0
latest_strategy_at: float = 0.0


def _age(t: float) -> float | None:
    """距现在多少秒（None = 从没发生过）。"""
    return round(time.monotonic() - t, 1) if t else None


def _role_from_ua(ua: str) -> str:
    """凭握手时的 User-Agent 猜来头。

    Unity 的 ClientWebSocket 不带 UA，Python 的 websockets 带 "Python/..."，
    浏览器带 "Mozilla/..."。猜不准也没关系，前端会写成"未知（多半是 Unity）"。
    """
    u = (ua or "").lower()
    if "python" in u:
        return "agent"
    if "mozilla" in u or "safari" in u or "chrome" in u or "webkit" in u:
        return "console"
    if not u:
        return "unity"
    return "other"


def _state_summary() -> dict | None:
    if latest_state is None:
        return None
    s = latest_state
    return {
        "label": s.get("label"),
        "relax": s.get("relax"),
        "score": s.get("score"),
        "n_good": s.get("n_good"),
        "fs": s.get("fs"),
        "board_id": s.get("board_id"),
        "note": s.get("note"),
        "round": s.get("round"),
        "strategy": s.get("strategy"),
        "strategy_name": s.get("strategy_name"),
        "null_arm": s.get("null_arm"),
        "round_seconds": s.get("round_seconds"),
        "world_params": s.get("world_params"),
        "rec": s.get("rec"),
        "channels": s.get("channels") or [],
        "focus": s.get("focus"),
        "bands": s.get("bands"),
        "lr": s.get("lr"),
        "bt": s.get("bt"),
    }


def latest_report() -> Path | None:
    """sessions/ 里最新的一份报告；还没跑过训练就返回 None。"""
    try:
        if not SESSION_DIR.exists():
            return None
        files = sorted(SESSION_DIR.glob("report-*.html"),
                       key=lambda p: p.stat().st_mtime)
        return files[-1] if files else None
    except OSError:
        return None


def status_snapshot() -> dict:
    """把桥接现在知道的一切吐成 JSON。控制台的抽屉就是渲染这个。"""
    now = time.time()
    detail = []
    for ws, meta in list(world_meta.items()):
        if ws not in world_clients:
            continue
        detail.append({
            "role": meta.get("role") or "unknown",
            "ua": (meta.get("ua") or "")[:80],
            "connected_s": round(time.monotonic() - meta.get("at", time.monotonic()), 1),
            "msgs": meta.get("msgs", 0),
        })
    detail.sort(key=lambda d: -d["connected_s"])

    st = _state_summary()
    board_id = (latest_state or {}).get("board_id")
    if not eeg_clients:
        source = "none"          # 没人在喂数据
    elif board_id == -1:
        source = "synthetic"     # 合成板
    else:
        source = "real"

    return {
        "ok": True,
        "server": {
            "pid": PROCESS_PID,
            "host": HOST,
            "port": PORT,
            "started_at": datetime.fromtimestamp(STARTED_AT).strftime("%Y-%m-%d %H:%M:%S"),
            "now": datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S"),
            "uptime_s": round(now - STARTED_AT, 1),
        },
        "session": {
            "recording": session._fh is not None,
            "file": session.path.name if session.path else None,
            "frames": session.count,
            "report": latest_report().name if latest_report() else None,
        },
        "device": device_snapshot(),
        "channels": {
            "eeg": {
                "clients": len(eeg_clients),
                "msgs": stats["eeg_msgs"],
                "last_age_s": _age(stats["eeg_last_at"]),
            },
            "world": {
                "clients": len(world_clients),
                "msgs": stats["world_msgs"],
                "last_age_s": _age(stats["world_last_at"]),
                "detail": detail,
            },
            "video": {
                "publishers": len(video_pubs),
                "viewers": len(video_subs),
                "last_frame_age_s": _age(latest_frame_at),
                "last_frame_bytes": last_frame_bytes,
            },
        },
        "source": source,
        "state": st,
        "state_age_s": _age(stats["eeg_last_at"]),
        "guidance": {
            "text": (latest_guidance or {}).get("script"),
            "cue": (latest_guidance or {}).get("cue"),
            "state": (latest_guidance or {}).get("state"),
            "age_s": _age(latest_guidance_at),
        },
    }


# ==========================================================
# 演示开关：页面上的「开始演示 / 停止」按钮打到这里
# 演示时用合成板（不碰真电极），停止时把进程和 Unity 播放一起关掉
# ==========================================================
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYBIN = str(ROOT / ".venv" / "bin" / "python")
LOGDIR = ROOT / ".data" / "logs"

# ── 会话记录 ────────────────────────────────────────────────
# 每收到一条脑电状态就追加一行 JSONL；EEG 断开时收尾，顺手把这次训练
# 压成一页 HTML 报告（sessions/report-*.html）。报告是副产物，
# 任何一步出错都只打日志，绝不能让记录把桥接带崩。
import sys as _sys

if str(ROOT) not in _sys.path:
    _sys.path.insert(0, str(ROOT))
from analysis.session_report import SessionRecorder  # noqa: E402
# 随访量表：题目、计分、分层都放在 analysis/scales.py，桥接只负责收和存。
from analysis.scales import (questionnaire as scale_form,  # noqa: E402
                             score_gad7, score_phq2, score_phq9, triage)

SESSION_DIR = Path(os.getenv("SESSION_DIR", str(ROOT / "sessions")))
session = SessionRecorder(SESSION_DIR)

UNITY_TOGGLE = (
    'tell application "System Events" to tell process "Unity" '
    'to click menu item "切换 Play 状态" of menu "NeuroBloom" of menu bar 1'
)
BACK_TO_BROWSER = (
    'tell application "System Events" to (name of processes) contains "Safari"'
)

_children: dict[str, subprocess.Popen] = {}

# ── VR 成品 ──────────────────────────────────────────────────
# 目标：用户全程不碰 Unity 编辑器。控制台点「开始演示」= 直接启动构建好的
# NeuroBloom.app（窗口模式、自动连桥接），停止 = 让它退出。
# 窗口模式见下面 _player_args()：默认不全屏，全屏要 export NB_PLAYER_FULLSCREEN=1。
# 只有在没有成品时才退回到「去点编辑器 Play」的老路（开发时用）。
PLAYER_APP = ROOT / "UnityVR" / "Build" / "NeuroBloom.app"
PLAYER_MATCH = "Build/NeuroBloom.app/Contents/MacOS/"

# 成品以「窗口」启动，不再一开就抢全屏。
# 起因（2026-10-21 实测）：成品原来按打包设置全屏跑。用户一退出全屏（或只是点到
# 控制台网页），窗口就失去焦点；这时只要 runInBackground 没真正生效，Update 就停，
# CameraStreamer 的抓帧和推流一起停 → 网页直播卡死，看起来像「Unity 停了」。
# 所以把窗口模式作为启动参数钉死，跟打包设置解耦。
# 想要全屏：启动前 export NB_PLAYER_FULLSCREEN=1
PLAYER_WINDOW = os.getenv("NB_PLAYER_WINDOW", "1280x800")


def _player_args() -> list[str]:
    """给成品进程的启动参数。

    默认**窗口模式**：全屏窗口在 macOS 上会新开一个 Space，等于把用户正在用的
    桌面切走。开发和日常调试一律窗口模式，只有正式演示才 export
    NB_PLAYER_FULLSCREEN=1 全屏。
    """
    if os.getenv("NB_PLAYER_FULLSCREEN", "0") == "1":   # 正式演示才开全屏
        return ["-screen-fullscreen", "1"]
    args = ["-screen-fullscreen", "0"]
    try:
        w, h = PLAYER_WINDOW.lower().split("x", 1)
        args += ["-screen-width", str(int(w)), "-screen-height", str(int(h))]
    except Exception:  # noqa: BLE001
        pass
    return args


def _player_running() -> bool:
    """成品进程在不在。

    不能用 pgrep -f：那是子串匹配，任何命令行里带这段路径的进程都会被算进来
    （我在终端里打过一条含该路径的测试命令，结果被误判成「已在运行」，
    于是「开始演示」什么都没启动）。这里改成比对进程的第一个参数，要求完全相等。
    """
    exe = str(PLAYER_APP / "Contents/MacOS/NeuroBloom")
    try:
        r = subprocess.run(["ps", "-Ao", "args="],
                           capture_output=True, text=True, timeout=4)
        for line in (r.stdout or "").splitlines():
            # 只看第一个参数（可执行文件），避免命令行里带路径的 shell 被误判。
            # 用 endswith 而不是 ==：`open -a` 有时会补上绝对路径前缀或解析到注册副本。
            first = line.strip().split(" ", 1)[0]
            if first == exe or first.endswith("/NeuroBloom.app/Contents/MacOS/NeuroBloom"):
                return True
        return False
    except Exception:  # noqa: BLE001
        return False


def _launch_player() -> str:
    if not PLAYER_APP.exists():
        return "no-build"
    if _player_running():
        return "already-running"
    try:
        # -g：后台启动，不抢当前焦点，正在看别的东西的人不会被打断
        argv = ["open", "-g", "-a", str(PLAYER_APP), "--args", *_player_args()]
        subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return "launched"
    except Exception as e:  # noqa: BLE001
        return f"failed:{type(e).__name__}"


def _quit_player() -> str:
    if not _player_running():
        return "not-running"
    try:
        subprocess.run(["osascript", "-e", 'tell application "NeuroBloom" to quit'],
                       capture_output=True, text=True, timeout=6)
    except Exception:  # noqa: BLE001
        pass
    for _ in range(10):
        if not _player_running():
            return "quit"
        time.sleep(0.3)
    subprocess.run(["pkill", "-f", PLAYER_MATCH], capture_output=True, timeout=6)
    return "killed"


def _restart_player() -> str:
    """把成品关掉再起一遍。

    为什么要单独有这么一个：画面卡住（推流断了但进程还活着）时，
    _launch_player() 只会返回 already-running，什么都不会发生 ——
    用户在控制台点「重新启动画面」必须真的能重启。
    """
    if not PLAYER_APP.exists():
        return "no-build"
    if _player_running():
        _quit_player()
        time.sleep(0.8)          # 等旧进程真的退干净，否则新进程会被当成重复实例
    return _launch_player()


# ── 设备连接 ────────────────────────────────────────────────
# 用户的要求很明确：打开软件就该连上设备，连不上要能看见"卡在哪"。
# 所以这里把采集板当成一个可配置对象：IP、端口、板卡型号都在 app 里可改，
# 探测结果（TCP 通不通）也回给前端，而不是让人去终端看日志。
DEVICE_CFG = {
    "ip": os.getenv("EEG_IP", "192.168.4.1"),
    # ⚠️ 这是本机（电脑）的监听端口，不是板子的端口。板子的网页在下面 PROBE_PORT 上。
    "port": int(os.getenv("EEG_IP_PORT", "6677")),
    "board_id": int(os.getenv("EEG_BOARD_ID", "6")),
}
BOARD_CHOICES = {
    6: "16 通道 · Cyton+Daisy / WiFi",
    5: "8 通道 · Cyton / WiFi",
    0: "蓝牙或 USB 串口",
}
# 探活探的是板子自己的网页端口（能打开板子页面 = 板子在、也在同一网里）。
# 绝不能拿 DEVICE_CFG["port"] 去探板子 —— 那是本机在等的端口，板子上根本没开。
PROBE_PORT = int(os.getenv("EEG_PROBE_PORT", "80"))
_probe_cache: tuple[float, str, dict] = (0.0, "", {})


def _probe(ip: str, timeout: float = 0.8, max_age: float = 5.0) -> dict:
    """TCP 探一下板子在不在。只测端口，不读数据（读数据会跟 eeg 服务抢连接）。"""
    global _probe_cache
    now = time.monotonic()
    at, cip, res = _probe_cache
    if cip == ip and now - at < max_age:
        return res
    t0 = time.monotonic()
    try:
        with socket.create_connection((ip, PROBE_PORT), timeout=timeout):
            res = {"reachable": True, "ms": round((time.monotonic() - t0) * 1000, 1)}
    except Exception as e:  # noqa: BLE001
        res = {"reachable": False, "error": type(e).__name__}
    _probe_cache = (now, ip, res)
    return res


def _eeg_running() -> bool:
    p = _children.get("eeg")
    return bool(p is not None and p.poll() is None)


def device_snapshot() -> dict:
    board = (latest_state or {}).get("board_id")
    if eeg_clients:
        # 以启动时用的模式为准：演示进程即使还没吐第一包，也不能显示成「真机」
        mode = "demo" if (EEG_MODE["v"] == "demo" or board == -1) else "real"
    elif _eeg_running():
        mode = "starting"
    else:
        mode = "idle"
    ip, port = DEVICE_CFG["ip"], DEVICE_CFG["port"]
    return {
        "ok": True,
        "last_error": "" if (mode == "demo" or eeg_clients) else DEVICE_CFG.get("last_error", ""),
        "mode": mode,                      # real / demo / starting / idle
        "connected": bool(eeg_clients),
        "board_id": board,                    # 没收到过数据就是 None，别拿配置冒充状态
        "config": dict(DEVICE_CFG),
        "boards": {str(k): v for k, v in BOARD_CHOICES.items()},
        "probe": _probe(ip),
        "board_name": BOARD_CHOICES.get(DEVICE_CFG["board_id"], "未知板卡"),
        "hint": ("先连 WiFi「ExpandableEEGAP」（密码 abcd1234），再点连接。"
                 f"IP 默认 192.168.4.1，端口 {port}（本机监听口，不用改）。"),
    }


def _eeg_last_error() -> str:
    """从 eeg 日志尾部摘一行原因，给控制台显示。"""
    log = LOGDIR / "eeg.log"
    try:
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()[-40:]
    except OSError:
        return "日志里没有记录"
    skip = ("exception ignored", "traceback", "ctypes.", "during handling",
            "the above exception")
    for line in reversed(lines):
        low = line.lower()
        if any(s in low for s in skip):
            continue
        if "brainflowerror" in low or "general_error" in low or "unable to" in low:
            return line.strip()[:160]
    for line in reversed(lines):
        low = line.lower()
        if any(s in low for s in skip):
            continue
        if "error" in low or "failed" in low or "无法" in line:
            return line.strip()[:160]
    return "进程退出了，原因见 .data/logs/eeg.log"


# ── 随访量表（PHQ-2 / PHQ-9 / GAD-7） ───────────────────────
# 隐私边界：这个文件只存「受试编号 + 作答 + 得分 + 分层 + 当时的脑电趋势」。
# 不存姓名、电话、身份证、住院号 —— 编号由操作者自己编（默认 anon）。
# 放 sessions/ 下和训练记录在一起，医院带走数据时只需拷这一个目录。
SCALE_LOG = SESSION_DIR / "scales.jsonl"

# 只需要一个编号字符集，防止有人把姓名填进来还能被搜到
_PID_OK = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")


def _clean_pid(raw: str) -> str:
    pid = "".join(ch for ch in (raw or "") if ch in _PID_OK)[:16]
    return pid or "anon"


def _parse_scores(raw: str, n: int) -> list[int] | None:
    """把 "0,1,2" 解析成长度 n 的 0~3 列表。

    空字符串 → []（表示这次没做这份量表）；格式不对 → None（拒绝，不猜）。
    """
    raw = (raw or "").strip()
    if not raw:
        return []
    parts = [x.strip() for x in raw.split(",") if x.strip() != ""]
    if len(parts) != n:
        return None
    out = []
    for x in parts:
        try:
            v = int(x)
        except ValueError:
            return None
        if v < 0 or v > 3:
            return None
        out.append(v)
    return out


def _read_scales(limit: int = 20) -> list[dict]:
    p = SCALE_LOG
    if not p.exists():
        return []
    rows: list[dict] = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(r, dict):
            rows.append(r)
    return rows[-limit:]


def _current_risk() -> dict:
    """当前脑电趋势里的分层（拿不到就返回 unknown，不编）。"""
    try:
        from analysis.trend import trend as _trend
        snap = _trend() or {}
        risk = snap.get("risk") or {}
        return {"level": risk.get("level"), "title": risk.get("title"),
                "reasons": risk.get("reasons") or [],
                "sessions": len(snap.get("sessions") or [])}
    except Exception:  # noqa: BLE001
        return {"level": None, "title": "趋势不可用", "reasons": [], "sessions": 0}


def scales_snapshot() -> dict:
    """题库 + 最近一次随访 + 历史（给前端渲染用）。"""
    hist = _read_scales(20)
    return {
        "ok": True,
        "questionnaire": scale_form(),
        "latest": hist[-1] if hist else None,
        "history": [{k: r.get(k) for k in ("t", "pid", "scores", "level", "label")}
                    for r in hist],
        "count": len(hist),
        "file": SCALE_LOG.name,
        "privacy": "只存编号与得分，不含姓名等身份信息",
    }


def risk_snapshot() -> dict:
    """最近一次随访 + 当前脑电趋势 → 合成分层（只读，不写文件）。"""
    hist = _read_scales(1)
    latest = hist[-1] if hist else None
    eeg = _current_risk()
    combined = None
    if latest:
        ans = latest.get("answers") or {}
        p2 = score_phq2(ans["phq2"]) if ans.get("phq2") else None
        p9 = score_phq9(ans["phq9"]) if ans.get("phq9") else None
        g7 = score_gad7(ans["gad7"]) if ans.get("gad7") else None
        combined = triage(phq2=p2, phq9=p9, gad7=g7,
                          eeg_level=eeg.get("level"), eeg_reasons=eeg.get("reasons"))
    return {"ok": True, "eeg": eeg, "latest_scale": latest, "combined": combined}


def scales_submit(params: dict) -> dict:
    """记一次随访：问卷作答 → 计分 → 分层 → 追加一行 JSONL。"""
    a2 = _parse_scores(params.get("phq2", ""), 2)
    a9 = _parse_scores(params.get("phq9", ""), 9)
    a7 = _parse_scores(params.get("gad7", ""), 7)
    if a2 is None or a9 is None or a7 is None:
        return {"ok": False,
                "error": "分值格式不对：用逗号分隔的 0~3 整数，"
                         "长度分别是 PHQ-2=2、PHQ-9=9、GAD-7=7"}
    if not (a2 or a9 or a7):
        return {"ok": False, "error": "至少要做一份量表（phq2 / phq9 / gad7）"}

    r2 = score_phq2(a2) if a2 else None
    r9 = score_phq9(a9) if a9 else None
    r7 = score_gad7(a7) if a7 else None
    eeg = _current_risk()
    tri = triage(phq2=r2, phq9=r9, gad7=r7,
                 eeg_level=eeg.get("level"), eeg_reasons=eeg.get("reasons"))

    row = {
        "t": round(time.time(), 3),
        "pid": _clean_pid(params.get("pid", "")),
        "answers": {"phq2": a2 or None, "phq9": a9 or None, "gad7": a7 or None},
        "scores": {"phq2": r2["total"] if r2 else None,
                   "phq9": r9["total"] if r9 else None,
                   "gad7": r7["total"] if r7 else None,
                   "phq9_item9": bool(r9 and r9.get("item9_positive"))},
        "level": tri["level"], "label": tri["label"], "action": tri["action"],
        "reasons": tri["reasons"],
        "eeg": {"level": eeg.get("level"), "sessions": eeg.get("sessions")},
        "note": params.get("note", "")[:120],
    }
    try:
        SCALE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with SCALE_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        saved = True
    except OSError as e:                     # 写不进去也要把结果给医生看
        saved = False
        row["save_error"] = f"{type(e).__name__}: {e}"

    return {"ok": True, "saved": saved, "record": row,
            "phq2": r2, "phq9": r9, "gad7": r7, "triage": tri}


EEG_MODE = {"v": None}          # 当前采集进程跑的是 real 还是 demo


def _spawn_eeg(mode: str) -> str:
    if mode == "real":
        env = {"EEG_BOARD_ID": str(DEVICE_CFG["board_id"]),
               "EEG_IP": DEVICE_CFG["ip"],
               "EEG_IP_PORT": str(DEVICE_CFG["port"]),
               "EEG_IP_PROTOCOL": "0",   # 本设备用 0（不用 IP 协议族）；2=TCP 是错的
               "DEMO_MODE": "0"}
    else:
        # 只有用户主动点「演示」才走到这里，并且显式允许输出（带 sim 标记）
        env = {"EEG_BOARD_ID": "-1", "DEMO_MODE": "1", "EEG_DEMO_OUTPUT": "1"}
    st = _spawn_child("eeg", [PYBIN, "-u", "eeg_service/main.py"], env)
    if st == "started":
        EEG_MODE["v"] = mode
        return f"started({mode})"
    return st


def _start_eeg() -> str:
    """只连真机。探不到板子就什么都别启动。

    用户定的规矩（2026-10-24）：连上真脑电之前，界面上不允许出现任何数字，
    所以没有板子时既不跑采集、也不产出合成数据，宁可空着。
    """
    if _eeg_running() or eeg_clients:
        return "already-connected"
    if _probe(DEVICE_CFG["ip"])["reachable"]:
        return _spawn_eeg("real")
    return "no-board"


def demo_toggle(on: bool) -> dict:
    """演示开关：只由界面上的「演示」键触发。

    关闭时立刻杀掉采集进程，界面马上回到空值；打开时用合成数据，
    每一帧都带 sim=true，前端会挂水印和横幅，不会被当成真实脑电。
    """
    if on:
        if _board_ready():
            return {"ok": False, "error": "真板正在给数据，不需要演示"}
        _kill_child("eeg")
        _kill_pattern("eeg_service/main.py")
        st = _spawn_eeg("demo")
        return {"ok": str(st).startswith("started") or st == "already-running",
                "demo": True, "steps": {"eeg": st}}
    st = _kill_child("eeg")
    EEG_MODE["v"] = None
    if st == "not-running":
        st = _kill_pattern("eeg_service/main.py")
    # 演示数据驱动的是假画面，关演示就把画面一起收掉
    unity = "not-playing"
    if _player_running():
        unity = "stopped"
        try:
            import subprocess
            subprocess.run(["pkill", "-f", "MacOS/NeuroBloom"], check=False,
                           timeout=5)
        except Exception:  # noqa: BLE001
            pass
    return {"ok": True, "demo": False, "steps": {"eeg": st, "unity": unity}}


def _board_ready() -> bool:
    """真板是不是真的在给数据（不是"进程起来了"就算）。"""
    snap = device_snapshot()
    if snap.get("mode") == "real":
        return True
    return bool(_probe(DEVICE_CFG["ip"])["reachable"])


IP_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def device_connect(payload: dict) -> dict:
    ip = str(payload.get("ip") or DEVICE_CFG["ip"]).strip()
    if not IP_RE.match(ip):
        return {"ok": False, "error": "IP 格式不对，例子：192.168.4.1"}
    try:
        port = int(payload.get("port") or DEVICE_CFG["port"])
        board_id = int(payload.get("board_id") or DEVICE_CFG["board_id"])
    except Exception:  # noqa: BLE001
        return {"ok": False, "error": "端口和板卡编号必须是数字"}
    if not (1 <= port <= 65535):
        return {"ok": False, "error": "端口要在 1–65535 之间"}
    if board_id not in BOARD_CHOICES:
        return {"ok": False, "error": "只支持 6（16 通道）、5（8 通道）、0（串口）"}

    global _probe_cache
    _probe_cache = (0.0, "", {})   # 用户刚点了连接，必须重新探，不能吃 5 秒缓存
    alive = _probe(ip)
    if not alive["reachable"]:
        return {"ok": False, "error": f"连不上板子 {ip}（探的是它网页的 {PROBE_PORT} 口）。"
                                      "先连 WiFi「ExpandableEEGAP」，或确认板子已经开机。",
                "probe": alive}

    DEVICE_CFG.update({"ip": ip, "port": port, "board_id": board_id,
                       "last_error": ""})
    _kill_child("eeg")
    if not _children.get("eeg"):
        _kill_pattern("eeg_service/main.py")
    started = _spawn_eeg("real")
    # 端口通不代表板子能握手：BrainFlow 还要过一遍协议。
    # 等 4 秒，进程死了就把日志里的原因带回去，别让人对着"未连接"猜。
    p = _children.get("eeg")
    for _ in range(12):
        if p is None or p.poll() is not None:
            break
        time.sleep(0.35)
    if p is not None and p.poll() is not None:
        err = "端口通了，但脑电板握手失败：" + _eeg_last_error()
        DEVICE_CFG["last_error"] = err
        return {"ok": False, "error": err,
                "device": device_snapshot()}
    return {"ok": True, "started": started, "device": device_snapshot()}




def _spawn_child(name: str, args: list[str], extra_env: dict | None = None) -> str:
    p = _children.get(name)
    if p is not None and p.poll() is None:
        return "already-running"
    LOGDIR.mkdir(parents=True, exist_ok=True)
    log = open(LOGDIR / f"{name}.log", "ab", buffering=0)
    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    env["PYTHONUNBUFFERED"] = "1"
    _children[name] = subprocess.Popen(
        args, cwd=str(ROOT), stdout=log, stderr=log, env=env)
    print(f"[bridge] 已启动 {name}（pid {_children[name].pid}）", flush=True)
    return "started"


def _kill_child(name: str) -> str:
    p = _children.pop(name, None)
    if p is None or p.poll() is not None:
        return "not-running"
    p.terminate()
    try:
        p.wait(timeout=6)
    except Exception:
        p.kill()
    print(f"[bridge] 已停止 {name}", flush=True)
    return "stopped"


def _kill_pattern(pattern: str) -> str:
    """按命令行特征收编"不是本桥接拉起"的同名进程。

    用户可能自己开过 eeg/agent，_children 里没有记录；但点「停止」的语义
    是"全都停"，留一个后台进程空转既费电又会让状态变得莫名其妙。
    """
    try:
        out = subprocess.run(["pgrep", "-f", pattern],
                             capture_output=True, text=True, timeout=3).stdout
    except Exception as e:  # noqa: BLE001
        return f"failed({e})"
    pids = []
    for tok in out.split():
        if tok.isdigit():
            pid = int(tok)
            if pid != os.getpid():
                pids.append(pid)
    if not pids:
        return "not-running"
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:  # noqa: BLE001
            pass
    print(f"[bridge] 已按特征停止 {pattern}：{pids}", flush=True)
    return "stopped"


def _run_osa(script: str, timeout: int = 20):
    return subprocess.run(["osascript", "-e", script],
                          capture_output=True, text=True, timeout=timeout)


async def _toggle_unity() -> str:
    """点 Unity 菜单切 Play。点完把焦点还给浏览器，别把用户甩到 Unity 里。"""
    loop = asyncio.get_running_loop()
    try:
        r = await loop.run_in_executor(None, _run_osa, UNITY_TOGGLE)
        if r.returncode != 0:
            return "failed:" + (r.stderr or "").strip().splitlines()[-1][:100]
        try:
            has_safari = await loop.run_in_executor(None, _run_osa, BACK_TO_BROWSER)
            if (has_safari.stdout or "").strip() == "true":
                await loop.run_in_executor(
                    None, _run_osa, 'tell application "Safari" to activate')
        except Exception:
            pass
        return "toggled"
    except Exception as e:
        return f"failed:{type(e).__name__}"


def _unity_busy() -> bool:
    return bool(video_pubs) or (time.monotonic() - latest_frame_at) < FRESH_FRAME_S


_unity_alive_cache: tuple[float, bool] = (0.0, False)


def _unity_running(max_age: float = 3.0) -> bool:
    """Unity 到底开没开。

    这个检查是必须的：Unity 没开的时候如果还去点它的菜单，
    System Events 会一直等到 20 秒超时才报错，于是「开始演示」
    这个请求要挂四十秒才返回，控制台按钮一直转圈。
    """
    global _unity_alive_cache
    now = time.monotonic()
    if now - _unity_alive_cache[0] < max_age:
        return _unity_alive_cache[1]
    alive = False
    try:
        r = _run_osa(
            'tell application "System Events" to (name of processes) contains "Unity"',
            timeout=4)
        alive = (r.stdout or "").strip().lower() == "true"
    except Exception:
        alive = False
    _unity_alive_cache = (now, alive)
    return alive


async def control(action: str) -> dict:
    steps: dict[str, str] = {}
    if action == "restart":
        # 只重起画面，不动脑电和智能体：画面卡住时没必要把采集也断了
        if PLAYER_APP.exists():
            steps["unity"] = await asyncio.get_running_loop().run_in_executor(
                None, _restart_player)
        elif _unity_running():
            steps["unity"] = await _toggle_unity()
        else:
            steps["unity"] = "no-build"
        ok = not any(str(v).startswith("failed") for v in steps.values())
        return {"ok": ok, "action": action, "steps": steps}

    if action == "start":
        steps["eeg"] = _start_eeg()
        if not _board_ready() and device_snapshot().get("mode") != "demo":
            # 没有真板：不启动智能体、不启动画面（画面靠脑电驱动，没有脑电就是假的）
            steps["agent"] = "skipped(no-board)"
            if _player_running():
                steps["unity"] = await asyncio.get_running_loop().run_in_executor(
                    None, _quit_player)
            else:
                steps["unity"] = "skipped(no-board)"
            return {"ok": False, "action": action, "steps": steps,
                    "blocked": "no-board",
                    "reason": "探测不到采集板（192.168.4.1 不可达）。没有真脑电就不启动画面，"
                              "也不产生任何数据。"}
        agent_online = any(m.get("role") == "agent" for m in world_meta.values())
        if agent_online:
            steps["agent"] = "already-connected"
        else:
            steps["agent"] = _spawn_child(
                "agent", [PYBIN, "-u", "agent_service/agent.py"])
        if _unity_busy():
            steps["unity"] = "already-playing"
        elif PLAYER_APP.exists():
            steps["unity"] = await asyncio.get_running_loop().run_in_executor(
                None, _launch_player)
        elif not _unity_running():
            steps["unity"] = "unity-not-running"
        else:
            steps["unity"] = await _toggle_unity()
    else:
        steps["eeg"] = _kill_child("eeg")
        EEG_MODE["v"] = None
        if steps["eeg"] == "not-running":
            steps["eeg"] = _kill_pattern("eeg_service/main.py")
        steps["agent"] = _kill_child("agent")
        if steps["agent"] == "not-running":
            steps["agent"] = _kill_pattern("agent_service/agent.py")
        if _player_running():
            steps["unity"] = await asyncio.get_running_loop().run_in_executor(
                None, _quit_player)
        else:
            steps["unity"] = await _toggle_unity() if _unity_busy() and _unity_running() else "not-playing"
    ok = not any(str(v).startswith("failed") for v in steps.values())
    return {"ok": ok, "action": action, "steps": steps}


async def process_request(connection, request):
    """/status：握手前当成普通 HTTP 回掉，不走 WebSocket。

    CORS 必须放开：控制台是 8899 端口，这个在 8765，属跨源。
    只读、不含密钥，放开无风险。
    """
    path = request.path.split("?")[0].rstrip("/") or "/"

    # 演示开关：只认本机调用，别让局域网里任何一台机器都能启动你的 Unity
    if path == "/control/demo":
        qs = request.path.split("?", 1)[1] if "?" in request.path else ""
        on = not any(qs.startswith(k) or ("&" + k) in qs
                     for k in ("on=0", "on=false", "on=off"))
        body = json.dumps({"ok": True, "action": "demo", **demo_toggle(on)},
                          ensure_ascii=False).encode("utf-8")
        return Response(200, "OK", Headers([
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(body))),
            ("Access-Control-Allow-Origin", "*"),
            ("Cache-Control", "no-store"),
        ]), body)

    if path in ("/control/start", "/control/stop", "/control/restart"):
        peer = ""
        try:
            peer = (connection.remote_address or ("", 0))[0]
        except Exception:
            peer = ""
        if peer not in ("127.0.0.1", "::1", "localhost"):
            body = json.dumps({"ok": False, "error": "只允许本机调用"},
                              ensure_ascii=False).encode("utf-8")
            return Response(403, "Forbidden", Headers([
                ("Content-Type", "application/json; charset=utf-8"),
                ("Content-Length", str(len(body))),
                ("Access-Control-Allow-Origin", "*"),
            ]), body)
        action = path.rsplit("/", 1)[-1]          # start / stop / restart
        result = await control(action)
        body = json.dumps(result, ensure_ascii=False).encode("utf-8")
        return Response(200, "OK", Headers([
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(body))),
            ("Access-Control-Allow-Origin", "*"),
            ("Cache-Control", "no-store"),
        ]), body)

    if path in ("/report", "/report/"):
        f = latest_report()
        if f is None:
            body = "还没有训练记录。先跑一次训练（脑电服务开始推数），结束后自动生成报告。".encode("utf-8")
            return Response(404, "Not Found", Headers([
                ("Content-Type", "text/plain; charset=utf-8"),
                ("Content-Length", str(len(body))),
                ("Access-Control-Allow-Origin", "*"),
                ("Cache-Control", "no-store"),
            ]), body)
        body = f.read_bytes()
        download = "download=1" in request.path
        headers = [
            ("Content-Type", "text/html; charset=utf-8"),
            ("Content-Length", str(len(body))),
            ("Access-Control-Allow-Origin", "*"),
            ("Cache-Control", "no-store"),
        ]
        if download:
            headers.append(("Content-Disposition", f'attachment; filename="{f.name}"'))
        return Response(200, "OK", Headers(headers), body)

    # 设备：GET 看状态，POST 连接（只认本机）
    if path in ("/device", "/device/"):
        if "refresh=1" in request.path:
            # 缓存是三元的 (探测时刻, ip, 结果)，别写回旧的四元格式 ——
            # 写错了会让下一次 _probe() 解包直接抛 ValueError，/device 变成 500。
            globals()["_probe_cache"] = (0.0, "", {})
        snap = await asyncio.get_running_loop().run_in_executor(None, device_snapshot)
        body = json.dumps(snap, ensure_ascii=False).encode("utf-8")
        return Response(200, "OK", Headers([
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(body))),
            ("Access-Control-Allow-Origin", "*"),
            ("Cache-Control", "no-store"),
        ]), body)

    if path in ("/device/connect", "/device/connect/"):
        peer = ""
        try:
            peer = (connection.remote_address or ("", 0))[0]
        except Exception:  # noqa: BLE001
            peer = ""
        if peer not in ("127.0.0.1", "::1", "localhost"):
            body = json.dumps({"ok": False, "error": "只允许本机调用"},
                              ensure_ascii=False).encode("utf-8")
            return Response(403, "Forbidden", Headers([
                ("Content-Type", "application/json; charset=utf-8"),
                ("Content-Length", str(len(body))),
                ("Access-Control-Allow-Origin", "*"),
            ]), body)
        # websockets 的 Request 只暴露 path/headers，不读 body，
        # 所以参数走 query string（前端也是这么发的）。
        qs = parse_qs(urlparse(request.path).query)
        payload = {k: v[0] for k, v in qs.items() if v}
        result = await asyncio.get_running_loop().run_in_executor(
            None, device_connect, payload)
        body = json.dumps(result, ensure_ascii=False).encode("utf-8")
        return Response(200, "OK", Headers([
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(body))),
            ("Access-Control-Allow-Origin", "*"),
            ("Cache-Control", "no-store"),
        ]), body)

    if path in ("/garden/plant", "/garden/plant/"):
        # 控制台上点一下画面 → 让 Unity 在那个位置种一株花。
        # 为什么要在网页这一侧点：Unity 窗口经常在后台或被别的窗口挡住，
        # 用户看到的、想点的其实是网页里的画面。
        # 和 /device/connect 同理：websockets 的 Request 不读 body，参数走 query。
        qs = parse_qs(urlparse(request.path).query)
        try:
            u = float((qs.get("u") or ["-1"])[0])
            v = float((qs.get("v") or ["-1"])[0])
        except (TypeError, ValueError):
            u = v = -1.0

        def _plant_reply(ok: bool, err: str = "") -> Response:
            body = json.dumps({"ok": ok, "error": err or None},
                              ensure_ascii=False).encode("utf-8")
            return Response(200 if ok else 400, "OK" if ok else "Bad Request", Headers([
                ("Content-Type", "application/json; charset=utf-8"),
                ("Content-Length", str(len(body))),
                ("Access-Control-Allow-Origin", "*"),
                ("Cache-Control", "no-store"),
            ]), body)

        if not (0.0 <= u <= 1.0 and 0.0 <= v <= 1.0):
            return _plant_reply(False, "u/v 必须是 0~1 之间")

        # 只发给 Unity（ClientWebSocket 不带 UA，role 就是 unity）
        targets = {c for c in list(world_clients)
                   if (world_meta.get(c) or {}).get("role") == "unity"}
        if not targets:
            return _plant_reply(False, "Unity 还没连上桥接")
        await broadcast(targets, {"type": "plant", "u": u, "v": v})
        print(f"[bridge] 控制台播种 u={u:.3f} v={v:.3f} → {len(targets)} 个 Unity 端", flush=True)
        return _plant_reply(True)

    if path in ("/tts", "/tts/"):
        from tools.tts import synth
        qs = parse_qs(urlparse(request.path).query)
        text = (qs.get("text") or [""])[0]
        wav = await asyncio.get_running_loop().run_in_executor(None, synth, text)
        if wav is None:
            body = b"voice-unavailable"
            return Response(503, "Unavailable", Headers([
                ("Content-Type", "text/plain; charset=utf-8"),
                ("Content-Length", str(len(body))),
                ("Access-Control-Allow-Origin", "*"),
            ]), body)
        data = wav.read_bytes()
        return Response(200, "OK", Headers([
            ("Content-Type", "audio/wav"),
            ("Content-Length", str(len(data))),
            ("Access-Control-Allow-Origin", "*"),
            ("Cache-Control", "max-age=600"),
        ]), data)

    if path in ("/trend", "/trend/"):
        from analysis.trend import trend as _trend
        snap = await asyncio.get_running_loop().run_in_executor(None, _trend)
        body = json.dumps(snap, ensure_ascii=False).encode("utf-8")
        return Response(200, "OK", Headers([
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(body))),
            ("Access-Control-Allow-Origin", "*"),
            ("Cache-Control", "no-store"),
        ]), body)

    if path in ("/scales", "/scales/"):
        snap = await asyncio.get_running_loop().run_in_executor(None, scales_snapshot)
        body = json.dumps(snap, ensure_ascii=False).encode("utf-8")
        return Response(200, "OK", Headers([
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(body))),
            ("Access-Control-Allow-Origin", "*"),
            ("Cache-Control", "no-store"),
        ]), body)

    if path in ("/scales/submit", "/scales/submit/"):
        # 和 /device/connect 同理：websockets 的 Request 不读 body，参数走 query。
        # 例：/scales/submit?pid=S01&phq2=1,0&phq9=1,0,1,1,0,0,0,0,0&gad7=1,1,0,0,0,0,0
        qs = parse_qs(urlparse(request.path).query)
        payload = {k: v[0] for k, v in qs.items() if v}
        result = await asyncio.get_running_loop().run_in_executor(
            None, scales_submit, payload)
        body = json.dumps(result, ensure_ascii=False).encode("utf-8")
        return Response(200 if result.get("ok") else 400,
                        "OK" if result.get("ok") else "Bad Request", Headers([
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(body))),
            ("Access-Control-Allow-Origin", "*"),
            ("Cache-Control", "no-store"),
        ]), body)

    if path in ("/risk", "/risk/"):
        snap = await asyncio.get_running_loop().run_in_executor(None, risk_snapshot)
        body = json.dumps(snap, ensure_ascii=False).encode("utf-8")
        return Response(200, "OK", Headers([
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(body))),
            ("Access-Control-Allow-Origin", "*"),
            ("Cache-Control", "no-store"),
        ]), body)

    if path in ("/status", "/status/"):
        body = json.dumps(status_snapshot(), ensure_ascii=False).encode("utf-8")
        return Response(200, "OK", Headers([
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(body))),
            ("Access-Control-Allow-Origin", "*"),
            ("Cache-Control", "no-store"),
        ]), body)
    return None


async def broadcast(clients: set, message: dict) -> None:
    """给所有订阅者发同一条消息。

    必须并发发、每条设超时。早期的写法是「串行 for + await c.send」，
    那样只要有一个客户端卡住（断线没断干净 / Unity 编辑器暂停 / 网络半开），
    整条广播就卡在它身上：后面的客户端全部收不到，eeg_handler 也被拖住
    不再收脑电消息，最后 EEG 端因为 ping 没人回而误判断线、整条链路静默死掉。
    这个坑已经真实踩到过一次，所以在这里堵住。
    """
    if not clients:
        return
    text = json.dumps(message, ensure_ascii=False)
    targets = list(clients)          # 用副本遍历，避免"集合在迭代中被修改"
    results = await asyncio.gather(
        *(asyncio.wait_for(c.send(text), timeout=SEND_TIMEOUT) for c in targets),
        return_exceptions=True,
    )
    for c, r in zip(targets, results):
        if isinstance(r, BaseException):
            clients.discard(c)
            print("[bridge] 丢弃一个无响应的客户端", flush=True)


async def eeg_handler(ws) -> None:
    eeg_clients.add(ws)
    print("[bridge] EEG 客户端接入", flush=True)
    try:
        async for raw in ws:
            global latest_state, latest_state_at
            try:
                latest_state = json.loads(raw)
                latest_state_at = time.monotonic()
            except Exception:
                continue
            stats["eeg_msgs"] += 1
            stats["eeg_last_at"] = time.monotonic()
            await broadcast(world_clients, {"type": "state", **latest_state,
                                            "_clients": len(world_clients)})
            try:
                snap = dict(latest_state)
                text = (latest_guidance or {}).get("script")
                if text and "guidance" not in snap:
                    snap["guidance"] = text
                session.record(snap)
            except Exception as exc:
                print(f"[session] 记录失败（已忽略）：{exc!r}", flush=True)
    except ConnectionClosed:
        # 客户端没打招呼就断了（Unity 退出 Play / 编辑器重编译，都会这样）。
        # 这属于正常现象，绝不能让它冒到顶层：真实踩过一次，
        # 整个桥接进程被这一行异常带走，三个服务连带全挂。
        print("[bridge] EEG 客户端异常断开（已忽略）", flush=True)
    except Exception as e:
        print(f"[bridge] EEG 通道异常（已忽略）：{type(e).__name__}: {e}", flush=True)
    finally:
        eeg_clients.discard(ws)
        print("[bridge] EEG 客户端断开", flush=True)
        try:
            session.finish()
        except Exception as exc:
            print(f"[session] 收尾失败（已忽略）：{exc!r}", flush=True)


async def world_handler(ws) -> None:
    global latest_guidance, latest_guidance_at, latest_strategy_at
    world_clients.add(ws)

    req = getattr(ws, "request", None)
    ua = ""
    try:
        ua = req.headers.get("User-Agent", "") if req is not None else ""
    except Exception:
        ua = ""
    world_meta[ws] = {"at": time.monotonic(), "ua": ua,
                      "role": _role_from_ua(ua), "msgs": 0}

    print("[bridge] World(Unity/Agent) 客户端接入", flush=True)
    # 后接入的客户端要先知道「现在是什么样」，否则得等下一个 EEG 窗口才有画面。
    # 但只有拿到过**真实状态**才回放 —— 没拿到就什么都不发，
    # 让 Unity 保持自己的初始状态，比让它先黑一下强。
    # 只回放「还热着」的状态（5 秒内）。训练停了半小时再打开控制台，回放旧状态会让
    # 页面显示「进行中 · 合成板」，用户以为设备还连着 —— 之前那个假状态的老毛病。
    if latest_state is not None and time.monotonic() - latest_state_at < 5.0:
        await ws.send(json.dumps({"type": "state", **latest_state,
                                  "_clients": len(world_clients)},
                                 ensure_ascii=False))
    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            stats["world_msgs"] += 1
            stats["world_last_at"] = time.monotonic()
            meta = world_meta.get(ws)
            if meta is not None:
                meta["msgs"] = meta.get("msgs", 0) + 1
                if isinstance(msg, dict) and msg.get("role"):
                    meta["role"] = str(msg["role"])[:20]   # 自己报的家门最可信
            # 智能体说的话单独留一份，控制台点「智能体」时不用等下一句
            if isinstance(msg, dict) and msg.get("type") == "guidance":
                latest_guidance = msg
                latest_guidance_at = time.monotonic()
                if meta is not None:
                    meta["role"] = "agent"
            if isinstance(msg, dict) and msg.get("type") == "state":
                latest_strategy_at = time.monotonic()
            # Agent 服务向 world 通道注入引导语，转发给其他客户端
            await broadcast(world_clients, msg)
    finally:
        world_clients.discard(ws)
        world_meta.pop(ws, None)
        print("[bridge] World 客户端断开", flush=True)


async def _push_frame(ws, frame: bytes) -> None:
    """给一个观看端发一帧。发不动就把它踢掉，不拖累别人。"""
    try:
        await asyncio.wait_for(ws.send(frame), timeout=0.6)
    except Exception:
        video_subs.discard(ws)
    finally:
        video_busy.discard(ws)


async def _video_heartbeat(ws) -> None:
    """每 4 秒给 Unity 推一个文本心跳。

    为什么必须有：桥接被 kill/重启后，Unity 那条 socket 会变成"半死"——
    ReceiveAsync 不返回、SendAsync 却照样成功，从发送侧完全看不出断了。
    Unity 那边靠"15 秒收不到心跳就重连"来判断，所以这里必须真的发。
    """
    try:
        while True:
            await asyncio.sleep(4)
            await ws.send("hb")
    except Exception:
        return


async def video_pub_handler(ws) -> None:
    """Unity 侧：只收二进制帧，收到就转发。"""
    global latest_frame, latest_frame_at, last_frame_bytes
    video_pubs.add(ws)
    print("[bridge] 视频推流端接入（Unity）", flush=True)
    hb = asyncio.create_task(_video_heartbeat(ws))
    try:
        async for frame in ws:
            if not isinstance(frame, (bytes, bytearray)):
                continue          # 文本消息（比如心跳）直接忽略
            latest_frame = frame
            latest_frame_at = time.monotonic()
            last_frame_bytes = len(frame)
            for c in list(video_subs):
                # 上一帧还没发完就跳过这一帧：宁可掉帧，也不能让延迟越积越多
                if c in video_busy:
                    continue
                video_busy.add(c)
                asyncio.create_task(_push_frame(c, frame))
    except ConnectionClosed:
        print("[bridge] 视频推流端断开（Unity 停了或退出 Play）", flush=True)
    except Exception as e:
        print(f"[bridge] 视频通道异常（已忽略）：{type(e).__name__}: {e}", flush=True)
    finally:
        hb.cancel()
        video_pubs.discard(ws)


async def video_sub_handler(ws) -> None:
    """控制台侧：一接入就把最新一帧给它，免得先黑屏等下一帧。
    只给 3 秒内的新帧 —— 停播很久还塞一张旧图，会让人以为画面是活的。"""
    video_subs.add(ws)
    print("[bridge] 视频观看端接入（控制台）", flush=True)
    try:
        if latest_frame is not None and time.monotonic() - latest_frame_at < FRESH_FRAME_S:
            await asyncio.wait_for(ws.send(latest_frame), timeout=1.0)
        async for _ in ws:
            pass                  # 观看端不发东西，收到什么都不管
    except ConnectionClosed:
        pass
    except Exception as e:
        print(f"[bridge] 视频观看端异常（已忽略）：{type(e).__name__}: {e}", flush=True)
    finally:
        video_subs.discard(ws)
        video_busy.discard(ws)
        print("[bridge] 视频观看端断开", flush=True)


async def wave_pub_handler(ws) -> None:
    """EEG 侧：把 25Hz 的波形帧转发给所有打开着的控制台。

    波形是高频小包（每帧约 1KB），所以不缓存、不回放：慢了就丢，
    画面要的是「现在」，不是「刚才每一条都到齐」。但也不能因为一个
    卡住的页面把发布端堵死——所以并发发、每条设超时、失败就摘掉。
    """
    print("[bridge] 波形发布端接入（EEG）", flush=True)
    try:
        async for msg in ws:
            if not wave_subs:
                continue
            await asyncio.gather(
                *(asyncio.wait_for(c.send(msg), timeout=1.0) for c in list(wave_subs)),
                return_exceptions=True)
    except ConnectionClosed:
        pass
    except Exception as e:
        print(f"[bridge] 波形发布端异常（已忽略）：{type(e).__name__}: {e}", flush=True)
    finally:
        print("[bridge] 波形发布端断开", flush=True)


async def wave_sub_handler(ws) -> None:
    """控制台侧：只收不发。"""
    wave_subs.add(ws)
    print("[bridge] 波形观看端接入（控制台）", flush=True)
    try:
        async for _ in ws:
            pass
    except ConnectionClosed:
        pass
    except Exception as e:
        print(f"[bridge] 波形观看端异常（已忽略）：{type(e).__name__}: {e}", flush=True)
    finally:
        wave_subs.discard(ws)
        print("[bridge] 波形观看端断开", flush=True)


async def handler(ws) -> None:
    # 兜底：任何一条连接断掉/报错都不许把整个桥接带走。
    # 踩过的坑：客户端突然断开（Unity 退出 Play、拔线、进程被杀）时，
    # `async for` 会抛 ConnectionClosedError，之前没接住 → 桥接进程整个死掉，
    # EEG、Agent、Unity 三个一起静默掉线。
    try:
        path = getattr(ws, "request", None)
        path = path.path if path is not None else ""
        if path.startswith("/ws/wave/pub"):
            await wave_pub_handler(ws)
        elif path.startswith("/ws/wave/sub"):
            await wave_sub_handler(ws)
        elif path.startswith("/ws/video/pub"):
            await video_pub_handler(ws)
        elif path.startswith("/ws/video/sub"):
            await video_sub_handler(ws)
        elif path.startswith("/ws/eeg"):
            await eeg_handler(ws)
        elif path.startswith("/ws/world"):
            await world_handler(ws)
        else:
            await ws.close(code=1008, reason="unknown path")
    except Exception as exc:
        print(f"[bridge] 一条连接异常退出（已忽略，服务继续）：{exc!r}", flush=True)


def _shutdown(signum, frame):
    """被 kill 的时候也要把录像落盘，否则最后一段训练没有报告。"""
    try:
        session.finish()
    except Exception as exc:
        print(f"[session] 收尾失败（已忽略）：{exc!r}", flush=True)
    raise SystemExit(0)


async def main() -> None:
    for _sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(_sig, _shutdown)
        except Exception:
            pass
    # 板子插着、WiFi 也连着的话，开机直接就采，不用等用户点任何东西。
    # 探不到就保持安静（合成板要等用户点「开始演示」才跑，免得空转）。
    async def _autoconnect() -> None:
        if os.getenv("DEVICE_AUTOCONNECT", "1") == "0":
            return
        await asyncio.sleep(0.5)
        try:
            if not _probe(DEVICE_CFG["ip"], timeout=1.0)["reachable"]:
                print("[bridge] 没探测到采集板，先不启动脑电服务", flush=True)
                return
            print(f"[bridge] 探测到采集板 {DEVICE_CFG['ip']}（网页 {PROBE_PORT} 口通），"
                  f"自动连接", flush=True)
            _spawn_eeg("real")
        except Exception as e:  # noqa: BLE001
            print(f"[bridge] 自动连接失败（已忽略）：{e!r}", flush=True)

    asyncio.create_task(_autoconnect())

    # ping_interval=None：关闭保活探测，避免 Unity 编辑器暂停时被误判断线
    # 外层 while：万一 serve 本身出错也自动重启，不让演示中途没声音。
    while True:
        try:
            async with serve(handler, HOST, PORT, ping_interval=None,
                             process_request=process_request):
                print(f"[bridge] 监听 ws://{HOST}:{PORT}  (/ws/eeg, /ws/world, /ws/video)"
                      f"  状态接口 http://127.0.0.1:{PORT}/status", flush=True)
                await asyncio.Future()
        except Exception as exc:
            print(f"[bridge] 服务异常，2 秒后自动重启：{exc!r}", flush=True)
            await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(main())
