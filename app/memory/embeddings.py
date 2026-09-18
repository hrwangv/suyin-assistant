"""Embedding 适配层。

优先批量生成；批量失败时逐条降级，不能静默返回空结果。
"""
from typing import List, Tuple

from app.core.logger import logger
from app.llm.qwen_embedding_utils import generate_embeddings


def embed_texts(texts: List[str]) -> Tuple[List[list], List[dict]]:
    if not texts:
        return [], []

    try:
        result = generate_embeddings(texts)
        return result["dense"], result["sparse"]
    except Exception as exc:
        logger.warning(f"Embedding batch 失败，准备逐条降级：{exc}")

    dense_vectors = []
    sparse_vectors = []
    for text in texts:
        try:
            result = generate_embeddings([text])
            dense_vectors.extend(result["dense"])
            sparse_vectors.extend(result["sparse"])
        except Exception as single_exc:
            logger.error(f"单条 Embedding 失败：{single_exc}")
            raise

    return dense_vectors, sparse_vectors
