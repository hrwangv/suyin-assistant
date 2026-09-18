"""长期记忆抽取的「已有相关记忆召回」与落库行为测试。

改造点：参考集不再用 scroll_active() 的前 20 条（顺序不确定、与实际内容无关），
改成「把本批新消息拼成查询文本 → 向量检索 top_k 条最相关的已有记忆」。

另外覆盖：写入侧已移除「全量 scroll_active + md5 精确去重」，
所以这里断言抽取过程不再全量遍历记忆，且重复文本会照样入库。

不连真实 Qdrant / 大模型。
运行：python -m app.test.test_memory_recall
"""
from __future__ import annotations

from types import SimpleNamespace

from app.memory.config import (
    EXTRACTION_DEDUP_TOP_K,
    EXTRACTION_RECALL_QUERY_MAX_TOKENS,
)

passed = 0
failed: list[str] = []


def check(name: str, actual, expected):
    global passed
    if actual == expected:
        passed += 1
    else:
        failed.append(f"{name}\n     期望：{expected}\n     实际：{actual}")


def _coordinator(recalled, captured):
    import app.memory.coordinator as coordinator_module

    class FakeVectorStore:
        def search(self, dense_vector, scope, limit, lifecycle_state="ACTIVE"):
            captured["search_args"] = {
                "dense_vector": dense_vector, "scope": scope, "limit": limit,
            }
            return [(SimpleNamespace(id=m["id"], data=m["text"]), 0.9) for m in recalled]

        def scroll_active(self, scope, limit=None):
            captured["scroll_called"] = True
            return []

        def upsert(self, *a, **k):
            pass

    class FakeExtractor:
        def extract(self, new_messages, existing_memories, last_messages):
            captured["reference"] = existing_memories
            captured["new_messages"] = new_messages
            return {"memory": []}

    coordinator = coordinator_module.MemoryCoordinator.__new__(coordinator_module.MemoryCoordinator)
    coordinator.recent_service = None
    coordinator.vector_store = FakeVectorStore()
    coordinator.extractor = FakeExtractor()
    coordinator.history_store = None
    coordinator.entity_store = None

    original_embed = coordinator_module.embed_texts
    coordinator_module.embed_texts = lambda texts: ([[0.1] * 4] * len(texts), [{}] * len(texts))
    captured["restore_embed"] = original_embed
    return coordinator


def test_reference_comes_from_vector_search():
    """参考集来自向量检索（相关记忆），不是 scroll_active 的切片。"""
    import app.memory.coordinator as coordinator_module

    captured = {}
    coordinator = _coordinator(
        [{"id": "m1", "text": "欣旺达产能扩张"}, {"id": "m2", "text": "用户偏好"}], captured
    )
    try:
        coordinator.add(
            [{"role": "user", "content": "欣旺达今年的产能怎么样"}],
            user_id="u1", infer=True,
        )
    finally:
        coordinator_module.embed_texts = captured["restore_embed"]

    check("参考集来自检索结果",
          captured["reference"],
          [{"id": "m1", "text": "欣旺达产能扩张"}, {"id": "m2", "text": "用户偏好"}])
    check("检索条数来自配置", captured["search_args"]["limit"], EXTRACTION_DEDUP_TOP_K)
    check("检索锁定了 scope", captured["search_args"]["scope"], "user:u1")
    # 去重已前移到模型侧：不再全量遍历本 scope 的记忆做 md5 精确去重
    check("不再全量遍历记忆", bool(captured.get("scroll_called")), False)


def test_query_text_is_built_from_batch_and_bounded():
    """查询文本由本批消息拼成，且受 token 预算约束（embedding 有单条上限）。"""
    import app.memory.coordinator as coordinator_module
    from app.utils.token_budget import count_tokens

    captured = {}
    coordinator = _coordinator([], captured)

    seen = {}
    original_embed = coordinator_module.embed_texts

    def spy_embed(texts):
        seen["texts"] = texts
        return ([[0.1] * 4] * len(texts), [{}] * len(texts))

    coordinator_module.embed_texts = spy_embed
    try:
        # 一批"短消息但总量超预算"的消息：应被截到预算内
        messages = [{"role": "user", "content": "第%d条消息" % i * 5} for i in range(100)]
        coordinator.add(messages, user_id="u1", infer=True)
    finally:
        coordinator_module.embed_texts = original_embed

    text = seen["texts"][0]
    check("只嵌入一条查询文本", len(seen["texts"]), 1)
    check("查询文本受 token 预算约束",
          count_tokens(text) <= EXTRACTION_RECALL_QUERY_MAX_TOKENS, True)
    check("查询文本非空", bool(text.strip()), True)
    check("保留的是批次里较新的内容", text.strip().endswith("第99条消息" * 5), True)


