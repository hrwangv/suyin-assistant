"""L2 Durable Memory：Qdrant Vector Store。

这里保存记忆的当前状态，是长期记忆的 Source of Truth。
"""
import json
from typing import List, Optional

from qdrant_client import models

from app.core.logger import logger
from app.memory.config import MEMORY_QDRANT_COLLECTION
from app.memory.models import Memory
from app.memory.utils.time import from_iso, to_iso
from app.utils.qdrant_utils import ensure_collection, get_qdrant_client, sparse_dict_to_sparse_vector


class MemoryVectorStore:
    def __init__(self, collection_name: str = MEMORY_QDRANT_COLLECTION):
        self.client = get_qdrant_client()
        self.collection_name = ensure_collection(self.client, collection_name)

    def _payload_to_memory(self, payload: dict) -> Memory:
        return Memory(
            id=payload["id"],
            data=payload["data"],
            hash=payload.get("hash", ""),
            text_lemmatized=payload.get("text_lemmatized", ""),
            created_at=from_iso(payload.get("created_at")),
            updated_at=from_iso(payload.get("updated_at")),
            user_id=payload.get("user_id") or None,
            agent_id=payload.get("agent_id") or None,
            run_id=payload.get("run_id") or None,
            role=payload.get("role") or None,
            actor_id=payload.get("actor_id") or None,
            attributed_to=payload.get("attributed_to") or None,
            metadata=json.loads(payload.get("metadata_json") or "{}"),
            importance=float(payload.get("importance", 0.5)),
            strength_score=float(payload.get("strength_score", 0.5)),
            last_accessed_at=from_iso(payload.get("last_accessed_at")),
            access_count=int(payload.get("access_count", 0)),
            expiration_date=from_iso(payload.get("expiration_date")),
            lifecycle_state=payload.get("lifecycle_state", "ACTIVE"),
            scope=payload.get("scope", ""),
        )

    def _memory_payload(self, memory: Memory, scope: str) -> dict:
        return {
            "id": memory.id,
            "data": memory.data,
            "hash": memory.hash,
            "text_lemmatized": memory.text_lemmatized,
            "created_at": to_iso(memory.created_at),
            "updated_at": to_iso(memory.updated_at),
            "user_id": memory.user_id or "",
            "agent_id": memory.agent_id or "",
            "run_id": memory.run_id or "",
            "role": memory.role or "",
            "actor_id": memory.actor_id or "",
            "attributed_to": memory.attributed_to or "",
            "metadata_json": json.dumps(memory.metadata, ensure_ascii=False),
            "importance": memory.importance,
            "strength_score": memory.strength_score,
            "last_accessed_at": to_iso(memory.last_accessed_at) or "",
            "access_count": memory.access_count,
            "expiration_date": to_iso(memory.expiration_date) or "",
            "lifecycle_state": memory.lifecycle_state,
            "scope": scope,
        }

    def upsert(
        self,
        memory: Memory,
        dense_vector: list[float],
        sparse_vector: dict[int, float],
        scope: str,
    ) -> None:
        point = models.PointStruct(
            id=memory.id,
            vector={
                "dense": dense_vector,
                "sparse": sparse_dict_to_sparse_vector(sparse_vector),
            },
            payload=self._memory_payload(memory, scope),
        )
        self.client.upsert(
            collection_name=self.collection_name,
            points=[point],
        )

    def set_payload(self, memory_id: str, payload: dict) -> None:
        self.client.set_payload(
            collection_name=self.collection_name,
            payload=payload,
            points=[memory_id],
        )

    def update_stats(
        self,
        memory_id: str,
        scope: str,
        access_count: int,
        last_accessed_at,
    ) -> None:
        self.set_payload(
            memory_id,
            {
                "access_count": access_count,
                "last_accessed_at": to_iso(last_accessed_at) or "",
                "scope": scope,
            },
        )

    def delete(self, memory_id: str) -> None:
        self.client.delete(
            collection_name=self.collection_name,
            points_selector=[memory_id],
        )

    def search(
        self,
        dense_vector: list[float],
        scope: str,
        limit: int,
        lifecycle_state: str = "ACTIVE",
    ) -> List[tuple[Memory, float]]:
        """稠密向量检索（当前长期记忆的唯一召回路径）。

        返回 (Memory, 余弦相似度) —— 分数范围实测约 0.35~0.96，
        与 entity_boost（0~0.5）同量级，所以两者相加是有意义的。
        这也是 mem0 的原始设计：相似度 + 额外加分。
        """
        query_filter = models.Filter(
            must=[
                models.FieldCondition(
                    key="scope",
                    match=models.MatchValue(value=scope),
                ),
                models.FieldCondition(
                    key="lifecycle_state",
                    match=models.MatchValue(value=lifecycle_state),
                ),
            ]
        )
        result = self.client.query_points(
            collection_name=self.collection_name,
            query=dense_vector,
            using="dense",
            query_filter=query_filter,
            limit=limit,
            with_payload=True,
        )
        return [
            (self._payload_to_memory(point.payload), float(point.score))
            for point in result.points
            if point.payload
        ]

    def search_hybrid(
        self,
        dense_vector: list[float],
        sparse_vector: dict[int, float],
        scope: str,
        limit: int,
        lifecycle_state: str = "ACTIVE",
    ) -> List[tuple[Memory, float]]:
        """【当前未使用】dense + sparse RRF 混合召回，保留以便随时切回。

        改用它的原因（如果将来要切回）：
        - 优点：罕见词/数字（"50GWh"）这类精确词项命中更准，候选更多样
        - 缺点：RRF 会把原始相似度压缩到 0.008~0.033，与 entity_boost（0~0.5）
          差一个数量级，mem0 那套"相似度 + 加分"的加法语义会失效
        当前的选择是 dense-only，见上方 search()。

        避免像 Python BM25 那样全量加载 ACTIVE Memory。
        """
        query_filter = models.Filter(
            must=[
                models.FieldCondition(
                    key="scope",
                    match=models.MatchValue(value=scope),
                ),
                models.FieldCondition(
                    key="lifecycle_state",
                    match=models.MatchValue(value=lifecycle_state),
                ),
            ]
        )

        sparse_indices = list(sparse_vector.keys())
        sparse_values = list(sparse_vector.values())
        result = self.client.query_points(
            collection_name=self.collection_name,
            prefetch=[
                models.Prefetch(
                    query=dense_vector,
                    using="dense",
                    limit=limit,
                ),
                models.Prefetch(
                    query=models.SparseVector(
                        indices=sparse_indices,
                        values=sparse_values,
                    ),
                    using="sparse",
                    limit=limit,
                ),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            query_filter=query_filter,
            limit=limit,
            with_payload=True,
        )

        return [
            (self._payload_to_memory(point.payload), float(point.score))
            for point in result.points
            if point.payload
        ]

    def scroll_active(self, scope: str, limit: Optional[int] = None) -> List[Memory]:
        """遍历该 scope 下所有 ACTIVE 记忆。

        注意 Qdrant 的 scroll **不会自动翻页**，且 limit 默认只有 10 ——
        不传 limit 的话只能拿到第一页，跟调用方"要全部"的预期不符
        （长期记忆的去重、生命周期重算都依赖全量）。所以这里默认循环分页取完。

        :param limit: None 表示取全部；传入数字则只取前 N 条（不翻页）
        """
        query_filter = models.Filter(
            must=[
                models.FieldCondition(
                    key="scope",
                    match=models.MatchValue(value=scope),
                ),
                models.FieldCondition(
                    key="lifecycle_state",
                    match=models.MatchValue(value="ACTIVE"),
                ),
            ]
        )
        page_size = limit or 100
        points = []
        offset = None
        while True:
            batch, offset = self.client.scroll(
                collection_name=self.collection_name,
                scroll_filter=query_filter,
                limit=page_size,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            points.extend(batch)
            if offset is None:
                break
            if limit is not None and len(points) >= limit:
                points = points[:limit]
                break
        return [
            self._payload_to_memory(point.payload)
            for point in points
            if point.payload
        ]

    def get(self, memory_id: str) -> Optional[Memory]:
        result = self.client.retrieve(
            collection_name=self.collection_name,
            ids=[memory_id],
            with_payload=True,
        )
        if not result:
            return None
        return self._payload_to_memory(result[0].payload)
