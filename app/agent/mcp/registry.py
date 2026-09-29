"""MCP 服务注册表：管理「有哪些 MCP 服务」，并把它们的工具交给工具箱。

对照 Yuxi 的 `agents/mcp/service.py`，这里是同一套思路的最小实现：
配置与工具获取都收在这一层，上层只按 slug 拿连接信息或调工具。

配置来源（后者覆盖前者）：
    1. 代码声明：`app/agent/mcp/builtin.py` 的 BUILTIN_MCP_SERVERS（唯一配置处）
    2. 运行时补充：`register_server()`（脚本/测试里临时挂服务）
    3. 运行时开关：`set_server_enabled()` 只改进程内状态（第一版不落库）

两条纪律：
    - 导入期不建连：只有 `server_tools`（探测工具）和 `call_mcp_tool`（调用）会真的连，
      失败只记日志并返回空结果，与项目其它 MCP 通道的降级策略一致。
    - 对外只给脱敏信息：`to_dict()` 不含 api_key，调试接口不会漏密钥。
"""
from dataclasses import dataclass, field

from app.agent.mcp.client import MCPServerSpec, call_tool, describe_tools
from app.agent.mcp.builtin import (
    BUILTIN_MCP_SERVERS,
    resolve_api_key,
    resolve_url,
)
from app.core.logger import logger

# 内置服务 slug：业务代码按 slug 取能力（声明见 app/agent/mcp/builtin.py）
OCR_SERVER = "ocr"
COMPANY_SERVER = "company"


@dataclass
class MCPServerEntry:
    """一个 MCP 服务的注册信息。"""

    slug: str
    name: str
    url: str | None = None
    api_key: str | None = None
    auth_header: str = "x-api-key"
    transport: str = "streamable_http"
    timeout: float = 30.0
    enabled: bool = True
    # 服务里不想暴露给模型的工具名（注册时跳过）
    disabled_tools: list[str] = field(default_factory=list)
    description: str = ""
    tags: list[str] = field(default_factory=list)
    icon: str = ""
    # 代码按语义名调用的工具：{语义名: 服务端真实工具名}
    tools: dict = field(default_factory=dict)
    # 该 MCP 的入参适配（如 OCR 的 input_mode / args_template）
    adapter: dict = field(default_factory=dict)
    # 来源：builtin（builtin.py 声明）/ runtime（register_server 临时注册）
    source: str = "builtin"

    @property
    def configured(self) -> bool:
        """是否配齐了连接信息（有 url 才算可用）。"""
        return bool(self.url)

    @property
    def usable(self) -> bool:
        """当前是否可用于注册/调用。启用+配齐连接信息"""
        return self.enabled and self.configured

    def to_spec(self) -> MCPServerSpec:
        """转成 MCP 客户端认识的连接描述。"""
        return MCPServerSpec(
            name=self.slug,
            url=self.url,
            api_key=self.api_key,
            auth_header=self.auth_header,
            transport=self.transport,
            timeout=self.timeout,
        )

    def to_dict(self) -> dict:
        """脱敏后的信息（不含密钥），供接口与日志使用。"""
        return {
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "icon": self.icon,
            "tags": list(self.tags),
            "transport": self.transport,
            "url": self.url,
            "enabled": self.enabled,
            "configured": self.configured,
            "tools": dict(self.tools),
            "disabled_tools": list(self.disabled_tools),
            "source": self.source,
        }


# slug -> 服务；进程内状态，不落库
_servers: dict[str, MCPServerEntry] = {}
# slug -> 探测到的工具列表（{name, description, inputSchema}）
_tools_cache: dict[str, list[dict]] = {}
_loaded = False


def ensure_servers_loaded() -> None:
    """首次访问时把 builtin.py 里声明的 MCP 服务装进注册表（幂等）。"""
    global _loaded
    if _loaded:
        return
    _loaded = True
    _register_builtin_servers()


def _register_builtin_servers() -> None:
    """把 builtin.py 里声明的服务注册进来。"""
    for slug, definition in BUILTIN_MCP_SERVERS.items():
        _servers[slug] = _entry_from_definition(slug, definition)


