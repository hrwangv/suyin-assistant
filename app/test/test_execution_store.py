"""执行记录（PostgreSQL）自检：离线跑，不依赖真的数据库。

用法：
    PYTHONPATH=. python app/test/test_execution_store.py

做法：用一个假的连接对象替换真连接，验证 SQL 与参数拼装、降级行为、State 摘要。
真库连通性不在这个脚本里验（那属于部署验证，见 docs 里的两条命令）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.agent.services import execution_store  # noqa: E402


class _FakeCursor:
    def __init__(self, store: dict):
        self.store = store
        self.description = []
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.store.setdefault("sql", []).append(sql)
        if params is not None:
            self.store.setdefault("params", []).append(params)
        # 造两条"查询结果"，让 recent_runs 能拼出行
        if sql.strip().upper().startswith("SELECT"):
            self.description = [
                type("C", (), {"name": name})()
                for name in (
                    "id", "thread_id", "user_id", "turn_kind", "status",
                    "request_text", "attachments", "actions", "answer",
                    "error", "duration_ms", "created_at",
                )
            ]
            self._rows = [
                (2, "t1", "u1", "chat", "completed", "提取地址", [], ["document_skill"], "已提取", None, 1200, "2026-09-25"),
            ]

    def fetchall(self):
        return self._rows


class _FakeConnection:
    def __init__(self):
        self.calls: dict = {}
        self.closed = False

    def cursor(self):
        return _FakeCursor(self.calls)


def _with_fake_connection(configured: bool = True):
    """临时把连接塞进去（跳过 PG 配置与 psycopg 的真实连接）。"""
    fake = _FakeConnection()
    original_conn = execution_store._connection
    original_ready = execution_store._schema_ready
    execution_store._connection = fake
    execution_store._schema_ready = True
    return fake, lambda: (
        setattr(execution_store, "_connection", original_conn),
        setattr(execution_store, "_schema_ready", original_ready),
    )


def test_record_run_writes():
    fake, restore = _with_fake_connection()
    try:
        ok = execution_store.record_run(
            thread_id="t1",
            user_id="u1",
            turn_kind="chat",
            status="completed",
            request_text="提取邮寄地址",
            attachments=[{"file_id": "f1", "filename": "询证函.png"}],
            actions=["document_skill", "extract_mailing_address"],
            answer="地址是江苏省南京市…" * 500,  # 超长，落库时应被截断
            duration_ms=1234,
        )
    finally:
        restore()

    assert ok is True
    sql = fake.calls["sql"][0]
    assert "INSERT INTO agent_run" in sql, sql
    params = fake.calls["params"][0]
    assert params[0] == "t1" and params[2] == "chat" and params[3] == "completed"
    assert len(params[7]) <= 2000, "超长回答应被截断后再落库"
    print("[PASS] record_run：SQL 与参数拼装、超长回答截断 OK")


def test_record_run_degrades_when_unconfigured():
    """没配 PG：返回 False，不抛异常（业务照跑）。"""
    original = execution_store._connection
    execution_store._connection = None
    execution_store.reset_connection()  # 清掉 warn 标记，模拟冷启动
    try:
        ok = execution_store.record_run(thread_id="t1", status="completed")
    finally:
        execution_store._connection = original
    assert ok is False
    print("[PASS] 未配置 PG → 静默降级（返回 False，不抛错）OK")


def test_recent_runs_maps_columns():
    fake, restore = _with_fake_connection()
    try:
        rows = execution_store.recent_runs(limit=5, thread_id="t1")
    finally:
        restore()

    assert len(rows) == 1, rows
    assert rows[0]["thread_id"] == "t1" and rows[0]["status"] == "completed"
    assert "ORDER BY id DESC LIMIT %s" in fake.calls["sql"][0]
    assert fake.calls["params"][0] == ("t1", 5)
    print("[PASS] recent_runs：按 thread 过滤 + 列名映射 OK")


def test_summarize_state():
    normal = execution_store.summarize_state(
        {
            "answer": "地址已提取",
            "agent_results": {"document_skill": {}, "answer": {}},
            "resolved_action": "extract_mailing_address",
        }
    )
    assert normal["status"] == "completed"
    assert normal["actions"] == ["answer", "document_skill", "extract_mailing_address"], normal
    assert normal["answer"] == "地址已提取"

    interrupted = execution_store.summarize_state(
        {"__interrupt__": [object()], "agent_results": {"document_skill": {}}}
    )
    assert interrupted["status"] == "interrupted", interrupted

    failed = execution_store.summarize_state(
        {"agent_result": {"status": "failed", "reason": "ocr_failed: 请求超时"}}
    )
    assert failed["status"] == "failed" and "请求超时" in failed["error"], failed

    assert execution_store.summarize_state(None)["status"] == "completed"
    print("[PASS] summarize_state：completed / interrupted / failed 三态 OK")


def main() -> None:
    test_record_run_writes()
    test_record_run_degrades_when_unconfigured()
    test_recent_runs_maps_columns()
    test_summarize_state()
    print("\n执行记录自检全部通过 ✅")


if __name__ == "__main__":
    main()
