"""工具注册表：把普通 Python 函数登记成「大模型可调用的工具」。

对照 Yuxi 的 `agents/toolkits/registry.py`，这里保留同样的三件事——
收集实现、收集元数据、按名索引——但不引入 LangChain 依赖，
因为本项目的模型调用走的是「json_mode + 提示词约束」（见 app/agent/llm.py）。

用法：

    @tool(category="buildin", tags=["通用", "计算"], display_name="计算器")
    def calculator(expression: str) -> dict:
        ...

装饰器返回的是原函数，业务代码仍可像普通函数一样直接调用；
工具元数据（名称/说明/入参 schema）则登记在全局注册表里，
由 app/agent/tools/service.py 统一导出给模型和接口。
"""
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Type, Union

from pydantic import BaseModel

from app.core.logger import logger

# 本地工具的默认分类；MCP 注册进来的工具用 "mcp"
DEFAULT_CATEGORY = "buildin"


@dataclass
class ToolMetadata:
    """工具的分类与展示信息。"""

    category: str = DEFAULT_CATEGORY  # buildin / mcp / ...
    tags: list[str] = field(default_factory=list)
    display_name: str = ""  # 给人看的名字（缺省用工具名）
    source: str = "local"  # local（本地函数）/ mcp（外部 MCP 工具）
    server: str = ""  # source=mcp 时：MCP 服务 slug
    remote_name: str = ""  # source=mcp 时：MCP 侧真实工具名


@dataclass
class ToolSpec:
    """一个可被模型调用的工具：说明 + 入参 schema + 执行方式。"""

    name: str
    description: str = ""
    func: Optional[Callable[..., Any]] = None
    # 入参 schema：本地工具传 Pydantic 模型类，MCP 工具传 MCP 返回的 JSON Schema
    args_schema: Union[Type[BaseModel], dict, None] = None
    metadata: ToolMetadata = field(default_factory=ToolMetadata)

    @property
    def display_name(self) -> str:
        return self.metadata.display_name or self.name

    def input_schema(self) -> dict:
        """统一成 JSON Schema（对象的 properties 结构）。"""
        schema = self.args_schema
        if schema is None:
            return {"type": "object", "properties": {}}
        if isinstance(schema, dict):
            return schema
        return schema.model_json_schema()

    def function_schema(self) -> dict:
        """OpenAI function calling 的工具描述格式。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema(),
            },
        }

    def to_dict(self) -> dict:
        """给接口/前端看的元数据（不含实现）。"""
        return {
            "slug": self.name,
            "name": self.display_name,
            "description": self.description,
            "category": self.metadata.category,
            "tags": list(self.metadata.tags),
            "source": self.metadata.source,
            "server": self.metadata.server,
            "args": _schema_to_args(self.input_schema()),
        }

    def call(self, arguments: Optional[dict] = None) -> Any:
        """执行工具：本地工具先按 schema 校验，MCP 工具转交 MCP 注册表。"""
        payload = dict(arguments or {})
        if self.metadata.source == "mcp":
            from app.agent.mcp.registry import call_mcp_tool

            return call_mcp_tool(
                self.metadata.server, self.metadata.remote_name, payload, required=True
            )
        if self.func is None:
            raise RuntimeError(f"工具 {self.name} 没有可执行的实现")
        if isinstance(self.args_schema, type) and issubclass(self.args_schema, BaseModel):
            validated = self.args_schema.model_validate(payload)
            return self.func(**validated.model_dump())
        return self.func(**payload)


def _schema_to_args(schema: dict) -> list[dict]:
    """从 JSON Schema 里抽出参数清单（接口返回用，便于前端展示）。"""
    args = []
    for name, info in (schema.get("properties") or {}).items():
        if not isinstance(info, dict):
            info = {}
        args.append(
            {
                "name": name,
                "type": _schema_type(info),
                "description": info.get("description", ""),
                "required": name in (schema.get("required") or []),
            }
        )
    return args


def _schema_type(info: dict) -> str:
    """把参数类型渲染成可读字符串；Optional/数组取内层类型（如 array<integer>）。"""
    candidates = [info] + [item for item in (info.get("anyOf") or []) if isinstance(item, dict)]
    for item in candidates:
        value = item.get("type")
        if value == "array":
            items = item.get("items") if isinstance(item.get("items"), dict) else {}
            return f"array<{items.get('type', 'any')}>"
        if isinstance(value, str):
            return value
    return ""


# 工具名 -> 工具定义
_tools: dict[str, ToolSpec] = {}


def register_tool(spec: ToolSpec, replace: bool = True) -> ToolSpec:
    """登记一个工具；同名默认覆盖（重复导入模块时不会报错）。"""
    if not spec.name:
        raise ValueError("工具必须有名字")
    if spec.name in _tools and not replace:
        raise ValueError(f"工具 '{spec.name}' 已注册，如需覆盖请传 replace=True")
    _tools[spec.name] = spec
    return spec


def get_tool(name: str) -> Optional[ToolSpec]:
    """按名字取工具定义。"""
    return _tools.get(name)


def get_tool_instances() -> list[ToolSpec]:
    """所有已注册的工具。"""
    return list(_tools.values())


def get_tool_metadata(category: Optional[str] = None) -> list[dict]:
    """工具元数据列表（可选按分类过滤）。"""
    tools = get_tool_instances()
    if category:
        tools = [item for item in tools if item.metadata.category == category]
    return [item.to_dict() for item in tools]


def unregister_tools(source: Optional[str] = None, server: Optional[str] = None) -> int:
    """按来源/服务清掉已注册的工具，返回清理数量（MCP 重新注册前用）。"""
    drop = [
        name
        for name, spec in _tools.items()
        if (source is None or spec.metadata.source == source)
        and (server is None or spec.metadata.server == server)
    ]
    for name in drop:
        _tools.pop(name, None)
    return len(drop)


def unregister_tool(name: str) -> bool:
    """按名字注销一个工具（测试与热更新用），返回是否真的删掉了。"""
    return _tools.pop(name, None) is not None


def tool(
    name_or_func: Union[str, Callable, None] = None,
    *,
    description: Optional[str] = None,
    args_schema: Union[Type[BaseModel], dict, None] = None,
    category: str = DEFAULT_CATEGORY,
    tags: Optional[list[str]] = None,
    display_name: str = "",
    source: str = "local",
):
    """把函数登记成工具，同时返回原函数。

    支持两种写法（与 Yuxi 的 `@tool` 一致）：

        @tool
        def my_tool(...): ...

        @tool(category="buildin", tags=["企业"], display_name="企业检索")
        def my_tool(...): ...

    也可以显式给名字：`@tool("calculator")`。
    """

    def decorate(func: Callable) -> Callable:
        tool_name = name_or_func if isinstance(name_or_func, str) and name_or_func else func.__name__
        register_tool(
            ToolSpec(
                name=tool_name,
                description=description or (func.__doc__ or "").strip(),
                func=func,
                args_schema=args_schema,
                metadata=ToolMetadata(
                    category=category,
                    tags=list(tags or []),
                    display_name=display_name,
                    source=source,
                ),
            )
        )
        logger.debug(f"[tools] 已注册工具 {tool_name}（category={category}）")
        return func

    # @tool 直接修饰函数
    if callable(name_or_func):
        return decorate(name_or_func)
    return decorate