def _entry_from_definition(slug: str, definition: dict) -> MCPServerEntry:
    """把一条声明转成注册项（地址/密钥在此时解析，之后按注册项工作）。"""
    try:
        timeout = float(definition.get("timeout", 30.0))
    except (TypeError, ValueError):
        timeout = 30.0
    return MCPServerEntry(
        slug=slug,
        name=str(definition.get("name") or slug),
        description=str(definition.get("description") or ""),
        url=resolve_url(definition),
        api_key=resolve_api_key(definition),
        auth_header=str(definition.get("auth_header") or "x-api-key"),
        transport=str(definition.get("transport") or "streamable_http").strip().lower(),
        timeout=timeout,
        enabled=bool(definition.get("enabled", True)),
        disabled_tools=list(definition.get("disabled_tools") or []),
        tags=list(definition.get("tags") or []),
        icon=str(definition.get("icon") or ""),
        tools=dict(definition.get("tools") or {}),
        adapter=dict(definition.get("adapter") or {}),
        source="builtin",
    )


def resolve_tool_name(slug: str, key: str, default: str = "") -> str:
    """按语义名取服务端真实工具名（如 ocr 的 recognize → GeneralOcrRecognition）。"""
    entry = get_server(slug)
    if entry is None:
        return default
    return str(entry.tools.get(key) or default)


def server_adapter(slug: str) -> dict:
    """取某个服务的入参适配配置（不存在时给空字典）。"""
    entry = get_server(slug)
    return dict(entry.adapter) if entry else {}


def register_server(entry: MCPServerEntry, replace: bool = False) -> MCPServerEntry:
    """注册（或覆盖）一个 MCP 服务。"""
    ensure_servers_loaded()
    if entry.slug in _servers and not replace:
        raise ValueError(f"MCP 服务 '{entry.slug}' 已存在，如需覆盖请传 replace=True")
    _servers[entry.slug] = entry
    clear_tools_cache(entry.slug)
    logger.info(f"[mcp] 已注册 MCP 服务 {entry.slug}（enabled={entry.enabled}）")
    return entry


def get_server(slug: str) -> MCPServerEntry | None:
    """按 slug 取服务信息。"""
    ensure_servers_loaded()
    return _servers.get(slug)


def list_servers(enabled_only: bool = False) -> list[MCPServerEntry]:
    """列出所有服务（`enabled_only=True` 时只给可用的）。"""
    ensure_servers_loaded()
    entries = list(_servers.values())
    if enabled_only:
        return [entry for entry in entries if entry.usable]
    return entries


def set_server_enabled(slug: str, enabled: bool) -> MCPServerEntry:
    """开关一个 MCP 服务（进程内状态，重启后回到配置值）。"""
    entry = get_server(slug)
    if entry is None:
        raise KeyError(f"MCP 服务 '{slug}' 不存在")
    entry.enabled = bool(enabled)
    logger.info(f"[mcp] 服务 {slug} enabled={entry.enabled}")
    return entry


def server_tools(slug: str, force: bool = False) -> list[dict]:
    """探测某个服务暴露的工具（带缓存；失败返回空列表）。"""
    entry = get_server(slug)
    if entry is None:
        logger.warning(f"[mcp] 服务 {slug} 未注册，无法探测工具")
        return []
    if not entry.usable:
        logger.warning(f"[mcp] 服务 {slug} 未启用或未配置，跳过工具探测")
        return []
    if not force and slug in _tools_cache:
        return _tools_cache[slug]

    tools = describe_tools(entry.to_spec())
    _tools_cache[slug] = tools
    logger.info(f"[mcp] 服务 {slug} 探测到 {len(tools)} 个工具：{[t['name'] for t in tools]}")
    return tools


def call_mcp_tool(slug: str, tool_name: str, arguments: dict, required: bool = True) -> dict:
    """按 slug + 工具名调用 MCP 工具，返回规范化后的 dict。"""
    entry = get_server(slug)
    if entry is None:
        raise KeyError(f"MCP 服务 '{slug}' 不存在")
    if not entry.usable:
        raise RuntimeError(f"MCP 服务 '{slug}' 未启用或未配置")
    if tool_name in entry.disabled_tools:
        raise RuntimeError(f"MCP 服务 '{slug}' 的工具 '{tool_name}' 已被禁用")
    return call_tool(entry.to_spec(), tool_name, arguments or {}, required=required)


def clear_tools_cache(slug: str | None = None) -> None:
    """清掉工具探测缓存（配置变了或需要重新探测时调用）。"""
    if slug is None:
        _tools_cache.clear()
    else:
        _tools_cache.pop(slug, None)


def reset_registry() -> None:
    """清空注册表并允许重新加载（测试与热更新用）。"""
    global _loaded
    _servers.clear()
    _tools_cache.clear()
    _loaded = False
