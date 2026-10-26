"""离线语音合成（piper）。

为什么不用云端 TTS：这个项目讲的是"数据不出院"，引导语里可能出现
"你的放松度比上次低"这种带健康信息的话，没必要送到外面去合成。
piper 全本地跑，一次 3 秒的句子约 1 秒出结果。

模型：rhasspy/piper-voices · zh_CN-huayan-medium（MIT 许可的数据集/模型卡）
位置：data/voices/zh_CN-hyuayan-medium.onnx（不入库，首次运行前放好）
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VOICE = ROOT / "data" / "voices" / "zh_CN-huayan-medium.onnx"
CACHE = ROOT / "data" / "tts"
MAX_CHARS = 160

_CLEAN = re.compile(r"[*_#`>\[\]()]|https?://\S+")


def available() -> bool:
    return VOICE.exists()


def _clean(text: str) -> str:
    text = _CLEAN.sub("", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:MAX_CHARS]


def synth(text: str) -> Path | None:
    """返回 wav 路径；同样的句子只合成一次。"""
    if not available():
        return None
    text = _clean(text)
    if not text:
        return None
    CACHE.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
    out = CACHE / f"{key}.wav"
    if out.exists() and out.stat().st_size > 1000:
        return out

    piper = Path(sys.executable).parent / "piper"
    try:
        subprocess.run(
            [str(piper), "-m", str(VOICE),
             "-c", str(VOICE.with_suffix(".onnx.json")),
             "-f", str(out)],
            input=text.encode("utf-8"),
            capture_output=True, timeout=25, check=True,
        )
    except Exception:  # noqa: BLE001
        return None
    return out if out.exists() and out.stat().st_size > 1000 else None


if __name__ == "__main__":
    import sys as _sys
    p = synth(" ".join(_sys.argv[1:]) or "现在很安静，慢慢放松肩膀。")
    print(p or "合成失败（检查 data/voices 下的模型）")