def test_huge_single_message_still_produces_query():
    """单条消息就超过预算时，至少保留一条（否则整批被丢光、召回失效）。"""
    import app.memory.coordinator as coordinator_module

    captured = {}
    coordinator = _coordinator([], captured)
    seen = {}
    original_embed = coordinator_module.embed_texts

    def spy_embed(texts):
        seen["texts"] = texts
        return ([[0.1] * 4] * len(texts), [{}] * len(texts))

    coordinator_module.embed_texts = spy_embed
    try:
        coordinator.add(
            [{"role": "user", "content": "欣旺达产能" * 400}], user_id="u1", infer=True
        )
    finally:
        coordinator_module.embed_texts = original_embed

    check("仍然生成了查询文本", bool(seen["texts"][0].strip()), True)


def test_recall_failure_does_not_break_extraction():
    """召回失败时不中断抽取，只是没有去重参考。"""
    import app.memory.coordinator as coordinator_module

    captured = {}
    coordinator = _coordinator([], captured)

    def boom(texts):
        raise RuntimeError("embedding 服务不可用")

    coordinator_module.embed_texts = boom
    try:
        coordinator.add(
            [{"role": "user", "content": "欣旺达的情况"}], user_id="u1", infer=True
        )
    finally:
        coordinator_module.embed_texts = captured["restore_embed"]

    check("抽取照常进行", "reference" in captured, True)
    check("参考集为空", captured["reference"], [])


def _write_coordinator(extracted, captured):
    """带假存储的 coordinator，用于验证「抽取结果如何落库」。"""
    import app.memory.coordinator as coordinator_module

    class FakeVectorStore:
        def search(self, *a, **k):
            return []

        def scroll_active(self, scope, limit=None):
            captured["scroll_called"] = True
            return []

        def upsert(self, memory, dense, sparse, scope):
            captured.setdefault("upserted", []).append(memory.data)

    class FakeHistory:
        def add(self, record):
            captured.setdefault("history", []).append(record.new_memory)

    class FakeEntityStore:
        def get_by_keys(self, keys, scope):
            return []

        def search_semantic(self, vec, scope, limit, threshold):
            return []

        def upsert(self, *a, **k):
            pass

    class FakeExtractor:
        def extract(self, new_messages, existing_memories, last_messages):
            return {"memory": extracted}

    coordinator = coordinator_module.MemoryCoordinator.__new__(
        coordinator_module.MemoryCoordinator
    )
    coordinator.vector_store = FakeVectorStore()
    coordinator.history_store = FakeHistory()
    coordinator.entity_store = FakeEntityStore()
    coordinator.recent_service = None
    coordinator.extractor = FakeExtractor()

    captured["restore_embed"] = coordinator_module.embed_texts
    captured["restore_entities"] = coordinator_module.extract_entities
    coordinator_module.embed_texts = lambda texts: (
        [[0.1] * 4] * len(texts), [{}] * len(texts)
    )
    coordinator_module.extract_entities = lambda text, max_entities=8: []
    return coordinator


def test_duplicate_texts_are_written_without_hash_dedup():
    """hash 去重已移除：模型返回的重复文本会照样入库（当前有意为之）。

    这条断言把契约写死——如果以后补回硬去重（建议用向量相似度，
    而不是全量 md5），这里要同步改成「重复只写一次」。
    """
    import app.memory.coordinator as coordinator_module

    captured = {}
    extracted = [
        {"text": "润泽科技在苏州自建了数据中心", "attributed_to": "user"},
        {"text": "润泽科技在苏州自建了数据中心", "attributed_to": "user"},
    ]
    coordinator = _write_coordinator(extracted, captured)
    try:
        out = coordinator.add(
            [{"role": "user", "content": "润泽科技的情况"}], user_id="u1", infer=True
        )
    finally:
        coordinator_module.embed_texts = captured["restore_embed"]
        coordinator_module.extract_entities = captured["restore_entities"]

    check("两条重复文本都写入了向量库", len(captured.get("upserted", [])), 2)
    check("历史表也各记一条", len(captured.get("history", [])), 2)
    check("返回两条记忆", len(out), 2)
    check("抽取过程没有全量遍历记忆", bool(captured.get("scroll_called")), False)


def main():
    for test in (
        test_reference_comes_from_vector_search,
        test_query_text_is_built_from_batch_and_bounded,
        test_huge_single_message_still_produces_query,
        test_recall_failure_does_not_break_extraction,
        test_duplicate_texts_are_written_without_hash_dedup,
    ):
        test()

    print(f"\n通过 {passed} 项，失败 {len(failed)} 项")
    for item in failed:
        print("  [FAIL]", item)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
