"""工具箱统一入口：给大模型「看有哪些工具」以及「按名调用工具」。

对照 Yuxi 的 `agents/toolkits/service.py`，这里是同一层职责的最小实现：

    list_tools()                 工具清单（接口/前端展示）
    tool_schemas()               工具描述（OpenAI function calling 格式）
    describe_tools_for_prompt()  工具清单文本（塞进提示词，走 json_mode 也能用）
    call_tool(name, arguments)   统一调用入口（本地工具 + MCP 工具）
    run_tool_calls(calls)        批量执行模型返回的 tool_calls
    register_mcp_tools()         把已启用的 MCP 服务的工具注册进来

调用失败不抛异常，统一返回 `{"ok": False, "error": ...}` 信封：
模型/前端拿到的是可解释的失败，而不是 500。
"""
from typing import Optional

from app.agent.tools.registry import (
    ToolMetadata,
    ToolSpec,
    get_tool,
    get_tool_instances,
    get_tool_metadata,
    register_tool,
    unregister_tools,
)
from app.core.logger import logger
from app.core.tracing import observe, update_current_span


def list_tools(category: Optional[str] = None) -> list[dict]:
    """工具清单（含本地与已注册的 MCP 工具）。"""
    return get_tool_metadata(category)


def resolve_tools(names: Optional[list[str]] = None) -> list[ToolSpec]:
    """按名字筛选工具；names 为空时返回全部。"""
    if not names:
        return get_tool_instances()
    selected = []
    for name in names:
        spec = get_tool(name)
        if spec is None:
            logger.warning(f"[tools] 配置的工具不存在，已跳过：{name}")
            continue
        selected.append(spec)
    return selected


def tool_schemas(
    names: Optional[list[str]] = None,
    include_mcp: bool = False,
) -> list[dict]:
    """工具描述列表（OpenAI function calling 格式）。

    :param include_mcp: True 时会先注册已启用的 MCP 工具（要连一次 MCP，默认不做）
    """
    if include_mcp:
        register_mcp_tools()
    return [spec.function_schema() for spec in resolve_tools(names)]


def describe_tools_for_prompt(
    names: Optional[list[str]] = None,
    include_mcp: bool = False,
) -> str:
    """把工具清单渲染成提示词片段（模型按 json 约定发起调用时用）。"""
    schemas = tool_schemas(names=names, include_mcp=include_mcp)
    if not schemas:
        return "（当前没有可用工具）"

    lines = []
    for item in schemas:
        function = item["function"]
        params = function.get("parameters") or {}
        required = set(params.get("required") or [])
        args = "、".join(
            f"{name}{'（必填）' if name in required else ''}"
            for name in (params.get("properties") or {})
        ) or "无参数"
        lines.append(f"- {function['name']}：{function['description']}\n  参数：{args}")
    return "\n".join(lines)


def register_mcp_tools(slugs: Optional[list[str]] = None, force: bool = False) -> list[str]:
    """把 MCP 服务暴露的工具注册进工具箱，返回注册成功的工具名。

    :param slugs: 只注册这些服务；为空时注册所有已启用且已配置的服务
    :param force: 强制重新探测 MCP（默认用缓存）
    """
    from app.agent.mcp import registry as mcp_registry

    registered: list[str] = []
    if slugs:
        servers = [entry for entry in (mcp_registry.get_server(slug) for slug in slugs) if entry]
    else:
        servers = mcp_registry.list_servers(enabled_only=True)

    for entry in servers:
        if not entry.usable:
            logger.warning(f"[tools] MCP 服务 {entry.slug} 未启用或未配置，跳过工具注册")
            continue
        # 先清掉这个服务上一轮注册的工具，避免服务端删了工具后留下幽灵工具
        unregister_tools(source="mcp", server=entry.slug)
        for item in mcp_registry.server_tools(entry.slug, force=force):
            remote_name = item.get("name") or ""
            if not remote_name or remote_name in entry.disabled_tools:
                continue
            spec = ToolSpec(
                name=mcp_tool_name(entry.slug, remote_name),
                description=item.get("description") or f"{entry.name} 提供的工具",
                args_schema=_mcp_input_schema(item.get("inputSchema")),
                metadata=ToolMetadata(
                    category="mcp",
                    tags=["mcp", entry.slug],
                    display_name=remote_name,
                    source="mcp",
                    server=entry.slug,
                    remote_name=remote_name,
                ),
            )
            register_tool(spec)
            registered.append(spec.name)

    logger.info(f"[tools] MCP 工具注册完成：{registered}")
    return registered


def mcp_tool_name(server_slug: str, remote_name: str) -> str:
    """MCP 工具在工具箱里的名字：mcp__服务__工具（与 Yuxi 的命名一致）。"""
    return f"mcp__{server_slug}__{remote_name}"


def _mcp_input_schema(schema: object) -> dict:
    """MCP 的 inputSchema 直接当入参 schema 用；缺失/不合法时给空对象兜底。"""
    if not isinstance(schema, dict) or not schema:
        return {"type": "object", "properties": {}}
    normalized = dict(schema)
    if "type" not in normalized:
        normalized["type"] = "object"
    if normalized.get("type") == "object" and "properties" not in normalized:
        normalized["properties"] = {}
    return normalized


# 与 mcp:call 保持一致：入参和结果都上报，工具调用在 Langfuse 里可完整回溯。
@observe(name="tool:call", as_type="tool", capture_input=True, capture_output=True)
def call_tool(name: str, arguments: Optional[dict] = None) -> dict:
    """统一调用入口：返回 {"ok", "tool", "result"|"error"}。

    调用失败只记日志、返回信封，不抛异常——模型或前端拿到的是可解释的失败。
    """
    spec = get_tool(name)
    if spec is None:
        logger.warning(f"[tools] 调用了不存在的工具：{name}")
        return {"ok": False, "tool": name, "error": f"工具不存在：{name}"}
    update_current_span(metadata={"tool": name})
    try:
        result = spec.call(arguments or {})
    except Exception as exc:
        logger.error(f"[tools] 工具 {name} 调用失败：{exc}")
        return {"ok": False, "tool": name, "error": str(exc)}
    return {"ok": True, "tool": name, "result": result}


def run_tool_calls(calls: list[dict]) -> list[dict]:
    """批量执行模型给出的 tool_calls。

    约定模型返回：`{"tool_calls": [{"name": "...", "arguments": {...}}]}`，
    编排层取到后直接丢给这里执行（与项目「json_mode + 提示词约束」的调用口径一致）。
    """
    results = []
    for call in calls or []:
        if not isinstance(call, dict):
            continue
        name = call.get("name") or call.get("tool") or ""
        arguments = call.get("arguments") or call.get("args") or {}
        results.append(call_tool(name, arguments if isinstance(arguments, dict) else {}))
    return results
