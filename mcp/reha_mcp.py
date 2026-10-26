"""自建康复知识库 MCP Server（赛题 MCP 加分项）。

把康复知识查询封装成 MCP 工具，任何支持 MCP 的 harness（Claude Code / Hermes）
都能直接调用。运行：
    python mcp/reha_mcp.py
在 harness 配置里把它声明为 MCP server（stdio）。
"""
from __future__ import annotations

# 兼容 mcp 2.x（FastMCP 已更名为 MCPServer）
try:
    from mcp.server.fastmcp import FastMCP  # mcp 1.x
    mcp = FastMCP("neurobloom-reha")
except ImportError:  # mcp 2.x
    from mcp.server import MCPServer
    mcp = MCPServer("neurobloom-reha")

_KB = {
    "注意力": "建议进行 10 分钟专注类 VR 训练，配合即时视觉反馈（水晶生长）。",
    "情绪": "建议情绪调节训练：放松态花朵绽放，焦虑态引导 4-7-8 呼吸。",
    "记忆": "建议工作记忆训练，采用渐进难度与成就激励。",
    "放松": "推荐正念与渐进式肌肉放松，配合自然音效与阳光场景。",
}


@mcp.tool()
def query_reha_guide(topic: str) -> str:
    """根据康复主题返回训练建议。topic 可为：注意力/情绪/记忆/放松。"""
    for key, val in _KB.items():
        if key in topic:
            return val
    return "暂无对应条目，请咨询康复治疗师。"


@mcp.tool()
def get_patient_stage(scales: dict | None = None) -> str:
    """根据评定量表（如 MMSE/MoCA 分数）推断康复阶段。"""
    if not scales:
        return "未提供量表，建议先做基线评估。"
    mmse = scales.get("MMSE")
    if mmse is None:
        return "缺少 MMSE 分数。"
    if mmse >= 27:
        return "认知正常，可进行高阶专注训练。"
    if mmse >= 21:
        return "轻度认知障碍，采用中等强度训练。"
    return "中度及以上，采用低强度、强引导训练。"


if __name__ == "__main__":
    mcp.run()
