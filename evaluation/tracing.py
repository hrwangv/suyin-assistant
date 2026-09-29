"""评测侧的 Langfuse 辅助：把「一次运行 / 一个层级 / 一道题」映射成 session / tag / trace。

映射口径（在 Langfuse UI 里按这个找）：

    一次运行（run_id，形如 20260927_101530__ablation_ladder）  → session_id
    一个消融层级（baseline / full / …）                        → tag: level:<id>
    评测套件（ablation_ladder / custom / agent-all …）          → tag: suite:<name>
    一道题                                                     → 一条 trace
    逐题指标（recall@5 / mrr@10 / faithfulness / …）            → 该 trace 上的 score

底层全部走 `app.core.tracing`，未配置 Langfuse 时整套逻辑自动退化为 no-op，
评测流程与接入前逐字节一致（唯一差别是多几个函数调用）。
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from app.core import tracing as core
from evaluation import metrics
from evaluation.config import EVAL_KS

# 回写成 score 的逐题指标（名字与报告表一致，方便对齐）
_RETRIEVAL_SCORE_METRICS: tuple[str, ...] = tuple(
    [f"recall@{k}" for k in EVAL_KS]
    + [f"hit@{k}" for k in EVAL_KS]
    + ["mrr@10"]
)


def enabled() -> bool:
    """追踪是否生效（未配置密钥时为 False）。"""
    return core.is_enabled()


def flush() -> None:
    """评测收尾：把缓冲区推给 Langfuse。"""
    core.flush()


@contextmanager
def question_scope(
    *,
    run_id: str,
    suite: str,
    level_id: str,
    question: str,
    item_id: str = "",
    question_type: str = "",
    difficulty: str = "",
    requires_filter: bool | None = None,
) -> Iterator[Any]:
    """一道题 = 一条 trace；trace 名字带上层级，便于按层级筛。"""
    metadata = {
        "suite": suite,
        "level": level_id,
        "item_id": item_id,
        "question_type": question_type,
        "difficulty": difficulty,
        "requires_filter": requires_filter,
    }
    with core.trace_scope(
        f"rag-question:{level_id}",
        session_id=run_id or None,
        tags=["eval", f"suite:{suite or 'custom'}", f"level:{level_id}"],
        metadata={key: value for key, value in metadata.items() if value not in (None, "")},
        input=question,
    ) as span:
        yield span


@contextmanager
def case_scope(
    *,
    run_id: str,
    suite: str,
    case_id: str,
    input: Any = None,
    metadata: dict | None = None,
) -> Iterator[Any]:
    """Agent 评测：一个用例 = 一条 trace。"""
    with core.trace_scope(
        f"agent-case:{suite}",
        session_id=run_id or None,
        tags=["eval", f"suite:{suite}"],
        metadata={**(metadata or {}), "case_id": case_id},
        input=input,
    ) as span:
        yield span


def score(name: str, value: float | int | bool | str | None, *, comment: str | None = None) -> None:
    """写一个 score 到当前 trace；值为 None 时跳过（例如没跑判分）。"""
    if value is None:
        return
    core.score_current_trace(name, value, comment=comment)


def score_record(record: dict) -> None:
    """把一条逐题记录里的指标写回 trace。

    - 检索指标用与报告同一份实现（metrics.evaluate_retrieval）现算，避免口径漂移；
    - faithfulness / answer_relevancy 没跑判分时为 None，直接跳过。
    """
    retrieval = metrics.evaluate_retrieval(
        record.get("retrieved_keys") or [],
        set(record.get("gold_keys") or []),
        EVAL_KS,
    )
    for name in _RETRIEVAL_SCORE_METRICS:
        if name in retrieval:
            score(name, retrieval[name])

    score("faithfulness", record.get("faithfulness"))
    score("answer_relevancy", record.get("answer_relevancy"))

    latency = record.get("latency_ms")
    if latency is not None:
        score("retrieval_latency_ms", latency)
    answer_latency = record.get("answer_latency_ms")
    if answer_latency is not None:
        score("answer_latency_ms", answer_latency)


def score_level(overall: dict, *, level_id: str) -> None:
    """层级汇总指标也挂一条 trace（评测收尾时对空 span 用，便于按 level 看总览）。"""
    with core.trace_scope(
        f"rag-level:{level_id}",
        tags=["eval", "level-summary", f"level:{level_id}"],
        metadata={"level": level_id},
    ):
        for name in (
            "recall@5",
            "recall@10",
            "mrr@10",
            "ndcg@10",
            "faithfulness",
            "answer_relevancy",
            "avg_latency_ms",
        ):
            value = overall.get(name)
            if value is not None:
                score(name, value, comment="层级汇总（全部题目平均）")
