"""Agent 服务：订阅脑波状态，调用书生大模型生成引导语与世界参数，
推送到 bridge 的 world 通道，并写入长周期记忆。

对应方案："Hermes 式学习循环" —— 感知 -> 决策 -> 执行 -> 记录 -> 个性化。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from prompts import SYSTEM_PROMPT, build_user_context

load_dotenv()

BRIDGE_WS = os.getenv("BRIDGE_WS", "ws://127.0.0.1:8765/ws/world")
# 默认锚定「仓库根/.data/memory.jsonl」（跟着脚本走，不跟着"当前工作目录"跑）——
# 否则从仓库根启动和从 agent_service/ 启动会写到两个不同的文件，表现就是"记忆时有时无"。
# 注意要指回已有的那份历史（仓库根 .data/ 下已经有 715KB 的旧记录），别另起炉灶。
MEMORY_FILE = Path(os.getenv("MEMORY_FILE") or
                    (Path(__file__).resolve().parent.parent / ".data" / "memory.jsonl"))

client = OpenAI(
    api_key=os.getenv("SHUSHENG_API_KEY"),
    base_url=os.getenv("SHUSHENG_BASE_URL"),
    timeout=float(os.getenv("LLM_TIMEOUT", "60")),  # intern-s1-pro 较慢，放宽超时
    max_retries=0,                                   # 不重试，避免长时间卡住
)

# 节流：状态不变时不必每次都调大模型（每秒调一次既慢又费额度）
MIN_INTERVAL = float(os.getenv("LLM_MIN_INTERVAL", "8"))
MODEL = os.getenv("SHUSHENG_MODEL", "intern-s2")


def retrieve_knowledge(state: dict) -> list[str]:
    """最简知识检索：按状态给固定康复要点。生产环境替换为 RAG/MCP。"""
    kb = {
        "tense": ["紧张时可以带他做缓慢呼吸：吸气四拍、屏息七拍、呼气八拍。"],
        "neutral": ["过渡状态适合把注意力放回呼吸，不必刻意用力。"],
        "relaxed": ["放松态适合巩固训练，可延长沉浸时间以强化情绪调节。"],
    }
    return kb.get(state.get("label", ""), [])


def remember(record: dict) -> None:
    """写入长周期记忆（患者档案/趋势）。"""
    MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with MEMORY_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ------------------------------------------------------------------ 兜底话术
# 大模型不可用时的话术库。措辞已逐条对过四条红线（无诊断词/无读数/无承诺/无处方口吻）。
FALLBACK = {
    # 每个状态给 3 句 —— 同一句连说三遍，听的人立刻就出戏了。
    # 兜底也要轮着说（见 pick_fallback）。
    "relaxed": [
        "很好，你现在很放松，看这些花都为你开了。",
        "就这样待着，什么都不用做，已经很好了。",
        "感觉到了吗？花在你身边慢慢打开了。",
    ],
    "neutral": [
        "就这样，跟着呼吸慢慢来。",
        "我在你旁边，慢慢来就好。",
        "不用刻意做什么，听着自己的呼吸就行。",
    ],
    "tense": [
        "没关系，跟我一起深呼吸：吸气……慢慢呼气……",
        "别急，我陪你一起慢下来，先呼一口气。",
        "先不想别的，跟着我：慢慢地把气吐出去。",
    ],
    "calibrating": [
        "先安静坐一会儿，我在记你现在的样子。",
        "坐舒服就好，我先看看你的节奏。",
        "再等一会儿，我快认识你了。",
    ],
    "invalid": [
        "信号有点不稳，别动，我等你一下。",
        "先别动，信号飘了，我等它回来。",
        "稍等一下，我重新找一下你的信号。",
    ],
}

# 上一次真正说出口的那句（用来避免连说两句一模一样的话）
_LAST_SAID = {"text": ""}

_PUNCT = re.compile(r"[\s，。、！？…～~,.!?;；:：\"'“”‘’()（）\-—·]+")


def _norm(s: str) -> str:
    """归一化：去掉标点和空白，只比较"是不是同一句话"。"""
    return _PUNCT.sub("", s or "")


def pick_fallback(label: str) -> str:
    """兜底话术也要换着说 —— 别连说两句一样的。"""
    opts = FALLBACK.get(label) or FALLBACK["neutral"]
    last = _norm(_LAST_SAID["text"])
    for x in opts:
        if _norm(x) != last:
            return x
    return opts[0]

# 红线兜底词表。prompts.py 里已经写明：**词表过滤会被绕过**，真正的防线是提示词，
# 这里只当最后一道保险（模型偶发越界 / 提示词被长上下文冲淡时拦住）。
RED_LINE = [
    r"焦虑|抑郁|确诊|障碍|异常|患者|疑似|倾向",   # 裸词也拦：文档里那个绕过样例就是"焦虑倾向"
    r"效价|唤醒度|置信度|阈值|FAA|准确率|放松度|得分|分数|α波|β波",
    r"保证|一定能|治愈|疗效|改善",
    r"建议你|必须|按疗程|坚持使用",
    r"[0-9]",          # 铁律：引导语里不出现任何数字/读数
]


def find_violation(text: str) -> str | None:
    """返回第一处触红线的字词；没有则返回 None。"""
    for pat in RED_LINE:
        m = re.search(pat, text or "")
        if m:
            return m.group(0)
    return None


# ------------------------------------------------------------ 长周期记忆（读回）
def load_profile(limit: int = 400) -> dict:
    """把 .data/memory.jsonl 读回来，推算训练阶段 / 放松趋势 / 最近说过的话。

    原来只有 remember() 在写、没有任何地方读 —— 等于记忆没生效。
    """
    if not MEMORY_FILE.exists():
        return {}
    rows = []
    try:
        with MEMORY_FILE.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        return {}
    if not rows:
        return {}
    rows = rows[-limit:]

    def relax_of(r: dict):
        v = r.get("calm")
        if v is None:
            v = (r.get("params") or {}).get("bloom")   # bloom 即放松度
        try:
            return float(v)
        except Exception:
            return None

    vals = [x for x in (relax_of(r) for r in rows) if x is not None]
    trend = "暂无记录"
    if len(vals) >= 6:
        k = max(2, len(vals) // 5)
        head, tail = sum(vals[:k]) / k, sum(vals[-k:]) / k
        d = tail - head
        if d > 0.06:
            trend = "这几次比一开始松了一些"
        elif d < -0.06:
            trend = "这几次比一开始紧一些"
        else:
            trend = "一直比较平稳"
    elif len(vals) >= 2:
        trend = "刚开始记录"

    try:
        mins = (float(rows[-1]["t"]) - float(rows[0]["t"])) / 60.0
    except Exception:
        mins = 0.0
    if mins >= 240:
        stage = f"累计陪伴约 {mins / 60:.0f} 小时（{len(rows)} 次判断记录）"
    elif mins >= 1:
        stage = f"本次已陪伴约 {mins:.0f} 分钟（{len(rows)} 次判断）"
    else:
        stage = f"本次刚开始（{len(rows)} 次判断）"

    tense = sum(1 for r in rows if r.get("state") == "tense")
    tip = f"，其中约 {round(100 * tense / len(rows))}% 的时候偏紧" if tense else ""

    # 老记录里混着带数字/诊断词的话术（过滤器是后加的），不能拿这些当"上次说过"的
    # 例子喂回去，否则等于在教模型照着违规。先过一遍筛子，最多挑出 3 句干净的。
    pool = [r.get("script") for r in rows[-6:] if r.get("script")]
    recent = [x for x in pool if not find_violation(x)][-3:]
    return {"name": "匿名", "stage": stage, "trend": trend + tip,
            "recent_scripts": recent}


def _extract_json(text: str) -> dict | None:
    """从模型返回中稳健地取出 JSON（兼容 ```json 代码块、前后多余文字）。"""
    if not text:
        return None
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t).strip()
    try:
        return json.loads(t)
    except Exception:
        pass
    start, end = t.find("{"), t.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(t[start:end + 1])
        except Exception:
            return None
    return None


def _ask(ctx: str):
    """发一轮请求，返回解析后的 JSON（失败返回 None）。"""
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "system", "content": SYSTEM_PROMPT},
                  {"role": "user", "content": ctx}],
        temperature=0.7,
        response_format={"type": "json_object"},
    )
    return _extract_json(resp.choices[0].message.content)


def _emit(script: str, cue: str, **extra) -> dict:
    """统一的出口：顺便记住这句，下一轮用来判重。"""
    if script:
        _LAST_SAID["text"] = script
    return {"script": script, "cue": cue or "relax", **extra}


def generate(state: dict, profile: dict | None = None) -> dict:
    """调用大模型生成引导语，失败时回退。"""
    label = state.get("label") or ""
    recent = [_norm(x) for x in ((profile or {}).get("recent_scripts") or [])]
    try:
        ctx = build_user_context(state, profile=profile,
                                 knowledge=retrieve_knowledge(state))
        data = _ask(ctx)
        if data is None:
            raise ValueError("模型未返回可解析的 JSON")
        script = (data.get("script") or "").strip()
        cue = data.get("cue")
        bad = find_violation(script)

        # 触红线 / 空话术：直接换兜底，不给第二次机会（安全优先）。
        if not script or bad:
            why = "空话术" if not script else ("触红线[%s]" % bad)
            print(f"[agent] 弃用模型话术（{why}），改用兜底话术", flush=True)
            return _emit(pick_fallback(label), cue, fallback=True,
                         dropped=data.get("script"))

        # 跟最近说过的太像：先让模型换一种说法重试一次。
        # 只有第二次还是重复，才"保持屏幕上那句不动" —— 宁可字牌不动，
        # 也不要塞一句罐头话，让人以为"它就只会这一句"。
        if _norm(script) in recent or _norm(script) == _norm(_LAST_SAID["text"]):
            print("[agent] 这句跟最近说过的太像，让模型换一种说法再试", flush=True)
            data2 = _ask(ctx + "\n\n【重要】上面『最近已经对他说过』里，"
                               "已经有和你想说的几乎一样的句子了。"
                               "这一次必须换一个完全不同的意象和句式，"
                               "不要复用同样的词。")
            if data2:
                s2 = (data2.get("script") or "").strip()
                if (s2 and not find_violation(s2)
                        and _norm(s2) != _norm(_LAST_SAID["text"])
                        and _norm(s2) not in recent):
                    return _emit(s2, data2.get("cue") or cue, retried=True)
            keep = _LAST_SAID["text"] or pick_fallback(label)
            return _emit(keep, cue, fallback=True, repeat=True,
                         dropped=data.get("script"))

        return _emit(script, cue)
    except Exception as e:  # 兜底：大模型不可用也能演示
        return _emit(pick_fallback(label),
                     "breathing" if label == "tense" else "relax",
                     fallback=True, error=str(e))


async def run() -> None:
    import websockets

    last_label = None
    last_call = 0.0
    # 断线自动重连
    while True:
        try:
            async with websockets.connect(BRIDGE_WS) as ws:
                print("[agent] 已连接 bridge world 通道", flush=True)
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except Exception:
                        continue
                    if msg.get("type") != "state":
                        continue
                    state = {k: v for k, v in msg.items() if k != "type"}
                    label = state.get("label")
                    now = time.time()
                    # 仅在状态变化、或距上次调用超过 MIN_INTERVAL 时才请求大模型
                    if label == last_label and (now - last_call) < MIN_INTERVAL:
                        continue
                    last_label, last_call = label, now
                    # 每轮都重新读一遍记忆（文件很小，8 秒最多读一次）
                    result = await asyncio.to_thread(generate, state, load_profile())
                    # ⚠️ 画面参数只认脑电链路写的那一份（eeg_service + 策略层），
                    # 不让大模型覆盖 —— 否则场景会在两套值之间来回跳
                    # （接入文档第 6 节第 4 步专门点了这个多写者的坑）。
                    # 大模型只负责 script / cue。
                    world = state.get("world_params") or {}
                    out = {"type": "guidance", "t": time.time(),
                           "state": state.get("label"),
                           "script": result.get("script"),
                           "world_params": world,
                           "cue": result.get("cue")}
                    await ws.send(json.dumps(out, ensure_ascii=False))
                    remember({"t": out["t"], "state": out["state"],
                              "calm": state.get("calm"), "score": state.get("score"),
                              "script": out["script"], "params": out["world_params"],
                              "fallback": bool(result.get("fallback"))})
                    print("[agent]", out["state"], "->", out["script"], flush=True)
        except Exception as e:
            print(f"[agent] 与桥接断开（{type(e).__name__}: {e}），3 秒后重连…", flush=True)
            await asyncio.sleep(3)


if __name__ == "__main__":
    asyncio.run(run())
