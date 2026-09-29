"""Agent 评测入口（规范第 71 节）。

三个套件：
    routing   Main Agent 一级路由（Intent / Tool Selection Accuracy）
    company   企业主体匹配（Exact / Fuzzy / Ambiguous Detection）
    document  文档理解（Document Type + Field Extraction Accuracy）

用法（项目根目录）：
    PYTHONPATH=. python -m evaluation.agent.run_agent_eval --suite all
    PYTHONPATH=. python -m evaluation.agent.run_agent_eval --suite routing --offline
    PYTHONPATH=. python -m evaluation.agent.run_agent_eval --suite company

--offline 会把大模型调用替换成规则兜底（Supervisor 的 fallback / 文档正则抽取），
用来在没网、没配模型时跑通评测流程，同时得到一个「规则基线」分数。
"""
import argparse
import json
import os
from datetime import datetime
from pathlib import Path

from app.agent.schemas.company import CompanyCandidate
from app.agent.schemas.document import ACTIONS_BY_DOCUMENT_TYPE
from app.agent.services import company_service, document_service
from app.core import tracing as core
from evaluation.tracing import case_scope, flush

DATASET_DIR = Path(__file__).parent / "datasets"
RESULT_DIR = Path(__file__).parent / "results"

# 本次进程的追踪会话 id（main 里按运行时间戳统一设置）
RUN_ID = ""


def _load(name: str) -> dict:
    return json.loads((DATASET_DIR / f"{name}.json").read_text(encoding="utf-8"))


# --------------------------- Routing ---------------------------


def _build_routing_state(case: dict) -> dict:
    """按数据集里的 State 描述构造一个最小可用的 Agent State。"""
    spec = case.get("state") or {}
    state: dict = {
        "session_id": "eval-routing",
        "thread_id": "eval-routing",
        "request_text": case["request"],
        "attachments": [],
        "history": [],
        "supervisor_turns": 0,
    }
    if spec.get("has_attachment"):
        state["attachments"] = [
            {"file_id": "eval-file", "filename": "scan.png", "mime_type": "image/png"}
        ]
    if spec.get("document_type"):
        document_type = spec["document_type"]
        state["document_analysis"] = {
            "document_type": document_type,
            "confidence": 0.9,
            "candidate_addresses": spec.get("candidate_addresses") or [],
            "available_actions": ACTIONS_BY_DOCUMENT_TYPE.get(
                document_type, ACTIONS_BY_DOCUMENT_TYPE["unknown"]
            ),
            "structured_fields": spec.get("structured_fields") or {},
            "summary": "（评测用摘要）",
            "error": None,
        }
    if spec.get("retrieved"):
        state["retrieved_documents"] = [
            {"title": "评测用证据", "text": "评测用证据内容", "score": 0.9, "source": "local"}
        ]
    return state


def run_routing(offline: bool, limit: int | None = None) -> tuple[dict, list[dict]]:
    from app.agent import supervisor as supervisor_module
    from app.agent.services.document_service import infer_action_from_task

    # 评测不读记忆库：既提速，也保证可复现
    supervisor_module.recent_history = lambda state, limit=6: []

    cases = _load("intent_routing")["cases"]
    if limit:
        cases = cases[:limit]

    rows = []
    for case in cases:
        with case_scope(
            run_id=RUN_ID,
            suite="routing",
            case_id=case["id"],
            input=case["request"],
            metadata={"offline": offline, "expected": case["expected"]},
        ):
            state = _build_routing_state(case)
            if offline:
                decision = supervisor_module.fallback_decision(state)
            else:
                decision = supervisor_module.decide(state)
            action_ok = decision.next_action == case["expected"]
            action_detail_ok = True
            # 契约改造后 Supervisor 不再产出动作名（document_action），只产出 task 文本。
            # 这里做**评测专用的影子映射**：把 task 归一成动作再与人工标注比对。
            # 用的是子 Agent 自己那套规则（infer_action_from_task），好处是与线上口径一致；
            # 局限是"task 写得含糊、但规则恰好猜对"测不出来——需要语义级判断时改用
            # evaluation/judge.py 的 LLM-as-judge。
            predicted_action = infer_action_from_task(decision.task)
            if case.get("expected_document_action"):
                action_detail_ok = predicted_action == case["expected_document_action"]
            rows.append(
                {
                    "id": case["id"],
                    "request": case["request"],
                    "expected": case["expected"],
                    "predicted": decision.next_action,
                    "task": decision.task,
                    "expected_document_action": case.get("expected_document_action"),
                    "predicted_document_action": predicted_action,
                    "intent_ok": action_ok,
                    "document_action_ok": action_detail_ok,
                    "reason": decision.reason,
                }
            )

    total = len(rows) or 1
    intent_accuracy = sum(row["intent_ok"] for row in rows) / total
    detail_rows = [row for row in rows if row["expected_document_action"]]
    document_action_accuracy = (
        sum(row["document_action_ok"] for row in detail_rows) / len(detail_rows)
        if detail_rows
        else None
    )
    per_class = {}
    for expected in sorted({row["expected"] for row in rows}):
        subset = [row for row in rows if row["expected"] == expected]
        per_class[expected] = round(sum(row["intent_ok"] for row in subset) / len(subset), 4)

    metrics = {
        "suite": "routing",
        "offline": offline,
        "cases": len(rows),
        "intent_accuracy": round(intent_accuracy, 4),
        "document_action_accuracy": (
            round(document_action_accuracy, 4) if document_action_accuracy is not None else None
        ),
        "accuracy_by_expected_class": per_class,
    }
    return metrics, rows


