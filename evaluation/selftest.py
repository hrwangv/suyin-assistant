"""离线自检：不联网、不调模型、不连 Qdrant，验证评测代码本身是对的。

跑之前不用配 .env。正式评测前先过一遍这个，能挡掉「指标算错 / 金标匹配错 /
消融层级不单调」这类会让人白跑几小时的问题。

运行：PYTHONPATH=. python -m evaluation.selftest
"""
from __future__ import annotations

import math

from evaluation.config import LEVELS, SUITES, resolve_levels
from evaluation.dataset import (
    EvalItem,
    GoldChunk,
    content_hash,
    match_payload,
    payload_keys,
    validate,
)
from evaluation.metrics import hit_at_k, mrr_at_k, ndcg_at_k, recall_at_k
from evaluation.report import build_level_row, build_markdown

passed = 0
failed: list[str] = []


def check(name: str, actual, expected, tol: float = 1e-6):
    global passed
    if isinstance(expected, float) and isinstance(actual, (int, float)):
        ok = abs(actual - expected) <= tol
    else:
        ok = actual == expected
    if ok:
        passed += 1
    else:
        failed.append(f"{name}\n     期望：{expected}\n     实际：{actual}")


def test_metrics():
    retrieved = ["A", "B", "C", "D", "E", "F"]
    gold = {"C", "F"}
    check("recall@5", recall_at_k(retrieved, gold, 5), 0.5)
    check("recall@10", recall_at_k(retrieved, gold, 10), 1.0)
    check("hit@5", hit_at_k(retrieved, gold, 5), 1.0)
    check("hit@2（前两条都没命中）", hit_at_k(retrieved, gold, 2), 0.0)
    check("mrr@10（第一个金标在第3位）", mrr_at_k(retrieved, gold, 10), 1 / 3)
    ideal = 1 / math.log2(2) + 1 / math.log2(3)
    check("ndcg@5", ndcg_at_k(retrieved, gold, 5), (1 / math.log2(4)) / ideal, tol=1e-9)
    check("空金标 recall", recall_at_k(retrieved, set(), 5), 0.0)
    check("无命中 mrr", mrr_at_k(["X", "Y"], gold, 10), 0.0)
    check("重复项去重", recall_at_k(["C", "C", "C"], gold, 5), 0.5)


def test_gold_matching():
    payload = {
        "content": "协创数据拟申请800亿融资租赁额度",
        "file_title": "经营晨报 20260410",
        "title": "## 协创数据：2026年拟申请800亿融资租赁额度",
        "part": 1,
        "chunk_id": "",
    }
    keys = payload_keys(payload)
    check("payload key 数量（无 chunk_id 时为 2）", len(keys), 2)
    check("payload 首个 key 是内容哈希", keys[0].startswith("hash:"), True)

    gold_hash = content_hash(payload["content"])
    matched = match_payload(payload, {f"hash:{gold_hash}"}, "miss:0")
    check("按内容哈希匹配", matched, f"hash:{gold_hash}")
    check("未命中返回占位符", match_payload(payload, {"hash:deadbeef"}, "miss:7"), "miss:7")

    with_id = dict(payload, chunk_id="cid-123")
    check("有 chunk_id 时优先匹配", payload_keys(with_id)[0], "cid:cid-123")


def test_dataset_validation():
    good = EvalItem(
        id="q0001",
        question="测试问题？",
        gold=[GoldChunk(content_hash="abc", file_title="f", title="t", part=1)],
    )
    check("正常条目无告警", validate([good]), [])
    bad = EvalItem(id="q0002", question="", gold=[])
    problems = validate([bad])
    check("能发现空问题", any("缺少 question" in p for p in problems), True)
    check("能发现空金标", any("没有金标" in p for p in problems), True)


def test_levels_are_monotonic():
    """消融层级必须只增不减，否则增益无法归因。"""
    flags = (
        "query_rewrite",
        "hyde",
        "hybrid_retrieval",
        "rrf_fusion",
        "metadata_filter",
        "rerank",
    )
    previous = {flag: False for flag in flags}
    for level in LEVELS:
        current = {flag: getattr(level, flag) for flag in flags}
        removed = [flag for flag in flags if previous[flag] and not current[flag]]
        check(f"{level.level_id} 不应回退能力", removed, [])
        if level.level_id != "baseline":
            added = [flag for flag in flags if current[flag] and not previous[flag]]
            check(f"{level.level_id} 应新增能力", len(added) >= 1, True)
        previous = current

    all_ids = {level.level_id for level in LEVELS}
    check(
        "suite 引用合法",
        all(level_id in all_ids for ids in SUITES.values() for level_id in ids),
        True,
    )
    check(
        "ablation_ladder 覆盖全部层级",
        len(resolve_levels(None, "ablation_ladder")),
        len(LEVELS),
    )


def test_report_rendering():
    rows = [
        build_level_row(
            "baseline",
            "Baseline",
            "",
            {
                "recall@5": 0.4,
                "recall@10": 0.5,
                "mrr@10": 0.3,
                "faithfulness": 0.7,
                "answer_relevancy": 0.6,
                "n_questions": 2,
                "avg_candidates": 20,
                "avg_latency_ms": 1200,
            },
        ),
        build_level_row(
            "full",
            "+ Rerank",
            "",
            {
                "recall@5": 0.6,
                "recall@10": 0.7,
                "mrr@10": 0.5,
                "faithfulness": 0.9,
                "answer_relevancy": 0.8,
                "n_questions": 2,
                "avg_candidates": 12,
                "avg_latency_ms": 1600,
            },
        ),
    ]
    markdown = build_markdown(rows, title="自检", notes=["note"])
    check("报告包含主表", "## 主表" in markdown, True)
    check("报告包含增益列", "Δ Recall@10" in markdown, True)
    check("报告包含基准标记", "基准" in markdown, True)
    check("报告包含说明", "note" in markdown, True)


def main() -> None:
    test_metrics()
    test_gold_matching()
    test_dataset_validation()
    test_levels_are_monotonic()
    test_report_rendering()

    print(f"\n通过 {passed} 项，失败 {len(failed)} 项")
    for item in failed:
        print("  ✗", item)
    if failed:
        raise SystemExit(1)
    print("✅ evaluation 自检全部通过")


if __name__ == "__main__":
    main()
