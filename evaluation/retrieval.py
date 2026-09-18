"""消融检索执行器：按实验层级复现线上检索链路（不改动线上代码）。

复用关系一览（这是本文件最重要的部分 —— 评测结论要能代表线上，就不能重写实现）：

    环节              复用的现有函数
    ----------------  --------------------------------------------------------------
    问题改写 + 过滤     node_item_name_confirm.step_3_analyze_query
    向量化             app.llm.qwen_embedding_utils.generate_embeddings
    混合检索           app.utils.qdrant_utils.rrf_hybrid_search（Qdrant 内置 RRF）
    HyDE               node_search_embedding_hyde.step_1_create_hyde_doc
    多路 RRF 融合      node_rrf.step_3_reciprocal_rank_fusion
    结构化过滤         retrieval.qdrant_filter_builder.build_qdrant_filter
    精排 + 断崖截断    app.llm.reranker_utils.text_rerank + node_rerank.step_3_topk_and_gap

唯一没有复用的是「答案生成」和「SSE / 记忆写入」——那两处有副作用
（写 MySQL/Redis、推 SSE 队列），评测不能污染线上记忆，见 answering.py 的说明。

离线冒烟：--offline-smoke 时改用本地词法检索（读 chunks.json），
只为验证评测流程能跑通，**不代表线上效果**，报告里会明确标注。
"""
from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field

from app.core.logger import logger
from app.conf.retrieval_config import retrieval_config
from app.llm.qwen_embedding_utils import generate_embeddings
from app.llm.reranker_utils import text_rerank
from app.rag.query_process.agent.nodes.node_item_name_confirm import step_3_analyze_query
from app.rag.query_process.agent.nodes.node_rerank import step_3_topk_and_gap
from app.rag.query_process.agent.nodes.node_rrf import step_3_reciprocal_rank_fusion
from app.rag.query_process.agent.nodes.node_search_embedding_hyde import step_1_create_hyde_doc
from app.rag.query_process.retrieval.qdrant_filter_builder import (
    build_qdrant_filter,
    describe_filters,
)
from app.utils.qdrant_utils import get_qdrant_client, rrf_hybrid_search
from evaluation.config import CANDIDATE_TOP_K, CHUNKS_COLLECTION, EvalLevel


@dataclass
class RetrievedChunk:
    """检索命中的一条 chunk（保留完整 payload 供金标匹配）。"""

    chunk_id: str = ""
    score: float = 0.0
    source: str = "main"
    payload: dict = field(default_factory=dict)

    @property
    def content(self) -> str:
        return str(self.payload.get("content") or "")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> "RetrievedChunk":
        return cls(
            chunk_id=str(raw.get("chunk_id") or ""),
            score=float(raw.get("score") or 0.0),
            source=str(raw.get("source") or "main"),
            payload=raw.get("payload") or {},
        )


@dataclass
class RetrievalResult:
    """一个层级对一条问题的检索结果。"""

    question: str
    level_id: str
    rewritten_query: str = ""
    filters: dict = field(default_factory=dict)
    filter_applied: bool = False
    fallback_relaxed: bool = False
    offline_smoke: bool = False
    candidates: list[RetrievedChunk] = field(default_factory=list)
    latency_ms: int = 0
    notes: str = ""

    def to_dict(self) -> dict:
        data = asdict(self)
        data["candidates"] = [chunk.to_dict() for chunk in self.candidates]
        return data

    @classmethod
    def from_dict(cls, raw: dict) -> "RetrievalResult":
        return cls(
            question=raw.get("question") or "",
            level_id=raw.get("level_id") or "",
            rewritten_query=raw.get("rewritten_query") or "",
            filters=raw.get("filters") or {},
            filter_applied=bool(raw.get("filter_applied")),
            fallback_relaxed=bool(raw.get("fallback_relaxed")),
            offline_smoke=bool(raw.get("offline_smoke")),
            candidates=[RetrievedChunk.from_dict(c) for c in raw.get("candidates") or []],
            latency_ms=int(raw.get("latency_ms") or 0),
            notes=raw.get("notes") or "",
        )


# ------------------------------------------------------------------ #
# 基础动作
# ------------------------------------------------------------------ #
def _embed(text: str, need_sparse: bool) -> tuple[list[float], dict | None]:
    result = generate_embeddings([text])
    dense = result["dense"][0]
    sparse = result["sparse"][0] if need_sparse else None
    return dense, sparse


def _points_to_chunks(points, source: str) -> list[dict]:
    """统一成 node_rrf.step_3_reciprocal_rank_fusion 需要的结构。"""
    return [
        {"id": str(point.id), "score": float(point.score), "payload": point.payload or {}}
        for point in points
    ]


def _dense_search(client, dense, limit: int, query_filter) -> list[dict]:
    """dense-only 检索（Baseline / +Rewrite 用的就是这一路）。"""
    response = client.query_points(
        collection_name=CHUNKS_COLLECTION,
        query=dense,
        using="dense",
        query_filter=query_filter,
        limit=limit,
        with_payload=True,
    )
    return _points_to_chunks(response.points or [], "main")


