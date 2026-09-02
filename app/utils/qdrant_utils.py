"""Qdrant 连接、collection 初始化和向量写入工具。"""

from __future__ import annotations

from qdrant_client import QdrantClient, models
import uuid
from app.conf.qdrant_config import qdrant_config
from app.core.logger import logger


_qdrant_client: QdrantClient | None = None


def get_qdrant_client() -> QdrantClient:
    """返回单例 Qdrant 客户端；首次调用时才创建连接对象。"""
    global _qdrant_client

    if _qdrant_client is None:
        if not qdrant_config.url:
            raise ValueError("缺少 QDRANT_URL，无法创建 Qdrant 客户端")

        _qdrant_client = QdrantClient(
            url=qdrant_config.url,
            api_key=qdrant_config.api_key,
        )
        logger.info("Qdrant 客户端已初始化")

    return _qdrant_client


def sparse_dict_to_sparse_vector(sparse_dict: dict[int, float]) -> models.SparseVector:
    if not sparse_dict: # 为空直接返回空对象
        return models.SparseVector(indices=[], values=[])
    indices = sorted(sparse_dict.keys())
    values = [sparse_dict[i] for i in indices]
    return models.SparseVector(indices=indices, values=values)


def ensure_item_name_collection(
    client: QdrantClient,
    collection_name: str | None = None,
) -> str:
    """确保 item_name collection 存在（dense + 自定义 sparse 双向量）。

    不存在则自动创建，已存在则直接返回。dense 向量使用 COSINE 距离度量。
    """
    collection_name = collection_name or qdrant_config.item_name_collection
    # 如果存在cllection
    if client.collection_exists(collection_name):
        return collection_name
    # 如果不存在则创建
    client.create_collection(
        collection_name=collection_name,
        vectors_config={
            "dense": models.VectorParams(
                size=qdrant_config.dense_dimension,
                distance=models.Distance.COSINE,
            )
        },
        sparse_vectors_config={
            "sparse": models.SparseVectorParams(),
        },
    )
    logger.info(f"Qdrant collection 已创建：{collection_name}")
    return collection_name


def upsert_item_name(
    client: QdrantClient,
    collection_name: str,
    file_title: str,
    item_name: str,
    dense_vector: list[float],
    sparse_vector: dict[int, float],
) -> None:
    """幂等写入 item_name 向量数据：先删同 item_name 的旧记录，再 upsert 新记录。"""
    # 1. 幂等删除：移除相同 item_name 的旧数据
    client.delete(
        collection_name=collection_name,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="item_name",
                        match=models.MatchValue(value=item_name),
                    ),
                ],
            ),
        ),
    )
    logger.info(f"已删除 collection[{collection_name}] 中 item_name='{item_name}' 的旧数据")

    # 2. upsert 新数据
    # ====实际上面写了一个对象转化函数功能和下面的一样===
    # 将传进来的字典sparsevector dict[int, float] 转换为 SparseVector
    # sorted_items = sorted(sparse_vector.items())   # 创建索引、必须严格递增
    # indices = [idx for idx, _ in sorted_items]
    # values = [val for _, val in sorted_items]
    # sparse = models.SparseVector(indices=indices, values=values)
    
    # 构造要传入向量数据库的point数据
    point = models.PointStruct(
        id=1,  
        vector={
            "dense": dense_vector,
            "sparse": sparse_dict_to_sparse_vector(sparse_vector),
        },
        payload={
            "file_title": file_title,
            "item_name": item_name,
        },
    )
    client.upsert(
        collection_name=collection_name,
        points=[point],
    )
    logger.info(f"已写入 item_name='{item_name}' 的向量数据到 collection[{collection_name}]")


