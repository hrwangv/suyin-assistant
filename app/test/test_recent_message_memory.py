"""短期记忆（最近消息）读取、兜底与降级行为测试。

覆盖本轮修复的点：
1. Redis 有数据就直接命中，不再要求「凑够 limit 条」
   （否则 30 条容量配 50 条读取上限会永远回源，缓存形同虚设）；
2. 缓存未命中 / 读失败时回源 MySQL 并重建缓存；
3. MySQL 的 keep 来自配置（默认与 Redis 容量对齐），不再是写死的 10。
4. MySQL 不可用时的降级：构造函数不连库、读降级为空历史、写降级不上抛
   （失败要清 Redis 缓存）、服务构造失败也返回可用对象。
5. 助手回答写失败时，回答节点仍正常收尾（add_done_task 照常执行）。
6. 真异常推给前端的文案不含 SQL / 库表名。

不依赖 Redis / MySQL，全部用假对象。
运行：python -m app.test.test_recent_message_memory
"""
from __future__ import annotations

from app.memory.models import RecentMessage
from app.memory.recent_message_service import RecentMessageService
import app.memory.storage.recent_message_store as store_module

passed = 0
failed: list[str] = []


def check(name: str, actual, expected):
    global passed
    if actual == expected:
        passed += 1
    else:
        failed.append(f"{name}\n     期望：{expected}\n     实际：{actual}")


def make_messages(n: int) -> list[dict]:
    return [{"role": "user", "content": f"第{i}条"} for i in range(1, n + 1)]


class FakeCache:
    def __init__(self, size: int, fail_read=False, fail_write=False):
        self.msgs = make_messages(size)
        self.rebuilt_with: list[int] = []
        self.cleared = 0
        self.fail_read = fail_read
        self.fail_write = fail_write

    def get_messages(self, scope, limit):
        if self.fail_read:
            raise RuntimeError("redis down")
        return self.msgs[-limit:] if limit > 0 else []

    def rebuild_messages(self, scope, msgs):
        self.rebuilt_with.append(len(msgs))
        self.msgs = list(msgs)

    def add_messages(self, scope, msgs):
        if self.fail_write:
            raise RuntimeError("redis write failed")
        self.msgs += list(msgs)

    def clear_messages(self, scope):
        self.cleared += 1
        self.msgs = []


class FakeStore:
    def __init__(self, size: int):
        self.msgs = make_messages(size)
        self.read_calls = 0

    def add(self, message: RecentMessage):
        self.msgs.append({"role": message.role, "content": message.content})

    def get_recent(self, scope, limit):
        self.read_calls += 1
        return [
            RecentMessage(session_scope=scope, role=m["role"], content=m["content"])
            for m in self.msgs[-limit:]
        ]


def test_cache_hit_even_when_fewer_than_limit():
    """核心修复：缓存 30 条 + 读取上限 50，必须直接命中，不能回源。"""
    cache, store = FakeCache(30), FakeStore(10)
    service = RecentMessageService(cache=cache, store=store)

    out = service.get_messages("run:t", 50)

    check("返回缓存里的全部 30 条", len(out), 30)
    check("没有回源 MySQL", store.read_calls, 0)
    check("没有重建缓存", cache.rebuilt_with, [])
    check("缓存条数没被压缩", len(cache.msgs), 30)


def test_short_conversation_also_hits_cache():
    """短对话（只有 3 条）同样应该命中缓存，不该白跑一次 MySQL。"""
    cache, store = FakeCache(3), FakeStore(3)
    service = RecentMessageService(cache=cache, store=store)

    out = service.get_messages("run:t", 50)

    check("返回 3 条", len(out), 3)
    check("没有回源 MySQL", store.read_calls, 0)


def test_cache_miss_falls_back_and_rebuilds():
    """缓存为空（未命中 / 已过期）时回源 MySQL 并重建缓存。"""
    cache, store = FakeCache(0), FakeStore(30)
    service = RecentMessageService(cache=cache, store=store)

    out = service.get_messages("run:t", 50)

    check("回源拿到 30 条", len(out), 30)
    check("回源了一次", store.read_calls, 1)
    check("用 30 条重建了缓存", cache.rebuilt_with, [30])
    check("缓存被重新填充", len(cache.msgs), 30)


def test_cache_read_error_falls_back():
    """Redis 读异常时回源，不影响主流程。"""
    cache, store = FakeCache(30, fail_read=True), FakeStore(30)
    service = RecentMessageService(cache=cache, store=store)

    out = service.get_messages("run:t", 50)

    check("异常后仍返回数据", len(out), 30)
    check("回源了一次", store.read_calls, 1)


