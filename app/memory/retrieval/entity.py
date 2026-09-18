"""Entity 召回与 Boost。"""
from typing import Dict, List, Tuple

from app.memory.models import EntityRecord
from app.memory.storage.entity_store import EntityStore
from app.memory.config import ENTITY_BOOST_MAX
import math

def entity_search(
    entity_store: EntityStore,
    dense_vector: list[float],
    scope: str,
    threshold: float,
    limit: int,
) -> List[Tuple[EntityRecord, float]]:
    return entity_store.search_semantic(
        dense_vector,
        scope,
        limit,
        threshold,
    )


def entity_boost(
    entity_similarities: Dict[str, float],
    entities: List[EntityRecord],
) -> Dict[str, float]:
    boost: Dict[str, float] = {}
    for entity in entities:
        similarity = entity_similarities.get(entity.id, 0.0)
        if similarity <= 0:
            continue
        # 该实体链接到的记忆数量
        num_linked = max(1, len(entity.linked_memory_ids))
        # 稀释度函数
        # 当 num_linked = 1（独苗实体）：log(1)=0 → 权重 = 1.0（满额加分）。
        # 当 num_linked = 10：log(10)≈2.3 → 1 / (1+1.15) ≈ 0.465（接近减半）。
        # 当 num_linked = 100：log(100)≈4.6 → 1 / (1+2.3) ≈ 0.30（保留 30%）。
        # 当 num_linked = 10,000：log(10k)≈9.2 → 1 / (1+4.6) ≈ 0.178（保留约 18%）    
        memory_count_weight = 1 / (1 + 0.5 * math.log(num_linked))
        # 相似度分数：从实体向量库里读到的分数，memory_count_weight实体链接到的记忆都条数
        # 系数 ENTITY_BOOST_MAX 决定实体信号的上限（见 app/memory/config.py 的注释）
        value = similarity * ENTITY_BOOST_MAX * memory_count_weight
        for memory_id in entity.linked_memory_ids:
            # 如果一条记忆同时命中了两个实体，只取最高的那个加分，而不累加。

            # 防止“标签堆叠”作弊。假设用户问“苹果手机”，命中了“苹果（相似度0.6）”和“手机（相似度0.6）”，如果累加变成 1.2，直接顶爆分数上限。
            # 用 max 确保无论命中多少实体，单条记忆的实体加分上限就是 0.5 * 0.178 = 0.089（对于高频词）或 0.5（对于极稀有词）
            boost[memory_id] = max(boost.get(memory_id, 0.0), value)
    return boost
