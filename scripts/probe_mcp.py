"""通用 MCP 探针：列工具 / 看 schema / 调一次工具，用来对接新厂商。

新接一个 MCP 时，最先要拿到三样东西：服务端有哪些工具、工具的入参 schema、
真实返回长什么样。这个脚本就是干这个的——不依赖 builtin.py 的声明，
地址和密钥从命令行给，方便在配置落库之前先摸清接口。

用法
----
    # 1) 列工具 + 入参 schema
    PYTHONPATH=. python scripts/probe_mcp.py --url https://mcp.finchina.com/finchina-data-mcp-server/mcp \
        --key ak_xxx --list

    # 2) 调一次工具（参数是 JSON 字符串）
    PYTHONPATH=. python scripts/probe_mcp.py --url https://.../mcp --key ak_xxx \
        --call query_enterprise_basic_info --args '{}'

    # 3) 财汇这种两步式：先拿子工具目录，再用 execute_tool 执行
    PYTHONPATH=. python scripts/probe_mcp.py --url https://.../mcp --key ak_xxx \
        --call execute_tool \
        --args '{"tool_name":"filter_companies_by_basic_info","arguments":{"target_company":["贵州茅台"]}}'

参数也可以用环境变量给：MCP_PROBE_URL / MCP_PROBE_KEY。
密钥不想写进命令行时，用 --key-env 指定 .env 里的变量名（例如 --key-env YJT_API_KEY）。
"""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.mcp.client import MCPServerSpec, call_tool, describe_tools  # noqa: E402


def _key_from_env(name: str) -> str:
    """从 .env / 环境变量里取密钥，避免把 key 打在命令行里（会进 history）。"""
    if not name:
        return ""
    from dotenv import dotenv_values

    return os.getenv(name) or (dotenv_values(Path(__file__).resolve().parents[1] / ".env").get(name) or "")


def _print_tools(tools: list) -> None:
    if not tools:
        print("（没探到工具：地址或密钥不对，或服务端不支持 tools/list）")
        return
    print(f"共 {len(tools)} 个工具：")
    for tool in tools:
        schema = json.dumps(tool.get("inputSchema"), ensure_ascii=False)
        print(f"\n- {tool.get('name')}")
        if tool.get("description"):
            print(f"  说明：{tool['description'][:200]}")
        print(f"  入参：{schema[:800]}")


def main() -> None:
    parser = argparse.ArgumentParser(description="通用 MCP 探针")
    parser.add_argument("--url", default=os.getenv("MCP_PROBE_URL", ""), help="MCP 地址")
    parser.add_argument("--key", default=os.getenv("MCP_PROBE_KEY", ""), help="x-api-key")
    parser.add_argument("--key-env", default="", help="从 .env 读密钥的变量名，如 YJT_API_KEY")
    parser.add_argument("--auth-header", default="x-api-key", help="鉴权头名称")
    parser.add_argument("--transport", default="streamable_http", help="streamable_http / sse")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--list", action="store_true", help="列出工具 + 入参 schema")
    parser.add_argument("--call", default="", help="要调用的工具名")
    parser.add_argument("--args", default="{}", help="调用参数（JSON 字符串）")
    args = parser.parse_args()

    if not args.url:
        raise SystemExit("缺少 --url（或环境变量 MCP_PROBE_URL）")
    key = args.key or _key_from_env(args.key_env)
    spec = MCPServerSpec(
        name="probe",
        url=args.url,
        api_key=key or None,
        auth_header=args.auth_header,
        transport=args.transport,
        timeout=args.timeout,
    )
    print(f"地址：{spec.url}")
    print(f"鉴权：{spec.auth_header} {'（已带密钥）' if spec.api_key else '（无密钥）'}")

    if args.list or not args.call:
        print("\n=== tools/list ===")
        _print_tools(describe_tools(spec))

    if not args.call:
        return

    try:
        arguments = json.loads(args.args)
    except ValueError as exc:
        raise SystemExit(f"--args 不是合法 JSON：{exc}") from exc

    print(f"\n=== call {args.call} ===")
    print("参数：" + json.dumps(arguments, ensure_ascii=False)[:500])
    try:
        raw = call_tool(spec, args.call, arguments, required=False)
    except Exception as exc:
        print(f"调用异常：{type(exc).__name__}: {exc}")
        return
    if not raw:
        print("没有拿到结果（连接失败 / 服务端报错，原因见上面日志）")
        return
    print("返回（截断 3000 字）：")
    print(json.dumps(raw, ensure_ascii=False, indent=2, default=str)[:3000])


if __name__ == "__main__":
    main()
