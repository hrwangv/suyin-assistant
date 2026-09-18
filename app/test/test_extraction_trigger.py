"""长期记忆抽取触发逻辑测试。

覆盖水位机制的核心行为（都是之前分析里点过的边界）：
1. 没到阈值不抽（省钱的关键）
2. 到阈值抽一次，批次区间正确
3. 刚抽完再触发是空操作（水位已推进）
4. 残余 < 2 条不抽（只有提问没回答）
5. 拿不到锁时跳过（并发保护）
6. 抽取失败时水位不推进（下次重试）

不连 Redis / MySQL / 大模型，全部用假对象。
运行：python -m app.test.test_extraction_trigger
"""
from __future__ import annotations

from types import SimpleNamespace

import app.memory.extraction_trigger as trigger
from app.memory.config import EXTRACT_MIN_BATCH, EXTRACT_TRIGGER_NEW_MESSAGES

passed = 0
failed: list[str] = []


def check(name: str, actual, expected):
    global passed
    if actual == expected:
        passed += 1
    else:
        failed.append(f"{name}\n     期望：{expected}\n     实际：{actual}")


class FakeStore:
    """用 id 连续的消息模拟一张表。"""

    def __init__(self, messages):
        self.messages = messages          # [{"id":1,"role":...,"content":...}, ...]

    def count_since(self, scope, after_id):
        return len([m for m in self.messages if m["id"] > after_id])

    def get_since(self, scope, after_id, limit):
        rows = [m for m in self.messages if m["id"] > after_id][:limit]
        return [SimpleNamespace(**m) for m in rows]

    def get_before(self, scope, before_id, limit):
        rows = [m for m in self.messages if m["id"] < before_id][-limit:]
        return [SimpleNamespace(**m) for m in rows]


class FakeCache:
    def __init__(self, watermark=0):
        self.watermark = watermark

    def get_extract_watermark(self, scope):
        return self.watermark

    def set_extract_watermark(self, scope, message_id):
        self.watermark = int(message_id)


class FakeLock:
    def __init__(self, available=True):
        self.available = available
        self.acquired = 0
        self.released = 0

    def acquire(self, scope, ttl=None):
        if not self.available:
            return False
        self.acquired += 1
        return True

    def release(self, scope):
        self.released += 1


class InlineThread:
    """把异步执行变成同步，方便断言。"""

    def __init__(self, target=None, args=(), daemon=None, **kwargs):
        self._target, self._args = target, args

    def start(self):
        if self._target:
            self._target(*self._args)


def _messages(n, start_id=1):
    return [
        {"id": start_id + i, "role": "user" if i % 2 == 0 else "assistant",
         "content": f"消息{start_id + i}", "name": None}
        for i in range(n)
    ]


def _install(messages, watermark=0, lock_available=True, extract_ok=True):
    """装好假依赖，返回 (store, cache, lock, 记录调用信息的容器)。"""
    store, cache = FakeStore(messages), FakeCache(watermark)
    lock = FakeLock(lock_available)
    calls = {"batches": []}

    class FakeCoordinator:
        def add(self, batch, **kwargs):
            calls["batches"].append(batch)
            calls["context"] = kwargs.get("context")
            if not extract_ok:
                raise RuntimeError("抽取失败")
            return [{"id": "m1"}]

    trigger._get_store = lambda: store
    trigger._get_cache = lambda: cache
    trigger._get_lock = lambda: lock

    import app.memory.coordinator as coordinator_module
    original = coordinator_module.get_memory_coordinator
    coordinator_module.get_memory_coordinator = lambda: FakeCoordinator()

    original_thread = trigger.threading.Thread
    trigger.threading.Thread = InlineThread
    calls["restore"] = (coordinator_module, original, original_thread)
    return store, cache, lock, calls


def _restore(calls):
    coordinator_module, original, original_thread = calls["restore"]
    coordinator_module.get_memory_coordinator = original
    trigger.threading.Thread = original_thread