# --------------------------- Company ---------------------------


def run_company(offline: bool = True, limit: int | None = None) -> tuple[dict, list[dict]]:
    cases = _load("company_resolution")["cases"]
    if limit:
        cases = cases[:limit]

    rows = []
    for case in cases:
        with case_scope(
            run_id=RUN_ID,
            suite="company",
            case_id=case["id"],
            input=case["query"],
            metadata={"expected_action": case["expected_action"]},
        ):
            candidates = [CompanyCandidate(**item) for item in case.get("candidates") or []]
            ranked = company_service.rank_by_name(case["query"], candidates)
            picked = company_service.pick_company(ranked, case["query"])
            if not ranked:
                action = "not_found"
            elif picked:
                action = "auto"
            else:
                action = "hitl"
            expected_action = case["expected_action"]
            action_ok = action == expected_action
            company_ok = True
            if case.get("expected_company"):
                company_ok = bool(picked) and picked.get("company_name") == case["expected_company"]
            rows.append(
                {
                    "id": case["id"],
                    "query": case["query"],
                    "expected_action": expected_action,
                    "predicted_action": action,
                    "expected_company": case.get("expected_company"),
                    "predicted_company": (picked or {}).get("company_name"),
                    "action_ok": action_ok,
                    "company_ok": company_ok,
                }
            )

    total = len(rows) or 1
    metrics = {
        "suite": "company",
        "offline": True,
        "cases": len(rows),
        "action_accuracy": round(sum(row["action_ok"] for row in rows) / total, 4),
        "company_accuracy": round(sum(row["company_ok"] for row in rows) / total, 4),
        "auto_match_accuracy": round(
            sum(row["company_ok"] for row in rows if row["expected_action"] == "auto")
            / max(len([row for row in rows if row["expected_action"] == "auto"]), 1),
            4,
        ),
        "ambiguous_detection_accuracy": round(
            sum(row["action_ok"] for row in rows if row["expected_action"] == "hitl")
            / max(len([row for row in rows if row["expected_action"] == "hitl"]), 1),
            4,
        ),
    }
    return metrics, rows


# --------------------------- Document ---------------------------

FIELD_KEYS = ["company_name", "unified_social_credit_code", "registered_address", "province"]


def run_document(offline: bool, limit: int | None = None) -> tuple[dict, list[dict]]:
    if offline:
        document_service.json_completion = lambda *a, **k: None
        document_service.text_completion = lambda *a, **k: ""

    cases = _load("document_extraction")["cases"]
    if limit:
        cases = cases[:limit]

    rows = []
    for case in cases:
        with case_scope(
            run_id=RUN_ID,
            suite="document",
            case_id=case["id"],
            input=case.get("text", "")[:500],
            metadata={"offline": offline, "expected_type": case["expected_type"]},
        ):
            text = case["text"]
            classification = document_service.classify_document(text)
            fields = document_service.extract_fields(text, classification.document_type)
            predicted = fields.model_dump(exclude_none=True)
            expected = case.get("expected") or {}
            field_results = {}
            for key in FIELD_KEYS:
                if key not in expected:
                    continue
                field_results[key] = _normalize(predicted.get(key)) == _normalize(expected[key])
            rows.append(
                {
                    "id": case["id"],
                    "expected_type": case["expected_type"],
                    "predicted_type": classification.document_type,
                    "type_ok": classification.document_type == case["expected_type"],
                    "field_results": field_results,
                    "predicted_fields": predicted,
                }
            )

    total = len(rows) or 1
    field_pairs = [
        (row["id"], key, value) for row in rows for key, value in row["field_results"].items()
    ]
    metrics = {
        "suite": "document",
        "offline": offline,
        "cases": len(rows),
        "type_accuracy": round(sum(row["type_ok"] for row in rows) / total, 4),
        "field_accuracy": (
            round(sum(1 for _, _, ok in field_pairs if ok) / len(field_pairs), 4)
            if field_pairs
            else None
        ),
        "field_accuracy_by_key": {
            key: round(
                sum(1 for _, k, ok in field_pairs if k == key and ok)
                / max(len([1 for _, k, _ in field_pairs if k == key]), 1),
                4,
            )
            for key in FIELD_KEYS
            if any(k == key for _, k, _ in field_pairs)
        },
    }
    return metrics, rows


