"""Agent 执行记录（PostgreSQL）：每轮对话落一行。

记什么：谁、问什么、带了什么附件、调了哪些能力、结果如何、耗时多少、报了什么错。
用途：线上排查（"这个 thread 昨天为什么没出地址"）、统计（各能力调用量与失败率）。

三条纪律：
    1. 不阻塞业务——写库失败只记日志，绝不影响这一轮的返回；
    2. 不在导入期建连——第一次真要写时才连接并建表（与 MCP 那条纪律一致）；
    3. 没配 PG 就静默降级——本地开发不用装数据库也能跑。

连接信息见 `app/conf/pg_config.py`（.env 里的 PG_DSN 或 PG_HOST 等分项）。
"""
import time
from typing import Any, Optional

from app.conf.pg_config import pg_config
from app.core.logger import logger

TABLE = "agent_run"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS {table} (
    id            BIGSERIAL PRIMARY KEY,
    thread_id     TEXT        NOT NULL,
    user_id       TEXT,
    turn_kind     TEXT,                      -- chat（新一轮）/ resume（HITL 恢复）
    status        TEXT        NOT NULL,      -- completed / interrupted / failed
    request_text  TEXT,
    attachments   JSONB,                     -- [{file_id, filename, mime_type}]
    actions       JSONB,                     -- 本轮实际跑到的能力 ["document_skill", ...]
    answer        TEXT,                      -- 最终回答（截断存储）
    error         TEXT,
    duration_ms   INTEGER,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

# 回答太长不落库（排查时看前几百字足够，避免把表撑爆）
_ANSWER_MAX_CHARS = 2000

_connection: Any = None
_schema_ready = False
_warned = False


def _get_connection():
    """拿连接（首次调用时才连 + 建表）；不可用时返回 None，由调用方静默跳过。"""
    global _connection, _schema_ready, _warned

    if _connection is not None and not getattr(_connection, "closed", False):
        return _connection
    if not pg_config.configured:
        if not _warned:
            logger.info("[execution] 未配置 PostgreSQL（PG_DSN / PG_HOST 等），执行记录不落库")
            _warned = True
        return None

    try:
        import psycopg
    except ImportError:
        if not _warned:
            logger.warning(
                "[execution] 未安装 psycopg，执行记录不落库：pip install 'psycopg[binary]'"
            )
            _warned = True
        return None

    try:
        _connection = psycopg.connect(pg_config.connection_string, autocommit=True)
        if not _schema_ready:
            with _connection.cursor() as cursor:
                cursor.execute(_SCHEMA_SQL.format(table=_qualified_table()))
                cursor.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_thread "
                    f"ON {_qualified_table()}(thread_id)"
                )
                cursor.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_created "
                    f"ON {_qualified_table()}(created_at DESC)"
                )
            _schema_ready = True
        logger.info(f"[execution] 已连接 PostgreSQL（{pg_config.describe()}）")
        return _connection
    except Exception as exc:
        if not _warned:
            logger.warning(f"[execution] 连接 PostgreSQL 失败，执行记录不落库：{exc}")
            _warned = True
        _connection = None
        return None


def _qualified_table() -> str:
    schema = pg_config.schema
    return f"{schema}.{TABLE}" if schema and schema != "public" else TABLE


def record_run(
    *,
    thread_id: str,
    user_id: str = "",
    turn_kind: str = "chat",
    status: str = "completed",
    request_text: str = "",
    attachments: Optional[list] = None,
    actions: Optional[list] = None,
    answer: str = "",
    error: str = "",
    duration_ms: int = 0,
) -> bool:
    """写入一轮执行记录。返回是否真的落库（失败不抛异常）。"""
    connection = _get_connection()
    if connection is None:
        return False

    try:
        from psycopg.types.json import Jsonb

        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO {_qualified_table()}
                    (thread_id, user_id, turn_kind, status, request_text,
                     attachments, actions, answer, error, duration_ms)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    thread_id,
                    user_id or None,
                    turn_kind,
                    status,
                    request_text or None,
                    Jsonb(attachments or []),
                    Jsonb(actions or []),
                    (answer or "")[:_ANSWER_MAX_CHARS] or None,
                    (error or "")[:1000] or None,
                    int(duration_ms) if duration_ms else None,
                ),
            )
        return True
    except Exception as exc:  # 记录失败不能影响业务
        logger.warning(f"[execution] 写执行记录失败（不影响本轮请求）：{exc}")
        return False


def recent_runs(limit: int = 20, thread_id: str = "") -> list[dict]:
    """最近的执行记录（新→旧）。查不到就返回空列表。"""
    connection = _get_connection()
    if connection is None:
        return []
    sql = (
        f"SELECT id, thread_id, user_id, turn_kind, status, request_text, "
        f"attachments, actions, answer, error, duration_ms, created_at "
        f"FROM {_qualified_table()} "
    )
    params: tuple = ()
    if thread_id:
        sql += "WHERE thread_id = %s "
        params = (thread_id,)
    sql += "ORDER BY id DESC LIMIT %s"
    params = (*params, max(1, min(int(limit or 20), 200)))

    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            columns = [item.name for item in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
    except Exception as exc:
        logger.warning(f"[execution] 读取执行记录失败：{exc}")
        return []


def summarize_state(state: Optional[dict]) -> dict:
    """从图最终 State 里抽出要落库的那几个字段。"""
    state = state or {}
    results = state.get("agent_results") or {}
    actions = sorted(results.keys())
    if state.get("resolved_action"):
        actions.append(str(state["resolved_action"]))
    if state.get("application_phase"):
        actions.append(f"application:{state['application_phase']}")

    status = "completed"
    if state.get("__interrupt__"):
        status = "interrupted"
    elif not state.get("answer") and state.get("agent_result", {}).get("status") == "failed":
        status = "failed"

    return {
        "actions": actions,
        "answer": state.get("answer") or "",
        "status": status,
        "error": str((state.get("agent_result") or {}).get("reason") or ""),
    }


def reset_connection() -> None:
    """丢掉缓存的连接（测试 / 配置热更新用）。"""
    global _connection, _schema_ready, _warned
    _connection = None
    _schema_ready = False
    _warned = False
