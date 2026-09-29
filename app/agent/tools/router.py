"""工具箱管理与调试接口（挂在 /api/agent/toolbox 下）。

    GET  /api/agent/toolbox                工具清单（本地 + 已注册的 MCP 工具）
    GET  /api/agent/toolbox/schema         工具描述（OpenAI function calling 格式）
    POST /api/agent/toolbox/call           按名调用工具（联调/排查用）
    GET  /api/agent/toolbox/mcp            MCP 服务清单（脱敏）；probe=true 时探测各服务工具
    POST /api/agent/toolbox/mcp/{slug}/enable   启用/停用某个 MCP 服务

与已有的 `GET /api/agent/tools`（MCP 配置自检）并存，后者保持不变。
"""
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.agent.mcp import registry as mcp_registry
from app.agent.tools.service import call_tool, list_tools, register_mcp_tools, tool_schemas
from app.core.logger import logger

toolbox_router = APIRouter(prefix="/toolbox", tags=["agent-tools"])


class ToolCallRequest(BaseModel):
    """工具调用请求体。"""

    name: str = Field(..., description="工具名，取自 GET /api/agent/toolbox")
    arguments: dict = Field(default_factory=dict, description="工具入参")


class MCPServerStatusRequest(BaseModel):
    """MCP 服务开关请求体。"""

    enabled: bool = Field(..., description="是否启用")


@toolbox_router.get("")
async def get_tools(category: Optional[str] = None):
    """工具清单（category=buildin / mcp 可选过滤）。"""
    return {"success": True, "data": list_tools(category)}


@toolbox_router.get("/schema")
async def get_tool_schemas(include_mcp: bool = False):
    """工具描述（OpenAI 工具格式）；include_mcp=true 会先连一次 MCP 注册其工具。"""
    schemas = await run_in_threadpool(tool_schemas, None, include_mcp)
    return {"success": True, "data": schemas}


@toolbox_router.post("/call")
async def call_tool_endpoint(request: ToolCallRequest):
    """按名调用工具：失败也返回 200 + ok=false，便于联调看原因。"""
    result = await run_in_threadpool(call_tool, request.name, request.arguments)
    if not result["ok"] and result.get("error", "").startswith("工具不存在"):
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@toolbox_router.get("/mcp")
async def get_mcp_servers(probe: bool = False):
    """MCP 服务清单；probe=true 时逐个探测工具并注册进工具箱。"""
    servers = [entry.to_dict() for entry in mcp_registry.list_servers()]
    info: dict = {"servers": servers, "registered_tools": []}
    if probe:
        info["registered_tools"] = await run_in_threadpool(register_mcp_tools, None, True)
        for server in servers:
            entry = mcp_registry.get_server(server["slug"])
            if entry is not None and entry.usable:
                server["tools"] = [
                    tool["name"]
                    for tool in await run_in_threadpool(mcp_registry.server_tools, server["slug"])
                ]
    return {"success": True, "data": info}


@toolbox_router.post("/mcp/{slug}/enable")
async def set_mcp_server_enabled(slug: str, request: MCPServerStatusRequest):
    """启用/停用某个 MCP 服务（进程内状态）。"""
    try:
        entry = mcp_registry.set_server_enabled(slug, request.enabled)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    logger.info(f"[toolbox] MCP 服务 {slug} enabled={entry.enabled}")
    return {"success": True, "data": entry.to_dict()}
