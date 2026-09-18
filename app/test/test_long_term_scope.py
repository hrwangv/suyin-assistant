"""长期记忆 scope 的用户级改造测试。

核心保证：**写入和读取必须用同一个 scope**。
如果一边按 user 写、另一边按 session 读，长期记忆永远读不出来，
而且这种问题不会报错，只会表现为"记忆好像没生效"，很难排查。

不依赖 Redis / MySQL / Qdrant，全部用假对象。
运行：python -m app.test.test_long_term_scope
"""
from __future__ import annotations

from app.memory.config import MEMORY_DEFAULT_USER_ID
from app.memory.utils.scope import build_long_term_scope, parse_scope
from app.rag.query_process.agent.state import create_query_default_state
import app.rag.query_process.agent.nodes.node_answer_output as answer_node

passed = 0
failed: list[str] = []


def check(name: str, actual, expected):
    global passed
    if actual == expected:
        passed += 1
    else:
        failed.append(f"{name}\n     期望：{expected}\n     实际：{actual}")


def test_scope_priority():
    """三级优先级：请求带的 user_id > 配置的默认用户 > 会话级兜底。"""
    check("请求带 user_id 时按用户聚合",
          build_long_term_scope(user_id="u-1", default_user_id="default-user", run_id="s-1"),
          "user:u-1")
    check("只有默认用户时也按用户聚合",
          build_long_term_scope(default_user_id="default-user", run_id="s-1"),
          "user:default-user")
    check("都没有时退化为会话级",
          build_long_term_scope(run_id="s-1"),
          "run:s-1")
    check("反解 scope 能得到 user_id",
          parse_scope("user:default-user"),
          {"user_id": "default-user"})


def test_state_carries_user_id():
    """user_id 能从接口一路带进 state。"""
    state = create_query_default_state(
        session_id="s-1", user_id="u-1", original_query="问题")
    check("state.user_id", state.get("user_id"), "u-1")


class FakeCoordinator:
    """只记录检索时收到的 scope，不碰真实存储。"""

    def __init__(self):
        self.read_scope = None

    def search(self, query, scope, top_k=None, rerank=None):
        self.read_scope = scope
        return {"long_term_memories": []}


def _capture_trigger_scope(state, fake):
    """跑一轮「读取 + 触发」，返回触发时传给抽取的 scope 参数。

    抽取本身已经搬到 app/memory/extraction_trigger.py（带水位判断 + 异步线程），
    这里只需验证「读取用的 scope」和「触发时交给抽取的 scope」一致。
    """
    captured = {}

    def fake_trigger(run_scope, long_term_scope, force=False):
        captured["run_scope"] = run_scope
        captured["long_term_scope"] = long_term_scope
        return False

    original_coordinator = answer_node.get_memory_coordinator
    original_trigger = answer_node.maybe_trigger_extraction
    answer_node.get_memory_coordinator = lambda: fake
    answer_node.maybe_trigger_extraction = fake_trigger
    try:
        answer_node.step_2_load_long_term_memory(state)
        answer_node.step_6_extract_long_term_memory(state)
    finally:
        answer_node.get_memory_coordinator = original_coordinator
        answer_node.maybe_trigger_extraction = original_trigger
    return captured


def test_read_and_write_use_same_scope():
    """同一轮里，长期记忆的读取 scope 必须等于抽取写入的 scope。"""
    fake = FakeCoordinator()
    state = {
        "session_id": "s-1",
        "user_id": "",
        "original_query": "欣旺达今年的情况",
        "rewritten_query": "欣旺达今年的情况",
        "answer": "欣旺达今年新增产能……",
    }
    captured = _capture_trigger_scope(state, fake)

    check("读取用了用户级 scope", fake.read_scope, f"user:{MEMORY_DEFAULT_USER_ID}")
    check("抽取写入用同一个 scope", captured.get("long_term_scope"), fake.read_scope)
    check("水位维度是会话级 scope", captured.get("run_scope"), "run:s-1")