def _hybrid_search(client, dense, sparse, limit: int, query_filter) -> list[dict]:
    """dense+sparse 混合检索（复用线上函数，内部是 Qdrant 的 RRF 融合）。"""
    response = rrf_hybrid_search(
        client=client,
        collection_name=CHUNKS_COLLECTION,
        dense_vector=dense,
        sparse_vector=sparse or {},
        query_filter=query_filter,
        limit=limit,
        dense_limit=retrieval_config.prefetch_limit,
        sparse_limit=retrieval_config.prefetch_limit,
    )
    return _points_to_chunks(response.points or [], "main")


def _interleave_merge(main: list[dict], hyde: list[dict], top_k: int) -> list[dict]:
    """朴素轮询合并（L2 对照组：只加一路召回，先不上 RRF）。

    按排名交替取：main[0], hyde[0], main[1], hyde[1] ...
    这是"多路召回但不做加权融合"的最小实现，用来把「多一路」与「RRF 融合」两个变量拆开。
    """
    merged: list[dict] = []
    seen: set[str] = set()
    for index in range(max(len(main), len(hyde))):
        for source in (main, hyde):
            if index >= len(source):
                continue
            chunk = source[index]
            key = str(chunk.get("id") or (chunk.get("payload") or {}).get("chunk_id"))
            if key in seen:
                continue
            seen.add(key)
            merged.append(chunk)
            if len(merged) >= top_k:
                return merged
    return merged


def _run_paths(
    client,
    level: EvalLevel,
    dense: list[float],
    sparse: dict | None,
    hyde_dense: list[float] | None,
    hyde_sparse: dict | None,
    top_k: int,
    query_filter,
) -> tuple[list[dict], bool]:
    """跑主路（+ HyDE 路）并返回 (候选列表, 是否做了空结果放宽)。"""
    if level.hybrid_retrieval:
        main = _hybrid_search(client, dense, sparse, top_k, query_filter)
    else:
        main = _dense_search(client, dense, top_k, query_filter)

    hyde: list[dict] = []
    if level.hyde and hyde_dense is not None:
        if level.hybrid_retrieval:
            hyde = _hybrid_search(client, hyde_dense, hyde_sparse, top_k, query_filter)
        else:
            hyde = _dense_search(client, hyde_dense, top_k, query_filter)

    if not main and not hyde:
        return [], True

    if not hyde:
        return main[:top_k], False
    if level.rrf_fusion:
        # 复用线上 node_rrf 的加权 RRF 实现（k=60，权重当前 1.0/1.0）
        return step_3_reciprocal_rank_fusion([(main, 1.0), (hyde, 1.0)], top_k=top_k), False
    return _interleave_merge(main, hyde, top_k), False


def _to_retrieved(chunks: list[dict]) -> list[RetrievedChunk]:
    return [
        RetrievedChunk(
            chunk_id=str(chunk.get("id") or (chunk.get("payload") or {}).get("chunk_id") or ""),
            score=float(chunk.get("score") or 0.0),
            source=str(chunk.get("_source") or "main"),
            payload=chunk.get("payload") or {},
        )
        for chunk in chunks
    ]


def _apply_rerank(
    question: str,
    chunks: list[dict],
    top_k: int,
) -> list[dict]:
    """精排 + 断崖截断（复用线上函数）。

    top_n 传全部候选，与线上 node_rerank.step_2 保持一致：
    text_rerank 的 top_n 只决定返回条数，不决定打分范围，
    传全部才能让每条候选都拿到真实 relevance_score，
    再由 step_3_topk_and_gap 的「断崖 + 上限」决定最终保留几条。
    """
    if not chunks:
        return []
    docs = []
    for chunk in chunks:
        payload = chunk.get("payload") or {}
        docs.append(
            {
                "chunk_id": chunk.get("id"),
                "text": payload.get("content") or "",
                "title": payload.get("title") or "",
                "source": "local",
                "score": 0.0,
                "_payload": payload,
            }
        )
    results = text_rerank(question, [doc["text"] for doc in docs], top_n=len(docs))
    score_map = {item.index: item.relevance_score for item in results}
    for index, doc in enumerate(docs):
        doc["score"] = score_map.get(index, 0.0)
    docs.sort(key=lambda item: item["score"], reverse=True)
    # 复用线上断崖截断：绝对分差 0.5 / 相对分差 25%，上限 rerank_max_topk
    truncated = step_3_topk_and_gap(docs)
    return [
        {"id": doc.get("chunk_id"), "score": doc.get("score"), "payload": doc.get("_payload") or {}}
        for doc in truncated[:top_k]
    ]


# ------------------------------------------------------------------ #
# 离线冒烟用的本地词法检索（不联网，只验证评测流程）
# ------------------------------------------------------------------ #
_FILTER_CATEGORY_RE = re.compile("|".join(["重点要闻", "重点客户", "行业动态", "同业资讯", "宏观动态", "头部及域内企业要闻"]))
_FILTER_DOMAIN_RE = re.compile(r"([\u4e00-\u9fff]{2,6})领域")
_FILTER_MONTH_RE = re.compile(r"(\d{4})\D{0,3}(\d{1,2})\s*月")


