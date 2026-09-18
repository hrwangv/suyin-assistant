"""Entity Store：使用 Qdrant 保存实体及其与 Memory 的关联。

实体是辅助索引，Entity 失败不能影响主 Memory Pipeline。
"""
from typing import List, Optional, Tuple

from qdrant_client import models

from app.core.logger import logger
from app.memory.config import ENTITY_QDRANT_COLLECTION
from app.memory.models import EntityRecord
from app.memory.utils.normalization import normalize_entity_key
from app.utils.qdrant_utils import ensure_collection, get_qdrant_client, sparse_dict_to_sparse_vector


class EntityStore:
    def __init__(self, collection_name: str = ENTITY_QDRANT_COLLECTION):
        self.client = get_qdrant_client()
        self.collection_name = ensure_collection(self.client, collection_name)

    def upsert(
        self,
        entity: EntityRecord,
        dense_vector: list[float],
        sparse_vector: dict[int, float],
        scope: str,
    ) -> None:
        entity.entity_key = normalize_entity_key(entity.data)
        point = models.PointStruct(
            id=entity.id,
            vector={
                "dense": dense_vector,
                "sparse": sparse_dict_to_sparse_vector(sparse_vector),
            },
            payload={
                "id": entity.id,
                "data": entity.data,
                "entity_key": entity.entity_key,
                "linked_memory_ids": entity.linked_memory_ids,
                "user_id": entity.user_id or "",
                "agent_id": entity.agent_id or "",
                "run_id": entity.run_id or "",
                "scope": scope,
            },
        )
        self.client.upsert(
            collection_name=self.collection_name,
            points=[point],
        )

    def search_semantic(
        self,
        dense_vector: list[float],
        scope: str,
        limit: int,
        threshold: float,
    ) -> List[Tuple[EntityRecord, float]]:
        query_filter = models.Filter(
            must=[
                models.FieldCondition(
                    key="scope",
                    match=models.MatchValue(value=scope),
                )
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

        entities: List[Tuple[EntityRecord, float]] = []
        for point in result.points:
            if point.score < threshold:
                continue
            payload = point.payload
            entities.append(
                (
                    EntityRecord(
                        id=payload["id"],
                        data=payload["data"],
                        linked_memory_ids=payload.get("linked_memory_ids", []),
                        user_id=payload.get("user_id") or None,
                        agent_id=payload.get("agent_id") or None,
                        run_id=payload.get("run_id") or None,
                        entity_key=payload.get("entity_key", ""),
                    ),
                    float(point.score),
                )
            )
        return entities

    def get_by_keys(self, entity_keys: List[str], scope: str) -> List[EntityRecord]:
        if not entity_keys:
            return []
        query_filter = models.Filter(
            must=[
                models.FieldCondition(
                    key="entity_key",
                    match=models.MatchAny(any=entity_keys),
                ),
                models.FieldCondition(
                    key="scope",
                    match=models.MatchValue(value=scope),
                ),
            ]
        )
        # 查询所有符合条件的实体关联points
        points, _ = self.client.scroll(
            collection_name=self.collection_name,
            scroll_filter=query_filter,
            with_payload=True,
            with_vectors=False, # 不返回vector
        )
        return [
            EntityRecord(
                id=p.payload["id"],
                data=p.payload["data"],
                linked_memory_ids=p.payload.get("linked_memory_ids", []),
                user_id=p.payload.get("user_id") or None,
                agent_id=p.payload.get("agent_id") or None,
                run_id=p.payload.get("run_id") or None,
                entity_key=p.payload.get("entity_key", ""),
            )
            for p in points
            if p.payload
        ]

    def get(self, entity_id: str) -> Optional[EntityRecord]:
        result = self.client.retrieve(
            collection_name=self.collection_name,
            ids=[entity_id],
            with_payload=True,
        )
        if not result or not result[0].payload:
            return None
        payload = result[0].payload
        return EntityRecord(
            id=payload["id"],
            data=payload["data"],
            linked_memory_ids=payload.get("linked_memory_ids", []),
            user_id=payload.get("user_id") or None,
            agent_id=payload.get("agent_id") or None,
            run_id=payload.get("run_id") or None,
            entity_key=payload.get("entity_key", ""),
        )

    def get_by_linked_memory(
        self,
        memory_id: str,
        scope: str,
    ) -> List[EntityRecord]:
        query_filter = models.Filter(
            must=[
                models.FieldCondition(
                    key="scope",
                    match=models.MatchValue(value=scope),
                ),
                models.FieldCondition(
                    key="linked_memory_ids",
                    match=models.MatchAny(any=[memory_id]),
                ),
            ]
        )
        points, _ = self.client.scroll(
            collection_name=self.collection_name,
            scroll_filter=query_filter,
            with_payload=True,
            with_vectors=False,
        )
        return [
            EntityRecord(
                id=p.payload["id"],
                data=p.payload["data"],
                linked_memory_ids=p.payload.get("linked_memory_ids", []),
                user_id=p.payload.get("user_id") or None,
                agent_id=p.payload.get("agent_id") or None,
                run_id=p.payload.get("run_id") or None,
                entity_key=p.payload.get("entity_key", ""),
            )
            for p in points
            if p.payload
        ]

    def delete_entity(self, entity_id: str) -> None:
        self.client.delete(
            collection_name=self.collection_name,
            points_selector=[entity_id],
        )