def test_request_user_id_wins():
    """请求里带了 user_id 时，读写都用它。"""
    fake = FakeCoordinator()
    state = {
        "session_id": "s-2",
        "user_id": "u-999",
        "original_query": "问题",
        "rewritten_query": "问题",
        "answer": "回答",
    }
    captured = _capture_trigger_scope(state, fake)

    check("读取用请求里的 user_id", fake.read_scope, "user:u-999")
    check("抽取写入一致", captured.get("long_term_scope"), fake.read_scope)


def test_query_api_passes_user_id():
    """接口层贯通：POST /query 的 user_id 要能传到图执行函数。"""
    from fastapi.testclient import TestClient
    import app.api.query_server as server

    captured = {}
    original = server.run_query_graph
    server.run_query_graph = (
        lambda query, session_id, is_stream, user_id=None: captured.update(
            {"query": query, "session_id": session_id, "user_id": user_id}
        )
    )
    try:
        client = TestClient(server.app)
        # 带 user_id
        client.post("/query", json={
            "query": "你好", "session_id": "s-1", "is_stream": False, "user_id": "admin"})
        check("接口把 user_id 传下去了", captured.get("user_id"), "admin")
        check("session_id 正常透传", captured.get("session_id"), "s-1")

        # 不带 user_id（旧前端）→ 传 None，后端走默认用户兜底
        client.post("/query", json={"query": "你好", "session_id": "s-2", "is_stream": False})
        check("不传 user_id 时为 None", captured.get("user_id"), None)
    finally:
        server.run_query_graph = original


def test_search_is_long_term_only():
    """search() 只做长期记忆检索：不读短期记忆，返回体也只有 long_term_memories。

    这条约束是防回归的：早期 search() 会顺带读一次短期上下文，
    但主链路根本不消费它，而且 scope 语义不同（短期写在 run:xxx 下，
    这里传的是长期记忆的 user:xxx scope），读出来恒为空。
    """
    import app.memory.coordinator as coordinator_module

    class ExplodingRecentService:
        """只要被调用就报错，用来证明 search 不再碰短期记忆。"""

        def get_messages(self, *args, **kwargs):
            raise AssertionError("search() 不应该再读取短期记忆")

    class FakeVectorStore:
        # 长期记忆现在走纯稠密检索（search），不再走 search_hybrid
        def search(self, dense, scope, limit):
            return []

        def update_stats(self, *args, **kwargs):
            pass

    coordinator = coordinator_module.MemoryCoordinator.__new__(coordinator_module.MemoryCoordinator)
    coordinator.recent_service = ExplodingRecentService()
    coordinator.vector_store = FakeVectorStore()
    coordinator.entity_store = object()

    # 避开真实的 embedding / 实体抽取调用
    original_embed = coordinator_module.embed_texts
    original_extract = coordinator_module.extract_entities
    coordinator_module.embed_texts = lambda texts: ([[0.1] * 4] * len(texts), [{}] * len(texts))
    coordinator_module.extract_entities = lambda *a, **k: []
    try:
        result = coordinator.search(query="欣旺达的动态", scope="user:u1")
    finally:
        coordinator_module.embed_texts = original_embed
        coordinator_module.extract_entities = original_extract

    check("返回体只含长期记忆", sorted(result.keys()), ["long_term_memories"])
    check("短期记忆没有被读取", True, True)   # 没抛 AssertionError 即通过


def main():
    for test in (
        test_scope_priority,
        test_state_carries_user_id,
        test_read_and_write_use_same_scope,
        test_request_user_id_wins,
        test_query_api_passes_user_id,
        test_search_is_long_term_only,
    ):
        test()

    print(f"\n通过 {passed} 项，失败 {len(failed)} 项")
    for item in failed:
        print("  [FAIL]", item)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