def ensure_collection(
    client: QdrantClient,
    collection_name: str,
) -> str:
    """确保 collection 存在（dense + 自定义 sparse 双向量），不存在则自动创建。

    通用的建表函数，适用于 item_name、chunks 等所有使用 dense 1024 + sparse 向量结构的 collection。
    """
    if client.collection_exists(collection_name):
        logger.info(f"Qdrant collection{collection_name} 已存在无需创建")
        return collection_name
    
    client.create_collection(
        collection_name=collection_name,
        vectors_config={
            "dense": models.VectorParams(
                size=qdrant_config.dense_dimension,
                distance=models.Distance.COSINE,
            )
        },
        sparse_vectors_config={
            "sparse": models.SparseVectorParams(),
        },
    )
    logger.info(f"Qdrant collection 已创建：{collection_name}")
    return collection_name


def upsert_chunks(
    client: QdrantClient,
    collection_name: str,
    chunks: list[dict],
) -> list[dict]:
    """幂等批量写入 chunks 向量数据：先删同 item_name 的旧记录，再批量 upsert。

    与 upsert_item_name 的差异：
    - 批量处理 N 条 chunks（非单条 item_name）
    - 用 uuid.uuid4() 生成唯一 chunk_id 并回写到每个 chunk
    - payload 包含 content/title/parent_title/part 等切片字段
    """
    if not chunks:
        return chunks

    item_name = chunks[0].get('item_name', '')

    # 1. 幂等删除：移除相同 item_name 的旧数据
    if item_name:
        client.delete(
            collection_name=collection_name,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="item_name",
                            match=models.MatchValue(value=item_name),
                        ),
                    ],
                ),
            ),
        )
        logger.info(f"已删除 collection[{collection_name}] 中 item_name='{item_name}' 的旧数据")

    # 2. 批量构造 PointStruct，用 uuid 生成唯一 chunk_id
    points = []
    for chunk in chunks:
        chunk_id = str(uuid.uuid4())
        chunk['chunk_id'] = chunk_id

        points.append(models.PointStruct(
            id=chunk_id,
            vector={
                "dense": chunk.get('dense_vector', []),
                "sparse": sparse_dict_to_sparse_vector(chunk.get('sparse_vector', {})),
            },
            payload={
                "file_title": chunk.get('file_title', ''),
                "item_name": chunk.get('item_name', ''),
                "content": chunk.get('content', ''),
                "title": chunk.get('title', ''),
                "parent_title": chunk.get('parent_title', ''),
                "part": chunk.get('part', 0),
            },
        ))

    # 3. 批量 upsert
    client.upsert(
        collection_name=collection_name,
        points=points,
    )
    logger.info(f"已批量写入 {len(points)} 条 chunks 数据到 collection[{collection_name}]")

    return chunks


def rrf_hybrid_search(
    client: QdrantClient,
    collection_name: str,
    dense_vector: list[float],# 稠密向量，直接传列表
    sparse_vector: dict[int, float], # 稀疏向量，必须是 {index: value} 格式
    limit: int = 5,  # 此处固定为5个。【出现问题：如果很不相关，排序5个还是多了。但如果很相关，5个显然数量不够？】
    dense_limit: int = 20,
    sparse_limit: int = 20,
):
    """
    执行 Qdrant 稠密+稀疏向量混合搜索（基于 RRF 融合）
    vector2 必须为 {索引: 权重} 的字典，如 {7149: 0.829, 111290: 0.9004}
    """
    # 从字典提取索引列表和值列表（顺序一致）
    sparse_indices = list(sparse_vector.keys())
    sparse_values = list(sparse_vector.values())

    result= client.query_points(
        collection_name=collection_name,
        prefetch=[
            models.Prefetch(
                query=models.SparseVector(
                        indices=sparse_indices,
                        values=sparse_values
                    ),
                using="sparse", # collection 中的稀疏向量字段名（列名）
                limit=sparse_limit,
            ),
            models.Prefetch(
                query=dense_vector,
                using="dense", 
                limit=dense_limit,
            ),
        ],
        query=models.FusionQuery(fusion=models.Fusion.RRF), # 默认k=60 使用rrf排序算法 

        limit=limit,

        with_payload=True
    )
    logger.success(f"混合搜索完成~")
    return result

