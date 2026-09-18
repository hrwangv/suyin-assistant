"""Qdrant 连接、collection 初始化和向量写入工具。"""

from __future__ import annotations

from qdrant_client import QdrantClient, models
import uuid
from app.conf.qdrant_config import qdrant_config
from app.core.logger import logger
from app.utils.date_utils import to_rfc3339_date


_qdrant_client: QdrantClient | None = None

# 不写入 payload 的 chunk 字段：向量已经放在 PointStruct 的 vector 字段里，无需重复存
_PAYLOAD_EXCLUDED_FIELDS = frozenset({"dense_vector", "sparse_vector"})


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
    file_id: str = "",
    task_id: str = "",
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
            "file_id": file_id,
            "task_id": task_id,
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


# 检索时会被过滤/排序的 payload 字段及其索引类型。
# 索引只影响「过滤快不快」，不改变过滤的写法：不建索引 filter 照样生效（全表扫），
# 建了索引 Qdrant 才能快速命中，带过滤的向量检索也才有准确的基数估算。
# 注意：字段名要和 upsert_chunks 写入的 payload、QdrantFilterBuilder 使用的 key 保持一致。
#
# 说明：item_name 曾用于精确过滤，但它现在只是文件名兜底值（主体识别已关闭），
# 且不是高频过滤条件，所以不建索引。
CHUNKS_PAYLOAD_INDEXES: dict[str, str] = {
    "category": "keyword",    # 精确匹配
    "domain": "keyword",      # 精确匹配
    "file_title": "keyword",  # 按文档来源过滤
    "news_date": "datetime",  # RFC3339 时间范围过滤（DatetimeRange）
}


def ensure_payload_indexes(
    client: QdrantClient,
    collection_name: str,
    indexes: dict[str, str] | None = None,
) -> list[str]:
    """按需创建 payload 索引，返回本次真正新建的字段名列表。

    两个设计考虑：
    1. 已存在的索引不重复创建 —— 重复调用会以新参数重建索引（重扫全量数据），
       没必要每次导入都跑，所以先查 payload_schema 再决定建不建；
    2. 整体 try/except 包住，索引建失败不影响「文档导入」这个主流程。
    """
    indexes = indexes or CHUNKS_PAYLOAD_INDEXES
    created: list[str] = []
    try:
        # 先看哪些字段已经有索引，避免重复创建
        existing = set(client.get_collection(collection_name).payload_schema or {})
        for field_name, field_schema in indexes.items():
            if field_name in existing:
                continue
            client.create_payload_index(
                collection_name=collection_name,
                field_name=field_name,
                field_schema=field_schema,
                wait=True,
            )
            created.append(field_name)
        if created:
            logger.info(f"collection[{collection_name}] 已创建 payload 索引：{created}")
        else:
            logger.info(f"collection[{collection_name}] payload 索引已齐全，无需创建")
    except Exception as e:
        # 索引属于性能优化，失败时降级为全表扫描即可，不阻断导入
        logger.warning(f"创建 payload 索引失败（不影响导入，检索会退化为全表扫描）：{e}")
    return created


def upsert_chunks(
    client: QdrantClient,
    collection_name: str,
    chunks: list[dict],
    file_id: str = "",
    task_id: str = "",
) -> list[dict]:
    """幂等批量写入 chunks 向量数据：先删同 item_name 的旧记录，再批量 upsert。

    与 upsert_item_name 的差异：
    - 批量处理 N 条 chunks（非单条 item_name）
    - 用 uuid.uuid4() 生成唯一 chunk_id 并回写到每个 chunk
    - payload 除 content/title/parent_title/part 等基础字段外，
      还会透传 chunk 上的全部业务元数据（news_date/section/category/domain 等），
      仅排除 dense_vector/sparse_vector（向量本身单独存放）
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

        # 基础字段：显式给出，缺失时给默认值，保证检索侧始终能读到这几个 key
        payload = {
            "file_id": file_id,
            "task_id": task_id,
            "file_title": chunk.get('file_title', ''),
            "item_name": chunk.get('item_name', ''),
            "content": chunk.get('content', ''),
            "title": chunk.get('title', ''),
            "parent_title": chunk.get('parent_title', ''),
            "part": chunk.get('part', 0),
        }
        # 其余业务元数据（news_date / section / category / domain / chunk_id 等）一并透传，
        # 避免以后新增字段时又被静默丢掉；只有向量字段不进 payload
        payload.update({
            key: value
            for key, value in chunk.items()
            if key not in _PAYLOAD_EXCLUDED_FIELDS
        })
        # 日期统一成 RFC3339 再入库：payload 里只保留 news_date 一个日期字段，
        # 检索时可以直接对它做 DatetimeRange 范围过滤，不用再存第二份。
        # 切分节点已经转过一次，这里再兜一次是为了防止别的写入路径直接塞紧凑格式；
        # 转换函数是幂等的，重复调用没有副作用。
        if payload.get("news_date"):
            payload["news_date"] = to_rfc3339_date(payload["news_date"]) or payload["news_date"]

        points.append(models.PointStruct(
            id=chunk_id,
            vector={
                "dense": chunk.get('dense_vector', []),
                "sparse": sparse_dict_to_sparse_vector(chunk.get('sparse_vector', {})),
            },
            payload=payload,
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
    # 数量不在这里给默认值，由调用方显式传入：
    # 「召回多少条」是这一路检索的策略，主路 / HyDE 路 / 主体名匹配的诉求并不一样，
    # 函数只负责把调用方给的数字透传给 Qdrant。
    # 取值统一来自 app/conf/retrieval_config.py；漏传直接 TypeError，
    # 避免静默退回某个默认值、把召回池悄悄收窄（rerank 就没得挑了）。
    limit: int,
    dense_limit: int,
    sparse_limit: int,
    query_filter: models.Filter | None = None, # payload 过滤条件，由 QdrantFilterBuilder 构造；None 表示纯语义检索
):
    """
    执行 Qdrant 稠密+稀疏向量混合搜索（基于 RRF 融合）
    vector2 必须为 {索引: 权重} 的字典，如 {7149: 0.829, 111290: 0.9004}

    filter 说明：
    - query_filter 为 None 时，行为与改造前完全一致（纯语义检索）；
    - 传入 filter 时，同时挂在每一路 prefetch 和外层查询上：
      prefetch 上的 filter 让过滤发生在向量检索阶段（保证召回质量），
      外层再挂一次，确保最终融合结果也只包含符合条件的点。
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
                filter=query_filter,
            ),
            models.Prefetch(
                query=dense_vector,
                using="dense", 
                limit=dense_limit,
                filter=query_filter,
            ),
        ],
        query=models.FusionQuery(fusion=models.Fusion.RRF), # 默认k=60 使用rrf排序算法 

        limit=limit,
        # 注意参数名：新版 qdrant-client 用 query_filter（旧版叫 filter），
        # 传错名字会被 **kwargs 吞掉，过滤会静默失效
        query_filter=query_filter,

        with_payload=True
    )
    logger.success(f"混合搜索完成~")
    return result
