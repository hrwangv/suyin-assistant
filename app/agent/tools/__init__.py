"""Agent 工具箱：工具的注册、管理与调用。

    registry.py  @tool 装饰器 + 全局工具注册表（本地工具）
    buildin.py   内置工具（知识库问答 / 企业检索 / 文档识别）
    service.py   统一入口：清单、schema、调用、MCP 工具注册
    router.py    /api/agent/toolbox/* 管理与调试接口

导入本包即完成内置工具注册（与 Yuxi 的 toolkits/__init__.py 行为一致）。
"""
from app.agent.tools.registry import (  # noqa: F401
    ToolMetadata,
    ToolSpec,
    get_tool,
    get_tool_instances,
    get_tool_metadata,
    register_tool,
    tool,
    unregister_tool,
    unregister_tools,
)
from app.agent.tools import buildin  # noqa: F401  导入即注册内置工具
from app.agent.tools.service import (  # noqa: F401
    call_tool,
    describe_tools_for_prompt,
    list_tools,
    register_mcp_tools,
    resolve_tools,
    run_tool_calls,
    tool_schemas,
)

__all__ = [
    "ToolMetadata",
    "ToolSpec",
    "buildin",
    "call_tool",
    "describe_tools_for_prompt",
    "get_tool",
    "get_tool_instances",
    "get_tool_metadata",
    "list_tools",
    "register_mcp_tools",
    "register_tool",
    "resolve_tools",
    "run_tool_calls",
    "tool",
    "tool_schemas",
    "unregister_tool",
    "unregister_tools",
]