def test_lead_in_context_from_real_conversation():
    """前导上下文必须来自真实会话（run scope）的批次之前那一小段。"""
    from app.memory.config import EXTRACTION_CONTEXT_MESSAGES

    msgs = _messages(EXTRACT_TRIGGER_NEW_MESSAGES + 2)
    _, cache, lock, calls = _install(msgs, watermark=EXTRACT_TRIGGER_NEW_MESSAGES)
    try:
        trigger.maybe_trigger_extraction("run:s1", "user:u1", force=True)
    finally:
        _restore(calls)

    context = calls.get("context") or []
    check("带上了前导上下文", len(context), EXTRACTION_CONTEXT_MESSAGES)
    check("前导内容 = 批次之前那一小段",
          [m["content"] for m in context],
          [f"消息{i}" for i in range(EXTRACT_TRIGGER_NEW_MESSAGES - EXTRACTION_CONTEXT_MESSAGES + 1,
                                    EXTRACT_TRIGGER_NEW_MESSAGES + 1)])
    check("批次不含前导内容（不重复喂）",
          set(m["content"] for m in context).isdisjoint(set(m["content"] for m in calls["batches"][0])),
          True)


def test_first_batch_has_no_lead_in():
    """第一批（水位 = 0，前面没有消息）不带前导上下文。"""
    msgs = _messages(EXTRACT_TRIGGER_NEW_MESSAGES)
    _, cache, lock, calls = _install(msgs)
    try:
        trigger.maybe_trigger_extraction("run:s1", "user:u1")
    finally:
        _restore(calls)
    check("第一批没有前导上下文", calls.get("context"), [])


def test_below_threshold_does_nothing():
    """没到阈值不抽：短期记忆攒到 10 条（< 40）时应该是空操作。"""
    msgs = _messages(10)
    _, cache, lock, calls = _install(msgs)
    try:
        triggered = trigger.maybe_trigger_extraction("run:s1", "user:u1")
    finally:
        _restore(calls)
    check("未触发", triggered, False)
    check("没有调用抽取", calls["batches"], [])
    check("没有加锁", lock.acquired, 0)
    check("水位没变", cache.watermark, 0)


def test_reaches_threshold_triggers_once():
    """攒够阈值触发一次，批次区间正确、水位推进到批次末尾。"""
    msgs = _messages(EXTRACT_TRIGGER_NEW_MESSAGES)
    _, cache, lock, calls = _install(msgs)
    try:
        triggered = trigger.maybe_trigger_extraction("run:s1", "user:u1")
    finally:
        _restore(calls)
    check("触发了", triggered, True)
    check("抽取了一次", len(calls["batches"]), 1)
    check("批次条数 = 阈值", len(calls["batches"][0]), EXTRACT_TRIGGER_NEW_MESSAGES)
    check("水位推进到批次末尾", cache.watermark, EXTRACT_TRIGGER_NEW_MESSAGES)
    check("锁被释放", lock.released, 1)


def test_just_extracted_then_trigger_again_is_noop():
    """刚抽完再触发（比如立刻点新建对话）→ 空操作，这正是水位要解决的问题。"""
    msgs = _messages(EXTRACT_TRIGGER_NEW_MESSAGES)
    _, cache, lock, calls = _install(msgs, watermark=EXTRACT_TRIGGER_NEW_MESSAGES)
    try:
        # force=True 模拟「新建对话」收尾
        triggered = trigger.maybe_trigger_extraction("run:s1", "user:u1", force=True)
    finally:
        _restore(calls)
    check("未触发", triggered, False)
    check("没有调用抽取", calls["batches"], [])


def test_new_messages_after_extraction_are_picked_up():
    """抽过之后又来了一批，force 模式下只抽新增的那部分（不重复）。"""
    msgs = _messages(EXTRACT_TRIGGER_NEW_MESSAGES + 4)
    _, cache, lock, calls = _install(msgs, watermark=EXTRACT_TRIGGER_NEW_MESSAGES)
    try:
        triggered = trigger.maybe_trigger_extraction("run:s1", "user:u1", force=True)
    finally:
        _restore(calls)
    check("触发了", triggered, True)
    check("批次只含新增的 4 条", len(calls["batches"][0]), 4)
    check("水位推进到末尾", cache.watermark, EXTRACT_TRIGGER_NEW_MESSAGES + 4)


