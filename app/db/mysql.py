"""MySQL Session Memory 表结构定义与连接管理。

这里只维护表元数据和连接，不处理具体业务读写。
"""
from sqlalchemy import (
    Column,
    Index,
    MetaData,
    Table,
    create_engine,
    text,
)
from sqlalchemy.dialects.mysql import (
    BIGINT,
    DATETIME,
    LONGTEXT,
    VARCHAR,
)

from app.conf.memory_config import MYSQL_URL
from app.core.logger import logger


metadata = MetaData()


memory_history_table = Table(
    "memory_history",
    metadata,
    Column("id", BIGINT(unsigned=True), primary_key=True, autoincrement=True),
    Column("memory_id", VARCHAR(128), nullable=False),
    Column("scope", VARCHAR(255), nullable=False),
    Column("event", VARCHAR(32), nullable=False),
    Column("old_memory", LONGTEXT(), nullable=True),
    Column("new_memory", LONGTEXT(), nullable=True),
    Column("actor_id", VARCHAR(128), nullable=True),
    Column("role", VARCHAR(32), nullable=True),
    Column("is_deleted", BIGINT(unsigned=True), nullable=False, server_default=text("0")),
    Column("created_at", DATETIME(3), nullable=False),
    Column("updated_at", DATETIME(3), nullable=False),
)


memory_recent_messages_table = Table(
    "memory_recent_messages",
    metadata,
    Column("id", BIGINT(unsigned=True), primary_key=True, autoincrement=True),
    Column("session_scope", VARCHAR(255), nullable=False),
    Column("role", VARCHAR(32), nullable=False),
    Column("content", LONGTEXT(), nullable=False),
    Column("name", VARCHAR(128), nullable=True),
    Column("created_at", DATETIME(3), nullable=False),
)


knowledge_files_table = Table(
    "knowledge_files",
    metadata,
    Column("id", BIGINT(unsigned=True), primary_key=True, autoincrement=True),
    Column("file_id", VARCHAR(64), nullable=False, unique=True),
    Column("task_id", VARCHAR(64), nullable=False, unique=True),
    Column("filename", VARCHAR(512), nullable=False),
    Column("file_path", VARCHAR(1024), nullable=False),
    Column("size", BIGINT(unsigned=True), nullable=False, server_default=text("0")),
    Column("status", VARCHAR(32), nullable=False, server_default=text("'pending'")),
    Column("error_message", LONGTEXT(), nullable=True),
    Column("created_at", DATETIME(3), nullable=False),
    Column("updated_at", DATETIME(3), nullable=False),
)


Index(
    "idx_memory_id_created_at",
    memory_history_table.c.memory_id,
    memory_history_table.c.created_at,
)
Index(
    "idx_recent_scope_created_at",
    memory_recent_messages_table.c.session_scope,
    memory_recent_messages_table.c.created_at,
)
Index(
    "idx_knowledge_files_status_created_at",
    knowledge_files_table.c.status,
    knowledge_files_table.c.created_at,
)


_engine = None


def get_mysql_engine():
    """懒加载全局 MySQL Engine。"""
    global _engine
    if _engine is None:
        _engine = create_engine(
            MYSQL_URL,
            pool_pre_ping=True,
            pool_recycle=3600,
        )
    return _engine


def init_db() -> None:
    """创建 Session Memory 所需表结构。

    使用 checkfirst=True，因此可以重复调用。
    注意：它会真的建立连接，失败时会抛异常——不要在请求路径或对象构造里调用它。
    启动阶段请用 safe_init_db()。
    """
    engine = get_mysql_engine()
    metadata.create_all(engine, checkfirst=True)
    logger.info("Session Memory MySQL schema is ready")


def safe_init_db() -> bool:
    """启动时初始化表结构（best-effort）。

    为什么单独拆一个：init_db() 要连库，而短期记忆只是增强能力。
    MySQL 暂时不可用时不能让服务起不来，更不能让问答不可用——
    失败只告警，返回 False，短期记忆在本轮进程内降级。

    :return: True = 表结构就绪；False = 连不上/建表失败，已降级
    """
    try:
        init_db()
        return True
    except Exception as exc:
        logger.warning(
            f"Session Memory MySQL 初始化失败，短期记忆降级（问答不受影响，"
            f"Redis 命中时仍可读历史）：{exc}"
        )
        return False