def _normalize(value) -> str:
    return str(value or "").replace(" ", "").strip().upper()


def _score_suite(suite_metrics: dict) -> None:
    """套件级指标写成一条汇总 trace 的 score（未启用追踪时是 no-op）。"""
    suite = suite_metrics.get("suite", "unknown")
    with core.trace_scope(
        f"agent-suite:{suite}",
        session_id=RUN_ID or None,
        tags=["eval", f"suite:{suite}", "suite-summary"],
        metadata={"offline": suite_metrics.get("offline")},
    ):
        for key, value in suite_metrics.items():
            if key in {"suite", "cases", "accuracy_by_expected_class", "field_accuracy_by_key"}:
                continue
            if isinstance(value, (int, float, bool)) and value is not None:
                core.score_current_trace(key, value, comment="套件汇总")


# --------------------------- 输出 ---------------------------


def _write_results(results: list[tuple[dict, list[dict]]], tag: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = RESULT_DIR / f"{stamp}_{tag}"
    out_dir.mkdir(parents=True, exist_ok=True)

    metrics = {item[0]["suite"]: item[0] for item in results}
    (out_dir / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = ["# Agent 评测报告", "", f"运行时间：{stamp}", ""]
    for item in results:
        suite_metrics, rows = item
        lines += [f"## {suite_metrics['suite']}", "", f"样本数：{suite_metrics['cases']}", ""]
        for key, value in suite_metrics.items():
            if key in {"suite", "cases"}:
                continue
            lines.append(f"- {key}: {value}")
        lines += ["", "| id | 期望 | 预测 | 结果 |", "| --- | --- | --- | --- |"]
        for row in rows:
            expected = row.get("expected") or row.get("expected_action") or row.get("expected_type")
            predicted = (
                row.get("predicted") or row.get("predicted_action") or row.get("predicted_type")
            )
            ok = row.get("intent_ok", row.get("action_ok", row.get("type_ok")))
            lines.append(f"| {row['id']} | {expected} | {predicted} | {'✅' if ok else '❌'} |")
        lines.append("")
        (out_dir / f"{suite_metrics['suite']}_cases.json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    (out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    return out_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="企业业务智能 Agent 评测")
    parser.add_argument(
        "--suite",
        default="all",
        choices=["all", "routing", "company", "document"],
        help="评测套件",
    )
    parser.add_argument("--offline", action="store_true", help="用规则兜底，不调大模型")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 条")
    parser.add_argument("--tag", default="", help="结果目录后缀")
    parser.add_argument(
        "--no-trace", action="store_true", help="本次运行不上报 Langfuse（临时关闭追踪）"
    )
    args = parser.parse_args()

    if args.no_trace:
        os.environ["LANGFUSE_ENABLED"] = "false"

    global RUN_ID
    RUN_ID = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}__agent_{args.suite}"

    results = []
    if args.suite in {"all", "routing"}:
        results.append(run_routing(args.offline, args.limit))
    if args.suite in {"all", "company"}:
        results.append(run_company(limit=args.limit))
    if args.suite in {"all", "document"}:
        results.append(run_document(args.offline, args.limit))

    out_dir = _write_results(results, args.tag or args.suite)
    # 套件级汇总也挂一条 trace，便于在 Langfuse 里按 suite 看总览
    for suite_metrics, _ in results:
        _score_suite(suite_metrics)
    flush()
    for suite_metrics, _ in results:
        print(json.dumps(suite_metrics, ensure_ascii=False, indent=2))
    print(f"\n报告已写入：{out_dir}")


if __name__ == "__main__":
    main()