def test_redis_write_failure_clears_cache():
    """Redis 写失败 → 清空缓存，让下次读取回源重建（自愈）。"""
    from app.memory.config import CONTEXT_CACHE_CAPACITY

    cache, store = FakeCache(30, fail_write=True), FakeStore(30)
    service = RecentMessageService(cache=cache, store=store)

    service.add_messages("run:t", [{"role": "user", "content": "新消息"}])

    check("MySQL 写入成功", store.msgs[-1]["content"], "新消息")
    check("缓存被清空", cache.cleared, 1)
    check("缓存里没有残留", len(cache.msgs), 0)

    # 清空后再读 → 回源 MySQL，把完整数据捞回来
    service.get_messages("run:t", 50)
    # 回源时按缓存容量 CONTEXT_CACHE_CAPACITY 截取：库里 31 条，
    # 容量够大就全部重建，否则截到容量上限
    check("重建后缓存条数 = min(库里条数, 缓存容量)",
          len(cache.msgs), min(31, CONTEXT_CACHE_CAPACITY))


def test_mysql_keep_comes_from_config():
    """MySQL 的 keep 不再是硬编码 10，默认取配置。"""
    from app.memory.config import CONTEXT_CACHE_CAPACITY, CONTEXT_MYSQL_KEEP

    # 避开真实数据库连接，只验证 keep 的取值逻辑
    original_engine = store_module.get_mysql_engine
    store_module.get_mysql_engine = lambda: object()
    try:
        check("配置项是整数", isinstance(CONTEXT_MYSQL_KEEP, int), True)
        check("默认与 Redis 容量对齐", CONTEXT_MYSQL_KEEP, CONTEXT_CACHE_CAPACITY)
        check("三个条数配置已统一为 50", (CONTEXT_MYSQL_KEEP, CONTEXT_CACHE_CAPACITY), (50, 50))
        check("store 默认沿用配置", store_module.RecentMessageStore().keep, CONTEXT_MYSQL_KEEP)
        check("显式传参可覆盖", store_module.RecentMessageStore(keep=5).keep, 5)
    finally:
        store_module.get_mysql_engine = original_engine


class FailingStore:
    """模拟 MySQL 不可用：读写都抛异常。"""

    def __init__(self):
        self.add_calls = 0
        self.read_calls = 0

    def add(self, message):
        self.add_calls += 1
        raise RuntimeError("MySQL server has gone away")

    def get_recent(self, scope, limit):
        self.read_calls += 1
        raise RuntimeError("Can't connect to MySQL server")

    def clear_scope(self, scope):
        raise RuntimeError("Can't connect to MySQL server")


def test_store_construction_does_not_connect():
    """构造函数不许连库/建表（已移到启动时），否则 MySQL 一挂连构造都过不去。"""

    class DummyEngine:
        """任何真实使用都会失败：能构造出 store 就说明构造函数没碰数据库。"""

        def __getattr__(self, name):
            raise AssertionError(f"构造函数不该使用 engine.{name}")

    original_engine = store_module.get_mysql_engine
    store_module.get_mysql_engine = lambda: DummyEngine()
    try:
        store = store_module.RecentMessageStore()
        check("构造函数不连库也能成功", store.keep > 0, True)
    finally:
        store_module.get_mysql_engine = original_engine


def test_mysql_read_failure_degrades_to_empty_history():
    """MySQL 回源失败 → 降级为空历史，不上抛（问答继续）。"""
    cache, store = FakeCache(0), FailingStore()
    service = RecentMessageService(cache=cache, store=store)

    out = service.get_messages("run:t", 50)

    check("回源尝试过", store.read_calls, 1)
    check("降级为空历史", out, [])


def test_mysql_write_failure_is_swallowed_and_clears_cache():
    """MySQL 写失败 → 不上抛、不写 Redis、清空缓存（避免读到缺条目的旧历史）。"""
    cache, store = FakeCache(30), FailingStore()
    service = RecentMessageService(cache=cache, store=store)

    service.add_messages("run:t", [{"role": "user", "content": "本轮问题"}])

    check("尝试写过 MySQL", store.add_calls, 1)
    check("写失败不上抛", True, True)
    check("缓存被清空", cache.cleared, 1)
    check("缓存里没有残留（本轮消息没进缓存）", len(cache.msgs), 0)


