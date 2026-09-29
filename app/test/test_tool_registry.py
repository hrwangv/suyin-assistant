"""工具箱（工具 + MCP 注册/管理/调用）自检：离线，不联网、不依赖 MySQL/Qdrant。

用法：
    PYTHONPATH=. python app/test/test_tool_registry.py

覆盖：
1. 内置工具注册：计算器 / 当前时间 / 文本统计 / 随机挑选，元数据与分类齐全
2. 本地工具调用：入参校验、未知工具、工具异常都被收敛成 ok=false 信封
3. 工具描述导出：function calling 格式 + 提示词片段 + 按名筛选
4. 批量执行模型返回的 tool_calls
5. MCP 服务管理：注册 / 列出 / 启停 / 脱敏（服务清单来自代码声明 builtin.py）
6. MCP 声明解析：url 优先声明、其次 url_env；密钥只从环境变量取
7. MCP 工具注册与调用：mcp__服务__工具 命名、禁用工具过滤、服务停用降级
8. HTTP 接口：/api/agent/toolbox 的清单 / schema / 调用 / MCP 清单
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pydantic import BaseModel, Field  # noqa: E402

from app.agent.mcp import builtin as mcp_builtin  # noqa: E402
from app.agent.mcp import registry as mcp_registry  # noqa: E402
from app.agent.mcp.registry import MCPServerEntry  # noqa: E402
from app.agent.tools import (  # noqa: E402
    ToolMetadata,
    ToolSpec,
    call_tool,
    describe_tools_for_prompt,
    get_tool,
    list_tools,
    register_mcp_tools,
    register_tool,
    run_tool_calls,
    tool,
    tool_schemas,
    unregister_tool,
    unregister_tools,
)

FAKE_SERVER = "fake_mcp"
BUILTIN_TOOLS = {"calculator", "current_time", "text_stats", "random_pick"}
BUILTIN_SERVERS = {"ocr", "company", "yjt"}
# 工具名 / 入参形态都跟着 builtin.py 的声明走（换厂商只改声明，测试不用改）
OCR_TOOL_NAME = mcp_builtin.BUILTIN_MCP_SERVERS["ocr"]["tools"]["recognize"]
OCR_INPUT_MODE = mcp_builtin.BUILTIN_MCP_SERVERS["ocr"]["adapter"]["input_mode"]


def _isolate_builtin_servers() -> None:
    """清掉声明与环境变量里的 MCP 地址，保证自检不依赖外部环境、不打真实网络。

    地址有两个来源（后者覆盖前者）：builtin.py 声明里的 url、url_env 指向的环境变量。
    两处都要摘掉，注册出来的服务才是"未配置"状态——只清环境变量的话，
    声明里直接写死的地址（本地联调常用）会让服务仍然处于可用状态。
    """
    for name in ("OCR_MCP_BASE_URL", "YJT_BASE_URL"):
        os.environ.pop(name, None)
    for definition in mcp_builtin.BUILTIN_MCP_SERVERS.values():
        definition["url"] = ""
    mcp_registry.reset_registry()


# --------------------------------------------------------------------------
# 1. 内置工具注册
# --------------------------------------------------------------------------
def test_builtin_tools_registered():
    slugs = {item["slug"] for item in list_tools()}

    # assert断言，当表达式正确的时候就运行，没反应，错误则报错
    assert slugs == BUILTIN_TOOLS, slugs

    calculator = next(item for item in list_tools() if item["slug"] == "calculator")
    assert calculator["name"] == "计算器"
    assert calculator["category"] == "buildin" and calculator["source"] == "local"
    assert [arg["name"] for arg in calculator["args"]] == ["expression"]
    assert calculator["args"][0]["required"] is True

    # 按分类过滤
    assert {item["slug"] for item in list_tools("buildin")} == BUILTIN_TOOLS
    assert list_tools("mcp") == []
    print("[PASS] 内置工具注册（元数据 + 分类）OK")


# --------------------------------------------------------------------------
# 2. 内置小工具的实际行为
# --------------------------------------------------------------------------
def test_builtin_tools_behaviour():
    # calculator：正常算式
    assert call_tool("calculator", {"expression": "(1+2)*3"})["result"]["value"] == 9
    assert call_tool("calculator", {"expression": "7/2"})["result"]["value"] == 3.5
    assert call_tool("calculator", {"expression": "round(2.567, 2)"})["result"]["value"] == 2.57
    # calculator：只允许算术，任何取属性 / 调用别的函数都被拒
    for bad in ('__import__("os")', 'open("x")', "1 + a", "2**99999"):
        result = call_tool("calculator", {"expression": bad})
        assert result["ok"] is False, (bad, result)
    assert call_tool("calculator", {"expression": ""})["ok"] is False

    # current_time：默认上海时区；时区不合法要明确报错，不静默兜底
    now = call_tool("current_time", {})["result"]
    assert now["timezone"] == "Asia/Shanghai" and len(now["date"]) == 10, now
    assert call_tool("current_time", {"timezone": "Asia/Shanghai"})["ok"] is True
    assert call_tool("current_time", {"timezone": "Not/AZone"})["ok"] is False

    # text_stats：字符 / 行 / 词 / 高频词
    stats = call_tool("text_stats", {"text": "苏银 助手\n苏银 助手 hello", "top_n": 2})["result"]
    assert stats["chars"] == 17 and stats["lines"] == 2, stats
    assert stats["words"] == 1 and stats["cjk_chars"] == 8, stats
    # 中文按双字滑窗取词：出现两次的"苏银""助手"排在最前
    assert stats["top_terms"][0]["count"] == 2 and stats["top_terms"][0]["term"] in {"苏银", "助手"}, stats
    assert call_tool("text_stats", {"text": "", "top_n": 1})["result"]["chars"] == 0

    # random_pick：结果必须是候选的子集，且不重复
    picked = call_tool("random_pick", {"options": ["甲", "乙", "丙"], "count": 2})["result"]
    assert len(picked["picked"]) == 2 and set(picked["picked"]) <= {"甲", "乙", "丙"}, picked
    assert call_tool("random_pick", {"options": [], "count": 1})["ok"] is False
    assert call_tool("random_pick", {"options": ["甲"], "count": 3})["ok"] is False
    print("[PASS] 内置小工具行为（计算器安全求值 / 时间 / 文本统计 / 随机挑选）OK")


# --------------------------------------------------------------------------
# 3. 本地工具调用
# --------------------------------------------------------------------------
class _EchoArgs(BaseModel):
    text: str = Field(description="要回显的文本")
    times: int = Field(default=1, ge=1, le=5, description="重复次数")


def _echo(text: str, times: int = 1) -> str:
    """把文本重复若干次（自检用工具）。"""
    return text * times


def _boom():
    """自检用：总是抛异常，验证异常会被收敛成信封。"""
    raise RuntimeError("boom")


def _decorated_echo(text: str) -> str:
    """用 @tool 装饰器登记的工具（自检用）。"""
    return text


def test_local_tool_call():
    # @tool 装饰器：既登记元数据，也保留函数可调用性（这里在函数内应用，
    # 免得自检工具在模块导入时就混进"内置工具"清单）
    tool(category="test", tags=["自检"], display_name="装饰器示例")(_decorated_echo)
    register_tool(
        ToolSpec(
            name="_echo",
            description="回显",
            func=_echo,
            args_schema=_EchoArgs,
            metadata=ToolMetadata(category="test"),
        )
    )
    register_tool(ToolSpec(name="_boom", description="总是失败", func=_boom))

    decorated = get_tool("_decorated_echo")
    assert decorated is not None and decorated.metadata.category == "test"
    assert decorated.display_name == "装饰器示例"
    assert decorated.func("苏") == "苏"

    ok = call_tool("_echo", {"text": "苏", "times": 3})
    assert ok["ok"] and ok["result"] == "苏苏苏", ok

    # 缺必填参数 → 校验失败，收敛成信封
    missing = call_tool("_echo", {})
    assert missing["ok"] is False and "text" in missing["error"], missing

    # 越界参数 → 同样被拦下
    out_of_range = call_tool("_echo", {"text": "苏", "times": 99})
    assert out_of_range["ok"] is False, out_of_range

    # 未知工具
    unknown = call_tool("not_exist_tool", {})
    assert unknown["ok"] is False and "不存在" in unknown["error"], unknown

    # 工具内部异常不冒泡成 500
    boom = call_tool("_boom", {})
    assert boom["ok"] is False and "boom" in boom["error"], boom

    # 自检工具用完就清掉，别污染后续断言
    for name in ("_echo", "_boom", "_decorated_echo"):
        assert unregister_tool(name) is True, name
    assert get_tool("_echo") is None and get_tool("_boom") is None
    assert get_tool("_decorated_echo") is None
    # 内置工具不受影响
    assert {item["slug"] for item in list_tools()} == BUILTIN_TOOLS
    print("[PASS] 本地工具调用（校验 / 未知工具 / 异常信封）OK")


# --------------------------------------------------------------------------
# 4. 工具描述导出（function calling 格式 / 提示词片段 / 按名筛选）
# --------------------------------------------------------------------------
def test_tool_schema_export():
    schemas = {item["function"]["name"]: item for item in tool_schemas()}
    assert "calculator" in schemas
    function = schemas["calculator"]["function"]
    assert function["parameters"]["type"] == "object"
    assert function["parameters"]["required"] == ["expression"]

    only = tool_schemas(names=["text_stats"])
    assert [item["function"]["name"] for item in only] == ["text_stats"]

    prompt = describe_tools_for_prompt(names=["calculator"])
    assert "calculator" in prompt and "expression（必填）" in prompt
    assert describe_tools_for_prompt(names=["not_exist_tool"]) == "（当前没有可用工具）"
    print("[PASS] 工具描述导出（schema / 提示词片段 / 筛选）OK")


# --------------------------------------------------------------------------
# 5. 批量执行模型返回的 tool_calls
# --------------------------------------------------------------------------
def test_run_tool_calls():
    results = run_tool_calls(
        [
            {"name": "calculator", "arguments": {"expression": "6*7"}},
            {"name": "text_stats", "arguments": {}},
            "这不是一个合法的 tool_call",
        ]
    )
    assert len(results) == 2, results
    assert results[0]["ok"] and results[0]["result"]["value"] == 42, results[0]
    # 缺必填参数 → 具体原因保留在 error 里
    assert results[1]["ok"] is False and "text" in results[1]["error"], results[1]
    print("[PASS] 批量执行 tool_calls（含非法项忽略）OK")


# --------------------------------------------------------------------------
# 6. MCP 服务管理（注册 / 列出 / 启停 / 脱敏）
# --------------------------------------------------------------------------
def test_mcp_server_management():
    _isolate_builtin_servers()
    servers = {entry.slug: entry for entry in mcp_registry.list_servers()}
    # 服务清单来自代码声明 app/agent/mcp/builtin.py
    assert set(servers) == BUILTIN_SERVERS, servers
    # 语义工具名与入参适配都从声明里取（换厂商只改声明）
    assert mcp_registry.resolve_tool_name("ocr", "recognize") == OCR_TOOL_NAME
    # 企业信息走财汇 MCP：真实工具名是唯一执行入口 execute_tool（子工具在 adapter.sub_tool）
    assert (
        mcp_registry.resolve_tool_name("company", "search")
        == mcp_builtin.BUILTIN_MCP_SERVERS["company"]["tools"]["search"]
    )
    # 子工具名也跟声明走（换子工具只改 builtin.py）
    assert (
        mcp_registry.server_adapter("company")["sub_tool"]
        == mcp_builtin.BUILTIN_MCP_SERVERS["company"]["adapter"]["sub_tool"]
    )
    assert mcp_registry.server_adapter("company")["sub_tool"], "execute_tool 模式必须声明子工具"
    assert mcp_registry.server_adapter("ocr")["input_mode"] == OCR_INPUT_MODE
    # 没配 url 的服务不算可用（enabled 但没地址）
    assert mcp_registry.list_servers(enabled_only=True) == []
    mcp_registry.register_server(
        MCPServerEntry(slug="no_url", name="没地址", url=None), replace=True
    )
    assert "no_url" not in {item.slug for item in mcp_registry.list_servers(enabled_only=True)}

    entry = mcp_registry.register_server(
        MCPServerEntry(
            slug=FAKE_SERVER,
            name="假 MCP",
            url="http://127.0.0.1:9/mcp",
            api_key="secret-key",
            disabled_tools=["danger_tool"],
        ),
        replace=True,
    )
    assert entry.usable
    assert [item.slug for item in mcp_registry.list_servers(enabled_only=True)] == [FAKE_SERVER]
    # 对外信息不含密钥
    exposed = [item.to_dict() for item in mcp_registry.list_servers()]
    assert all("api_key" not in item for item in exposed)
    assert "secret-key" not in str(exposed)

    mcp_registry.set_server_enabled(FAKE_SERVER, False)
    assert mcp_registry.list_servers(enabled_only=True) == []
    mcp_registry.set_server_enabled(FAKE_SERVER, True)
    assert [item.slug for item in mcp_registry.list_servers(enabled_only=True)] == [FAKE_SERVER]

    try:
        mcp_registry.set_server_enabled("not_exist", True)
    except KeyError:
        pass
    else:
        raise AssertionError("未知 MCP 服务应当报错")
    print("[PASS] MCP 服务管理（注册 / 启停 / 脱敏）OK")


# --------------------------------------------------------------------------
# 6.1 MCP 声明解析：地址优先用声明，其次回退 url_env；密钥只从环境变量取
# --------------------------------------------------------------------------
def test_mcp_definition_resolution():
    definition = {"url": "", "url_env": "TEST_MCP_URL", "api_key": "", "api_key_env": "TEST_MCP_KEY"}
    assert mcp_builtin.resolve_url(definition) is None
    assert mcp_builtin.resolve_api_key(definition) is None

    original_url = os.environ.get("TEST_MCP_URL")
    original_key = os.environ.get("TEST_MCP_KEY")
    os.environ["TEST_MCP_URL"] = "https://example.test/mcp"
    os.environ["TEST_MCP_KEY"] = "k-123"
    try:
        assert mcp_builtin.resolve_url(definition) == "https://example.test/mcp"
        assert mcp_builtin.resolve_api_key(definition) == "k-123"
        # 声明里直接写地址时，优先于环境变量
        inline = mcp_builtin.resolve_url({**definition, "url": "https://inline.test/mcp"})
        assert inline == "https://inline.test/mcp", inline
    finally:
        for name, value in (("TEST_MCP_URL", original_url), ("TEST_MCP_KEY", original_key)):
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    print("[PASS] MCP 声明解析（url 优先声明、密钥只从环境变量取）OK")


# --------------------------------------------------------------------------
# 7. MCP 工具注册与调用（换掉 MCP 传输层，其余链路全跑真的）
# --------------------------------------------------------------------------
def test_mcp_tools_registration_and_call():
    _isolate_builtin_servers()
    mcp_registry.register_server(
        MCPServerEntry(
            slug=FAKE_SERVER,
            name="假 MCP",
            url="http://127.0.0.1:9/mcp",
            disabled_tools=["danger_tool"],
        )
    )

    seen: dict = {}

    def fake_describe(spec):
        seen["server"] = spec.name
        return [
            {
                "name": "lookup_company",
                "description": "按名称查企业",
                "inputSchema": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
            },
            {"name": "danger_tool", "description": "被禁用的工具", "inputSchema": None},
        ]

    def fake_call(spec, tool_name, arguments, required=True):
        seen["call"] = (spec.name, tool_name, arguments)
        return {"candidates": [{"company_name": arguments.get("name")}]}

    original_describe, original_call = mcp_registry.describe_tools, mcp_registry.call_tool
    mcp_registry.describe_tools, mcp_registry.call_tool = fake_describe, fake_call
    try:
        registered = register_mcp_tools()
        assert registered == [f"mcp__{FAKE_SERVER}__lookup_company"], registered
        # 被禁用的工具不进工具箱
        assert get_tool(f"mcp__{FAKE_SERVER}__danger_tool") is None

        info = next(item for item in list_tools() if item["slug"] == "mcp__fake_mcp__lookup_company")
        assert info["category"] == "mcp" and info["server"] == FAKE_SERVER
        assert info["args"][0]["name"] == "name"

        result = call_tool("mcp__fake_mcp__lookup_company", {"name": "欣旺达"})
        assert result["ok"], result
        assert result["result"]["candidates"][0]["company_name"] == "欣旺达"
        assert seen["call"][1] == "lookup_company", seen

        # 服务停用后：不再注册，调用也直接失败（不去打网络）
        mcp_registry.set_server_enabled(FAKE_SERVER, False)
        assert register_mcp_tools() == []
        unavailable = call_tool("mcp__fake_mcp__lookup_company", {"name": "欣旺达"})
        assert unavailable["ok"] is False and "未启用" in unavailable["error"], unavailable
    finally:
        mcp_registry.describe_tools, mcp_registry.call_tool = original_describe, original_call
        unregister_tools(source="mcp")
        mcp_registry.reset_registry()
        mcp_registry.ensure_servers_loaded()
    print("[PASS] MCP 工具注册与调用（命名 / 禁用工具 / 停用降级）OK")


# --------------------------------------------------------------------------
# 8. HTTP 接口
# --------------------------------------------------------------------------
def test_http_routes():
    try:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
    except ImportError:  # 没装 httpx 时跳过（不影响工具箱本体）
        print("[SKIP] 未安装 httpx / fastapi.testclient，跳过 HTTP 接口自检")
        return

    from app.agent.tools.router import toolbox_router

    app = FastAPI()
    app.include_router(toolbox_router, prefix="/api/agent")
    client = TestClient(app)

    listed = client.get("/api/agent/toolbox")
    assert listed.status_code == 200 and listed.json()["success"] is True
    assert {item["slug"] for item in listed.json()["data"]} >= {"calculator"}

    schemas = client.get("/api/agent/toolbox/schema")
    assert schemas.status_code == 200
    assert {item["function"]["name"] for item in schemas.json()["data"]} >= {"calculator"}

    called = client.post(
        "/api/agent/toolbox/call",
        json={"name": "calculator", "arguments": {"expression": "1+1"}},
    )
    assert called.status_code == 200 and called.json()["result"]["value"] == 2

    missing = client.post("/api/agent/toolbox/call", json={"name": "not_exist", "arguments": {}})
    assert missing.status_code == 404

    servers = client.get("/api/agent/toolbox/mcp")
    assert servers.status_code == 200
    assert BUILTIN_SERVERS <= {item["slug"] for item in servers.json()["data"]["servers"]}
    print("[PASS] HTTP 接口（清单 / schema / 调用 / MCP 清单）OK")


def main():
    test_builtin_tools_registered()
    test_builtin_tools_behaviour()
    test_local_tool_call()
    test_tool_schema_export()
    test_run_tool_calls()
    test_mcp_server_management()
    test_mcp_definition_resolution()
    test_mcp_tools_registration_and_call()
    test_http_routes()
    # 自检工具已清掉，剩下的都是内置小工具
    assert {item["slug"] for item in list_tools()} == BUILTIN_TOOLS, list_tools()
    print("\n工具箱自检全部通过 ✅")


if __name__ == "__main__":
    main()
