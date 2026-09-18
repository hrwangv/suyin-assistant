"""长期记忆排序的打分测试。

锁住这次的设计决定（对齐 mem0）：
    最终分 = 稠密余弦相似度 + 实体加分      ← 直接相加，不做 RRF 排名融合

为什么必须同量级：稠密相似度实测约 0.35~0.96，实体加分 0~ENTITY_BOOST_MAX。
早先用 dense+sparse 的 RRF 时，基础分被压到 0.008~0.033，
和 0.5 的加分差一个数量级，"相似度 + 加分"的语义就失效了。

不连真实 Qdrant / 大模型。
运行：python -m app.test.test_memory_scoring
"""
from __future__ import annotations

from datetime import datetime

from app.memory.models import EntityRecord, Memory

passed = 0
failed: list[str] = []


def check(name: str, actual, expected):
    global passed
    if actual == expected:
        passed += 1
    else:
        failed.append(f"{name}\n     期望：{expected}\n     实际：{actual}")


def _memory(memory_id: str, text: str) -> Memory:
    now = datetime.now()
    return Memory(
        id=memory_id, data=text, created_at=now, updated_at=now,
        importance=0.5, strength_score=0.5, scope="user:u1",
    )


def _run_search(dense_scores, entity_hit, linked_ids=None):
    """跑一次 search，返回 explain 后的结果。

    linked_ids：这个实体关联了哪些记忆（默认全部），用来控制加分落在谁身上。
    """
    import app.memory.coordinator as coordinator_module
    from app.memory.config import ENTITY_BOOST_MAX

    memories = {mid: _memory(mid, f"记忆{mid}") for mid in dense_scores}
    calls = {"dense": 0, "hybrid": 0}

    class FakeVectorStore:
        def search(self, dense, scope, limit):
            calls["dense"] += 1
            return [(memories[mid], score) for mid, score in dense_scores.items()]

        def search_hybrid(self, *args, **kwargs):
            calls["hybrid"] += 1
            raise AssertionError("不应该再走 RRF 混合检索")

        def update_stats(self, *args, **kwargs):
            pass

    class FakeEntityStore:
        def search_semantic(self, dense_vector, scope, limit, threshold):
            if not entity_hit:
                return []
            entity = EntityRecord(
                id="e1", data="欣旺达",
                linked_memory_ids=list(linked_ids or dense_scores),
                entity_key="欣旺达",
            )
            return [(entity, entity_hit)]

    coordinator = coordinator_module.MemoryCoordinator.__new__(coordinator_module.MemoryCoordinator)
    coordinator.recent_service = None
    coordinator.vector_store = FakeVectorStore()
    coordinator.entity_store = FakeEntityStore()
    coordinator.history_store = None

    original_embed = coordinator_module.embed_texts
    original_extract = coordinator_module.extract_entities
    coordinator_module.embed_texts = lambda texts: ([[0.1] * 4] * len(texts), [{}] * len(texts))
    # 查询里抽出一个实体（是否命中由 FakeEntityStore 决定）
    coordinator_module.extract_entities = lambda *a, **k: [{"text": "欣旺达"}] if entity_hit else []
    try:
        result = coordinator.search(query="欣旺达的动态", scope="user:u1", top_k=5, explain=True)
    finally:
        coordinator_module.embed_texts = original_embed
        coordinator_module.extract_entities = original_extract

    return result["long_term_memories"], calls, ENTITY_BOOST_MAX


def test_dense_only_no_rrf():
    """召回走纯稠密检索，不再调用 search_hybrid（RRF）。"""
    _, calls, _ = _run_search({"m1": 0.6}, entity_hit=None)
    check("调用了稠密检索", calls["dense"], 1)
    check("没有调用混合检索", calls["hybrid"], 0)


def test_score_is_similarity_plus_boost():
    """最终分 = 稠密相似度 + 实体加分（同量级直接相加）。"""
    results, _, boost_max = _run_search({"m1": 0.6}, entity_hit=0.9)
    item = results[0]

    # 实体只关联 1 条记忆 → 稀释度 = 1/(1+0.5·ln(1)) = 1.0
    expected_boost = 0.9 * boost_max * 1.0
    check("稠密分原样保留", item["dense_score"], 0.6)
    check("实体加分按公式计算", round(item["entity_boost"], 6), round(expected_boost, 6))
    check("最终分是相加", round(item["combined_score"], 6), round(0.6 + expected_boost, 6))
    check("两分量同量级（boost ≤ 1.0）", item["entity_boost"] <= boost_max, True)


def test_entity_boost_can_reorder():
    """实体加分能把一条记忆提上来，但提出量受 ENTITY_BOOST_MAX 限制。"""
    # m1 语义更相关(0.80) 但没实体命中；m2 语义稍差(0.60) 但有实体命中
    results, _, boost_max = _run_search(
        {"m1": 0.80, "m2": 0.60}, entity_hit=0.9, linked_ids=["m2"])
    by_id = {r["memory_id"]: r for r in results}
    gap = by_id["m1"]["combined_score"] - by_id["m2"]["combined_score"]

    check("两条都在结果里", len(results), 2)
    # m2 的加分上限就是 boost_max，所以能否反超取决于 boost_max 与语义差距的关系
    check("加分不超过上限", by_id["m2"]["entity_boost"] <= boost_max, True)
    # 当前 boost_max=0.5 > 语义差距 0.2 → 会被反超（这正说明系数偏大）
    check("当前系数下实体命中可反超", gap < 0, True)


def test_boost_max_from_config():
    """系数走配置，改一行就能调整实体信号的强度。"""
    from app.memory.config import ENTITY_BOOST_MAX

    check("配置项存在且为浮点", isinstance(ENTITY_BOOST_MAX, float), True)
    check("配置值合理（0~1）", 0.0 < ENTITY_BOOST_MAX <= 1.0, True)


def main():
    for test in (
        test_dense_only_no_rrf,
        test_score_is_similarity_plus_boost,
        test_entity_boost_can_reorder,
        test_boost_max_from_config,
    ):
        test()

    print(f"\n通过 {passed} 项，失败 {len(failed)} 项")
    for item in failed:
        print("  [FAIL]", item)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