def test_single_message_is_skipped():
    """只有 1 条（提问成功但回答失败）→ 不抽，避免抽出垃圾记忆。"""
    msgs = _messages(1)
    _, cache, lock, calls = _install(msgs)
    try:
        triggered = trigger.maybe_trigger_extraction("run:s1", "user:u1", force=True)
    finally:
        _restore(calls)
    check("未触发", triggered, False)
    check("最小批量配置正确", EXTRACT_MIN_BATCH, 2)


def test_lock_held_skips():
    """拿不到锁（已有任务在跑）→ 跳过，不重复抽取。"""
    msgs = _messages(EXTRACT_TRIGGER_NEW_MESSAGES)
    _, cache, lock, calls = _install(msgs, lock_available=False)
    try:
        triggered = trigger.maybe_trigger_extraction("run:s1", "user:u1")
    finally:
        _restore(calls)
    check("未触发", triggered, False)
    check("没有调用抽取", calls["batches"], [])


def test_failure_keeps_watermark():
    """抽取失败时水位不推进 —— 下次触发会重试这批。

    注意：写入侧已不再做 md5 精确去重（见 coordinator._add_with_extraction），
    所以重试时若模型输出了和上次不完全相同的文本，会有重复记忆入库。
    """
    msgs = _messages(EXTRACT_TRIGGER_NEW_MESSAGES)
    _, cache, lock, calls = _install(msgs, extract_ok=False)
    try:
        triggered = trigger.maybe_trigger_extraction("run:s1", "user:u1")
    finally:
        _restore(calls)
    check("触发了（尝试过）", triggered, True)
    check("水位没有推进", cache.watermark, 0)
    check("锁仍然被释放", lock.released, 1)


def test_coordinator_uses_passed_context_only():
    """抽取的上下文由调用方传入；coordinator 不再自己读短期记忆、也不回写影子副本。

    这是本次修复的核心：以前 coordinator 用长期记忆的 scope（user:xxx）去读短期记忆表，
    读到的是"影子副本"，会把别的对话的内容当成"最近对话"混进抽取。
    """
    import app.memory.coordinator as coordinator_module

    captured = {}

    class ExplodingRecent:
        def get_messages(self, *a, **k):
            raise AssertionError("coordinator 不应再自己读短期记忆")

        def add_messages(self, *a, **k):
            raise AssertionError("coordinator 不应再往短期记忆写影子副本")

    class FakeVector:
        def scroll_active(self, scope):
            return []

        def upsert(self, *a, **k):
            pass

    class FakeExtractor:
        def extract(self, new_messages, existing_memories, last_messages):
            captured["context"] = [m["content"] for m in last_messages]
            return {"memory": []}

    coordinator = coordinator_module.MemoryCoordinator.__new__(coordinator_module.MemoryCoordinator)
    coordinator.recent_service = ExplodingRecent()
    coordinator.vector_store = FakeVector()
    coordinator.extractor = FakeExtractor()
    coordinator.history_store = None
    coordinator.entity_store = None

    coordinator.add(
        [{"role": "user", "content": "本批消息"}],
        user_id="u1",
        infer=True,
        context=[
            {"role": "user", "content": "前导1"},
            {"role": "assistant", "content": "前导2"},
        ],
    )

    check("上下文来自调用方传入", captured["context"], ["前导1", "前导2"])


def main():
    for test in (
        test_below_threshold_does_nothing,
        test_reaches_threshold_triggers_once,
        test_just_extracted_then_trigger_again_is_noop,
        test_new_messages_after_extraction_are_picked_up,
        test_single_message_is_skipped,
        test_lock_held_skips,
        test_failure_keeps_watermark,
        test_lead_in_context_from_real_conversation,
        test_first_batch_has_no_lead_in,
        test_coordinator_uses_passed_context_only,
    ):
        test()

    print(f"\n通过 {passed} 项，失败 {len(failed)} 项")
    for item in failed:
        print("  [FAIL]", item)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
