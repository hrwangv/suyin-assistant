"""通用 MCP 客户端（同步门面）。

底层复用项目已有的 openai-agents（MCPServerStreamableHttp / MCPServerSse），
与 node_web_search_mcp 的接入方式保持一致，避免再引入第二套 MCP 客户端。
"""
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Optional

from agents.mcp import MCPServerSse, MCPServerStreamableHttp

from app.core.logger import logger
from app.core.tracing import observe, update_current_span


@dataclass(frozen=True)
class MCPServerSpec:
    """一个外部 MCP 服务的连接信息。"""

    name: str
    url: Optional[str]
    api_key: Optional[str] = None
    auth_header: str = "x-api-key"
    transport: str = "streamable_http"
    timeout: float = 30.0

    @property
    def configured(self) -> bool:
        return bool(self.url)

    def headers(self) -> dict:
        if not self.api_key:
            return {}
        return {self.auth_header: self.api_key}


def _build_server(spec: MCPServerSpec):
    """按传输方式构造 MCP 服务对象。"""
    params = {"url": spec.url, "headers": spec.headers()}
    if spec.transport == "sse":
        return MCPServerSse(params=params, client_session_timeout_seconds=spec.timeout)
    return MCPServerStreamableHttp(params=params, client_session_timeout_seconds=spec.timeout)


async def _call_tool_async(spec: MCPServerSpec, tool_name: str, arguments: dict) -> Any:
    server = _build_server(spec)
    try:
        await server.connect()
        return await server.call_tool(tool_name=tool_name, arguments=arguments)
    finally:
        try:
            await server.cleanup()
        except Exception:  # cleanup 失败不能覆盖业务结果
            pass


def _run_async(coro) -> Any:
    """在同步代码里跑协程；已经在事件循环里时换一个线程跑。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


# MCP 调用是排查线上问题的关键一段：入参与返回都要在 Langfuse 里看得到。
# 但这里**不能**用装饰器的自动抓参：本函数的第一个参数是 MCPServerSpec，
# 里面带 api_key，自动上报等于把 MCP 密钥推到 Langfuse。
# 所以关掉自动抓取，改由函数体内 update_current_span() 显式上报脱敏后的内容。
@observe(name="mcp:call", as_type="tool", capture_input=False, capture_output=False)
def call_tool(
    spec: MCPServerSpec,
    tool_name: str,
    arguments: dict,
    required: bool = True,
) -> dict:
    """调用外部 MCP 工具，返回规范化后的 dict。

    :param required: 未配置 MCP 时是抛错（True）还是返回空 dict（False）
    """
    if not spec.configured:
        if required:
            raise RuntimeError(f"{spec.name} 未配置（缺少 base_url）")
        return {}

    # 外部工具调用往往是整条链路里最慢的一段：
    # 服务名/工具名进 metadata，调用入参进 input（不含 spec，密钥不外传）。
    # 上报内容仍会走 app/core/tracing.py 的内置脱敏（手机号 / 身份证号 / 长数字）。
    update_current_span(
        input={"server": spec.name, "tool": tool_name, "arguments": arguments},
        metadata={"server": spec.name, "tool": tool_name},
    )

    try:
        result = _run_async(
            asyncio.wait_for(_call_tool_async(spec, tool_name, arguments), timeout=spec.timeout)
        )
    except Exception as exc:
        logger.error(f"[{spec.name}] MCP 调用失败 tool={tool_name}：{exc}")
        update_current_span(output={"error": f"{type(exc).__name__}: {exc}"})
        if required:
            raise
        return {}

    payload = parse_tool_result(result)
    if isinstance(payload, dict) and payload.get("isError"):
        message = f"{spec.name} 返回错误：{str(payload)[:200]}"
        update_current_span(output={"isError": True, "message": message})
        if required:
            raise RuntimeError(message)
        logger.warning(message)
        return {}
    final = payload if isinstance(payload, dict) else {"data": payload}
    update_current_span(output=final)
    return final


def list_tools(spec: MCPServerSpec) -> list[str]:
    """列出 MCP 暴露的工具名（调试用：确认厂商实际的工具名）。"""
    return [item["name"] for item in describe_tools(spec)]


def describe_tools(spec: MCPServerSpec) -> list[dict]:
    """列出 MCP 暴露的工具（名称 + 说明 + 入参 schema）。

    工具箱注册 MCP 工具时需要入参 schema，list_tools 只给名字不够用，
    所以这里多返回一层；失败行为与 list_tools 一致：只记日志、返回空列表。
    """
    if not spec.configured:
        return []

    async def _describe():
        server = _build_server(spec)
        try:
            await server.connect()
            tools = await server.list_tools()
            described: list[dict] = []
            for tool in tools:
                name = getattr(tool, "name", None)
                if not name:
                    continue
                described.append(
                    {
                        "name": str(name),
                        "description": getattr(tool, "description", "") or "",
                        "inputSchema": (
                            getattr(tool, "inputSchema", None)
                            or getattr(tool, "input_schema", None)
                        ),
                    }
                )
            return described
        finally:
            try:
                await server.cleanup()
            except Exception:
                pass

    try:
        return _run_async(_describe())
    except Exception as exc:
        logger.error(f"[{spec.name}] describe_tools 失败：{exc}")
        return []


def parse_tool_result(result: Any) -> Any:
    """把 MCP 的 CallToolResult 拆成普通 Python 结构。

    兼容三种常见返回：
    1. structuredContent / structured_content：已经是结构化数据；
    2. content 里只有一块 text，且是 JSON 字符串；
    3. content 里是纯文本（OCR 厂商常见）。
    """
    structured = (
        getattr(result, "structuredContent", None)
        or getattr(result, "structured_content", None)
    )
    if structured is not None:
        return structured

    if isinstance(result, dict):
        return result

    contents = getattr(result, "content", None)
    if not contents:
        return {"raw": str(result)}

    texts: list[str] = []
    for block in contents:
        text = getattr(block, "text", None)
        if text is None and isinstance(block, dict):
            text = block.get("text")
        if text is not None:
            texts.append(str(text))

    if len(texts) == 1:
        return _loads_or_text(texts[0])
    if texts:
        joined = "\n".join(texts)
        return {"texts": texts, "text": joined}
    return {"raw": str(result)}


def _loads_or_text(text: str) -> Any:
    stripped = (text or "").strip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            return json.loads(stripped)
        except (ValueError, TypeError):
            pass
    return {"text": text}
