"""检索指标：Recall@K / Hit@K / MRR@K / nDCG@K（纯函数，可离线单测）。

四个指标的分工（别混用）：

    Recall@K   金标里有几条被召回 —— 衡量"覆盖率"，金标多的时候天然偏低
    Hit@K      前 K 条里有没有**任意一条**金标 —— 衡量"能不能找到"，最宽容
    MRR@K      第一条金标排在第几位的倒数 —— 衡量"排得靠不靠前"
    nDCG@K     带位置折损的排序质量 —— 对"金标排后面"的惩罚比 MRR 平滑

分面型问题（如"某期晨报的重点客户有哪些"）金标天然是多条，
Recall@K 的上限就是 K/|gold|，所以这类问题要看 **Hit@K 和 MRR@K**；
报告里也按题型拆开看，避免总体指标被题型结构带偏。
"""
from __future__ import annotations

import math
from typing import Iterable, Sequence


def _dedup_keep_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def recall_at_k(retrieved: Sequence[str], gold: set[str], k: int) -> float:
    """|前K条 ∩ 金标| / |金标|。金标为空时返回 0.0。"""
    if not gold or k <= 0:
        return 0.0
    top = _dedup_keep_order(retrieved)[:k]
    return len(set(top) & gold) / len(gold)


def hit_at_k(retrieved: Sequence[str], gold: set[str], k: int) -> float:
    """前 K 条里是否命中任意金标（1.0 / 0.0）。"""
    if not gold or k <= 0:
        return 0.0
    top = _dedup_keep_order(retrieved)[:k]
    return 1.0 if set(top) & gold else 0.0


def mrr_at_k(retrieved: Sequence[str], gold: set[str], k: int) -> float:
    """第一条金标位置的倒数；前 K 条都没有则 0。"""
    if not gold or k <= 0:
        return 0.0
    for rank, key in enumerate(_dedup_keep_order(retrieved)[:k], start=1):
        if key in gold:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(retrieved: Sequence[str], gold: set[str], k: int) -> float:
    """二值相关度的 nDCG@K。"""
    if not gold or k <= 0:
        return 0.0
    top = _dedup_keep_order(retrieved)[:k]
    dcg = 0.0
    for rank, key in enumerate(top, start=1):
        if key in gold:
            dcg += 1.0 / math.log2(rank + 1)
    ideal_hits = min(len(gold), k)
    idcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
    return dcg / idcg if idcg > 0 else 0.0


def evaluate_retrieval(
    retrieved: Sequence[str],
    gold: set[str],
    ks: Sequence[int] = (5, 10),
) -> dict:
    """单题检索指标。"""
    result: dict[str, float] = {}
    for k in ks:
        result[f"recall@{k}"] = recall_at_k(retrieved, gold, k)
        result[f"hit@{k}"] = hit_at_k(retrieved, gold, k)
        result[f"ndcg@{k}"] = ndcg_at_k(retrieved, gold, k)
    result["mrr@10"] = mrr_at_k(retrieved, gold, 10)
    result["gold_size"] = float(len(gold))
    return result


def mean(values: Sequence[float]) -> float:
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else 0.0


def _optional_mean(rows: Sequence[dict], key: str) -> float | None:
    values = [row.get(key) for row in rows if row.get(key) is not None]
    return mean(values) if values else None


def aggregate(
    records: Sequence[dict],
    ks: Sequence[int] = (5, 10),
    group_key: str = "question_type",
) -> dict:
    """把逐题结果聚合成总体 + 分题型指标。

    :param records: 每条至少包含 retrieved_keys / gold_keys / question_type，
                    以及可选的 faithfulness / answer_relevancy
    """
    metric_names = (
        [f"recall@{k}" for k in ks]
        + [f"hit@{k}" for k in ks]
        + [f"ndcg@{k}" for k in ks]
        + ["mrr@10"]
    )

    per_question: list[dict] = []
    for record in records:
        row = dict(record)
        row.update(
            evaluate_retrieval(
                record.get("retrieved_keys") or [],
                set(record.get("gold_keys") or []),
                ks,
            )
        )
        per_question.append(row)

    overall: dict = {name: mean([row[name] for row in per_question]) for name in metric_names}
    # 生成层指标：没跑生成/判分时为 None，不参与平均
    overall["faithfulness"] = _optional_mean(per_question, "faithfulness")
    overall["answer_relevancy"] = _optional_mean(per_question, "answer_relevancy")
    overall["n_questions"] = len(per_question)
    overall["avg_candidates"] = mean(
        [len(row.get("retrieved_keys") or []) for row in per_question]
    )

    by_type: dict[str, list[dict]] = {}
    for row in per_question:
        by_type.setdefault(row.get(group_key) or "unknown", []).append(row)
    overall[f"by_{group_key}"] = {
        name: {
            **{metric: mean([r[metric] for r in rows]) for metric in metric_names},
            "faithfulness": _optional_mean(rows, "faithfulness"),
            "answer_relevancy": _optional_mean(rows, "answer_relevancy"),
            "n_questions": len(rows),
        }
        for name, rows in sorted(by_type.items())
    }
    return {"overall": overall, "per_question": per_question}
