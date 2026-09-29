"""单独跑 Business Application 子图：不经过主 Agent、不经过 Supervisor。

联调时用它区分"是主 Agent 的问题还是子图的问题"——子图能跑通就说明
企业 MCP、模板、渲染都没事，问题在 Supervisor 的路由或提示词。

    START → 解析请求 → 查企业 ─┬─选中──→ 准备数据 ─┬─就绪─→ 确认(interrupt②) ─→ 渲染 → END
                              ├─多候选→ 选主体(interrupt①) ─┘        └─补字段─┘
                              └─没查到────────────────────────→ 收口（反问用户）

用法
----
    # 1) 离线跑通（不调模型、不调 MCP）：用给定企业名伪造一个候选
    PYTHONPATH=. python scripts/run_application_subgraph.py --offline \
        --company "江苏恒瑞医药股份有限公司" --province 江苏省

    # 2) 真实链路（调 LLM 解析 + 调财汇 MCP）
    PYTHONPATH=. python scripts/run_application_subgraph.py \
        --request "帮我生成贵州茅台酒股份有限公司的业务申请书"

    # 3) 续跑 HITL（确认 / 选序号 / 补字段都在这一步）
    PYTHONPATH=. python scripts/run_application_subgraph.py --thread t1 --resume "确认"
    PYTHONPATH=. python scripts/run_application_subgraph.py --thread t1 --resume "1"

    # 4) 看结构 / 看全量状态 / 看检查点历史
    PYTHONPATH=. python scripts/run_application_subgraph.py --mermaid
    PYTHONPATH=. python scripts/run_application_subgraph.py --thread t1 --json --history

    # 5) 想跨进程复用同一个 thread，换成持久化检查点
    PYTHONPATH=. python scripts/run_application_subgraph.py --thread t1 --checkpoint sqlite:output/agent_state.db ...
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langgraph.types import Command  # noqa: E402

from app.agent.checkpoint import build_checkpointer  # noqa: E402
from app.agent.subgraphs import business_application as ba  # noqa: E402


def _install_offline_stubs(company: str, fields: dict):
    """离线模式：模型调用返回 None（走规则兜底），企业检索直接给一个候选。"""
    originals = {
        "json_completion": ba.json_completion,
        "search_company": ba.company_service.search_company,
        "company_mcp_configured": ba.company_service.company_mcp_configured,
    }
    candidate = {
        "company_name": company,
        "unified_social_credit_code": fields.get("unified_social_credit_code"),
        "registered_address": fields.get("registered_address"),
        "province": fields.get("province"),
        "city": fields.get("city"),
        "legal_person": fields.get("legal_person"),
        "enterprise_nature": fields.get("enterprise_nature"),
        "registered_capital": fields.get("registered_capital"),
    }
    candidate = {k: v for k, v in candidate.items() if v}

    ba.json_completion = lambda *a, **k: None
    ba.company_service.company_mcp_configured = lambda: True
    ba.company_service.search_company = lambda query, limit=None: {
        "query": query,
        "candidates": [candidate],
        "source": "offline_stub",
    }
    return originals


def _restore(originals: dict) -> None:
    ba.json_completion = originals["json_completion"]
    ba.company_service.search_company = originals["search_company"]
    ba.company_service.company_mcp_configured = originals["company_mcp_configured"]


def _print_summary(state: dict) -> None:
    print("\n=== 本轮结论 ===")
    print(f"  阶段       : {state.get('application_phase')}")
    print(f"  企业查询   : {state.get('company_query')}")
    selected = state.get("selected_company") or {}
    print(f"  选中主体   : {selected.get('company_name') or '（未选中）'}")
    if state.get("company_candidates") and not selected:
        names = [c.get("company_name") for c in state["company_candidates"][:5]]
        print(f"  候选       : {names}")
    print(f"  模板       : {state.get('template_type') or '（未选）'}")
    if state.get("missing_fields"):
        print(f"  缺字段     : {state['missing_fields']}")
    if state.get("error"):
        print(f"  错误       : {state['error']}")
    if state.get("generated_file_path"):
        print(f"  产物       : {state['generated_file_path']}")
        print(f"  下载地址   : {state.get('generated_file_url')}")

    data = state.get("application_data") or {}
    if data:
        print("\n=== application_data ===")
        for key, value in data.items():
            if value not in (None, "", [], {}):
                print(f"  {key:<24}: {value}")

    card = state.get("agent_result") or {}
    if card:
        print("\n=== 上行给主 Agent 的 AgentResult ===")
        print(f"  status     : {card.get('status')}")
        print(f"  message    : {card.get('message')}")


def main() -> None:
    parser = argparse.ArgumentParser(description="单独运行 Business Application 子图")
    parser.add_argument("--thread", default="app-cli", help="thread_id（多轮 / HITL 靠它）")
    parser.add_argument("--request", default="", help="用户原话；不填则用 --company 拼一句")
    parser.add_argument("--company", default="江苏恒瑞医药股份有限公司", help="企业名（离线模式用）")
    parser.add_argument("--province", default="江苏省")
    parser.add_argument("--city", default="")
    parser.add_argument("--credit-code", default="", dest="credit_code")
    parser.add_argument("--address", default="")
    parser.add_argument("--legal-person", default="", dest="legal_person")
    parser.add_argument("--enterprise-nature", default="", dest="enterprise_nature")
    parser.add_argument("--registered-capital", default="", dest="registered_capital")
    parser.add_argument("--offline", action="store_true", help="不调模型、不调 MCP")
    parser.add_argument("--resume", default="", help="HITL 续跑值（确认 / 序号 / 补充内容）")
    parser.add_argument("--checkpoint", default="", help="memory / sqlite:<路径>")
    parser.add_argument("--json", action="store_true", help="打印完整 State")
    parser.add_argument("--history", action="store_true", help="打印检查点历史")
    parser.add_argument("--mermaid", action="store_true", help="打印子图结构")
    args = parser.parse_args()

    app = ba.build_application_app(build_checkpointer(args.checkpoint or None))
    config = {"configurable": {"thread_id": args.thread}}

    if args.mermaid:
        print(app.get_graph().draw_mermaid())

    fields = {
        "unified_social_credit_code": args.credit_code,
        "registered_address": args.address,
        "province": args.province,
        "city": args.city,
        "legal_person": args.legal_person,
        "enterprise_nature": args.enterprise_nature,
        "registered_capital": args.registered_capital,
    }
    originals = _install_offline_stubs(args.company, fields) if args.offline else None
    if args.offline:
        print(f"[离线模式] 模型调用与 MCP 检索都走桩；企业={args.company}")
    try:
        if args.resume:
            payload = Command(resume=args.resume)
        else:
            request = args.request or f"帮我生成{args.company}的业务申请书"
            payload = {
                "session_id": args.thread,
                "thread_id": args.thread,
                "is_stream": False,
                "request_text": request,
            }
            if args.offline:
                # 离线模式下 node_parse_application_request 抽不出企业名（模型是桩），
                # 直接把企业名塞进 State——node_resolve_company 优先读它
                payload["company_query"] = args.company
        state = app.invoke(payload, config)
    finally:
        if originals:
            _restore(originals)

    _print_summary(state)

    if state.get("__interrupt__"):
        print("\n=== 停在这里了（HITL）===")
        for item in state["__interrupt__"]:
            value = getattr(item, "value", item)
            print("  " + json.dumps(value, ensure_ascii=False, default=str)[:400])
        print(f"  续跑：--thread {args.thread} --resume '确认'  或  --resume '1'")

    if args.json:
        printable = {k: v for k, v in state.items() if k != "__interrupt__"}
        print("\n=== 完整 State ===")
        print(json.dumps(printable, ensure_ascii=False, indent=2, default=str)[:4000])

    if args.history:
        print("\n=== 检查点历史（新→旧）===")
        for snapshot in app.get_state_history(config):
            keys = sorted(k for k in snapshot.values if k != "__interrupt__")
            print(f"  step={snapshot.metadata.get('step')} next={snapshot.next} 写入键={keys[:8]}")


if __name__ == "__main__":
    main()