def test_factory_degrades_when_construction_fails():
    """服务构造失败时返回降级实现，不把异常抛进问答主流程。"""
    import app.memory.recent_message_service as service_module

    original_cls = service_module.RecentMessageService
    original_service = service_module._recent_message_service
    original_degraded = service_module._degraded_service

    class BoomService:
        def __init__(self):
            raise RuntimeError("坏配置：Redis URL 不合法")

    service_module.RecentMessageService = BoomService
    service_module._recent_message_service = None
    service_module._degraded_service = None
    try:
        service = service_module.get_recent_message_service()
        check("构造失败也拿到服务对象", service is not None, True)
        check("读降级为空历史", service.get_messages("run:t", 10), [])
        service.add_messages("run:t", [{"role": "user", "content": "x"}])
        check("写降级不上抛", True, True)
        check("清空降级返回 0", service.clear("run:t"), 0)
    finally:
        service_module.RecentMessageService = original_cls
        service_module._recent_message_service = original_service
        service_module._degraded_service = original_degraded


def test_answer_node_survives_history_write_failure():
    """助手回答写失败时，节点仍正常收尾：add_done_task 照常执行。"""
    import app.rag.query_process.agent.nodes.node_answer_output as answer_node
    from app.utils.sse_utils import create_sse_queue
    from app.utils.task_utils import get_done_task_list

    class FailingService:
        def add_messages(self, scope, messages):
            raise RuntimeError("MySQL down")

        def get_messages(self, scope, limit=None):
            raise RuntimeError("MySQL down")

    original_service = answer_node.get_recent_message_service
    original_extract = answer_node.step_6_extract_long_term_memory
    answer_node.get_recent_message_service = lambda: FailingService()
    answer_node.step_6_extract_long_term_memory = lambda state: None  # 不在测试里起后台线程
    sid = "test_write_fail"
    create_sse_queue(sid)
    try:
        state = {"session_id": sid, "answer": "已经生成好的答案", "is_stream": True}
        out = answer_node.node_answer_output(state)
        check("节点正常返回", out.get("answer"), "已经生成好的答案")
        check("add_done_task 照常执行", "生成答案" in get_done_task_list(sid), True)
    finally:
        answer_node.get_recent_message_service = original_service
        answer_node.step_6_extract_long_term_memory = original_extract


def test_error_event_does_not_leak_sql():
    """真异常推给前端的只有统一文案，不带 SQL / 库表名。"""
    import app.api.query_server as query_server
    import app.utils.sse_utils as sse_utils

    class BoomApp:
        def invoke(self, state):
            raise RuntimeError(
                "INSERT INTO memory_recent_messages ... [SQL: INSERT INTO memory_recent_messages]"
            )

    original_app = query_server.query_app
    sid = "test_error_copy"
    queue = sse_utils.create_sse_queue(sid)
    query_server.query_app = BoomApp()
    try:
        query_server.run_query_graph("测试问题", sid, True, "u1")
        events = []
        while not queue.empty():
            events.append(queue.get())
    finally:
        query_server.query_app = original_app

    error_payloads = [str(e["data"]) for e in events if e["event"] == "error"]
    check("推了一个 error 事件", len(error_payloads), 1)
    check("文案统一", error_payloads[0] if error_payloads else "",
          f"{{'error': '{query_server.USER_FACING_ERROR_MESSAGE}'}}")
    check("不含 SQL 关键字",
          any(("SQL" in p) or ("memory_recent_messages" in p) for p in error_payloads), False)
    check("会话被标记 failed",
          any(e["event"] == "progress" and e["data"].get("status") == "failed" for e in events), True)


def main():
    for test in (
        test_cache_hit_even_when_fewer_than_limit,
        test_short_conversation_also_hits_cache,
        test_cache_miss_falls_back_and_rebuilds,
        test_cache_read_error_falls_back,
        test_redis_write_failure_clears_cache,
        test_mysql_keep_comes_from_config,
        test_store_construction_does_not_connect,
        test_mysql_read_failure_degrades_to_empty_history,
        test_mysql_write_failure_is_swallowed_and_clears_cache,
        test_factory_degrades_when_construction_fails,
        test_answer_node_survives_history_write_failure,
        test_error_event_does_not_leak_sql,
    ):
        test()

    print(f"\n通过 {passed} 项，失败 {len(failed)} 项")
    for item in failed:
        print("  [FAIL]", item)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
