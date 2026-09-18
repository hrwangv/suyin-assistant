"""可选 Reranker。"""
from typing import List

from app.llm.reranker_utils import text_rerank


def rerank(query: str, memories: List[dict], top_n: int) -> List[dict]:
    """对候选记忆文本重排序。

    memories 中每一项至少包含 memory_id 与 data。
    """
    documents = [item["data"] for item in memories]
    results = text_rerank(query, documents, top_n)
    ranked = []
    for result in results:
        index = result.get("index", 0)
        if index < len(memories):
            item = dict(memories[index])
            item["rerank_score"] = result.get("relevance_score", 0.0)
            ranked.append(item)
    return ranked