def _offline_filter_hint(question: str) -> dict:
    hint: dict = {}
    matched = _FILTER_CATEGORY_RE.search(question)
    if matched:
        hint["category"] = matched.group(0)
    matched = _FILTER_DOMAIN_RE.search(question)
    if matched:
        hint["domain"] = matched.group(1)
    matched = _FILTER_MONTH_RE.search(question)
    if matched:
        hint["month"] = f"{int(matched.group(1)):04d}-{int(matched.group(2)):02d}"
    return hint


def _offline_score(question: str, content: str) -> float:
    """字符 bigram 重合度，纯本地、确定性，够用来冒烟。"""
    query_grams = {question[i : i + 2] for i in range(max(0, len(question) - 1))}
    doc_grams = {content[i : i + 2] for i in range(max(0, len(content) - 1))}
    if not query_grams:
        return 0.0
    return len(query_grams & doc_grams) / len(query_grams)


def _offline_retrieve(
    question: str,
    level: EvalLevel,
    corpus,
    top_k: int,
) -> list[RetrievedChunk]:
    hint = _offline_filter_hint(question) if level.metadata_filter else {}
    scored: list[tuple[float, object]] = []
    for chunk in corpus:
        if hint.get("category") and chunk.category != hint["category"]:
            continue
        if hint.get("domain") and chunk.domain != hint["domain"]:
            continue
        if hint.get("month") and not chunk.news_date.startswith(hint["month"]):
            continue
        score = _offline_score(question, chunk.content)
        if score <= 0:
            continue
        scored.append((score, chunk))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        RetrievedChunk(
            chunk_id=getattr(chunk, "chunk_id", ""),
            score=score,
            source="offline",
            payload={
                "chunk_id": getattr(chunk, "chunk_id", ""),
                "content": chunk.content,
                "file_title": chunk.file_title,
                "title": chunk.title,
                "part": chunk.part,
                "category": chunk.category,
                "domain": chunk.domain,
                "news_date": chunk.news_date,
            },
        )
        for score, chunk in scored[:top_k]
    ]


# ------------------------------------------------------------------ #
# 对外入口
# ------------------------------------------------------------------ #
def retrieve(
    question: str,
    level: EvalLevel,
    *,
    top_k: int = CANDIDATE_TOP_K,
    client=None,
    offline_corpus=None,
) -> RetrievalResult:
    """按层级检索一条问题。

    :param offline_corpus: 传了就走离线词法检索（冒烟用），不联网、不调大模型
    """
    started = time.time()

    if offline_corpus is not None:
        candidates = _offline_retrieve(question, level, offline_corpus, top_k)
        return RetrievalResult(
            question=question,
            level_id=level.level_id,
            rewritten_query=question,
            filters=_offline_filter_hint(question) if level.metadata_filter else {},
            filter_applied=level.metadata_filter,
            offline_smoke=True,
            candidates=candidates,
            latency_ms=int((time.time() - started) * 1000),
            notes="离线词法冒烟模式，不代表线上效果",
        )

    client = client or get_qdrant_client()

    # 1. 问题改写 + 结构化条件抽取（复用线上 Query Analyzer）
    rewritten_query = question
    filters: dict = {}
    if level.query_rewrite:
        analysis = step_3_analyze_query(question, [])
        rewritten_query = analysis.get("rewritten_query") or question
        filters = analysis.get("filters") or {}
    if not level.metadata_filter:
        # 只保留改写，不用过滤条件
        filters = {}

    query_filter = build_qdrant_filter(filters) if filters else None
    if query_filter is not None:
        logger.info(f"[eval] {level.level_id} 过滤条件：{describe_filters(filters)}")

    # 2. 向量化
    dense, sparse = _embed(rewritten_query, need_sparse=level.hybrid_retrieval)
    hyde_dense = hyde_sparse = None
    if level.hyde:
        hyde_doc = step_1_create_hyde_doc(rewritten_query)
        hyde_dense, hyde_sparse = _embed(
            f"{rewritten_query}{hyde_doc}", need_sparse=level.hybrid_retrieval
        )

    # 3. 检索（+ 空结果放宽兜底，与线上 node_search_embedding 的策略一致）
    merged, empty = _run_paths(
        client, level, dense, sparse, hyde_dense, hyde_sparse, top_k, query_filter
    )
    fallback_relaxed = False
    if not merged and query_filter is not None:
        logger.warning(f"[eval] {level.level_id} 带过滤检索为空，放宽条件重查")
        merged, _ = _run_paths(
            client, level, dense, sparse, hyde_dense, hyde_sparse, top_k, None
        )
        fallback_relaxed = True

    # 4. 精排（可选）
    if level.rerank:
        merged = _apply_rerank(rewritten_query, merged, top_k)

    return RetrievalResult(
        question=question,
        level_id=level.level_id,
        rewritten_query=rewritten_query,
        filters=filters,
        filter_applied=query_filter is not None,
        fallback_relaxed=fallback_relaxed,
        candidates=_to_retrieved(merged),
        latency_ms=int((time.time() - started) * 1000),
        notes=level.note,
    )
