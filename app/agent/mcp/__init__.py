"""外部 MCP 适配层。

对上层只暴露「一个同步函数 + 一个规范化后的 dict」，把 MCP 的
传输方式（streamable_http / sse）、鉴权头、工具名、返回结构差异
全部收敛在这一层。换 MCP 厂商时只改 .env，不改业务代码。
"""
from app.agent.mcp.client import (
    MCPServerSpec,
    call_tool,
    describe_tools,
    list_tools,
)

__all__ = [
    "MCPServerSpec",
    "call_tool",
    "describe_tools",
    "list_tools",
]
