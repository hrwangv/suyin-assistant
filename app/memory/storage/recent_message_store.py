"""最近消息的持久化 fallback。

Redis 是 L1 快速路径；MySQL 是 Source of Truth，用于 Redis 不可用时兜底。

构造这个 Store 不会连库：表结构由启动时初始化（app.db.mysql.safe_init_db）。
原因见 __init__ 里的注释——把建表放在构造函数里会让「MySQL 不可用」直接
升级成「短期记忆服务连构造都构造不出来」，而 Redis 里其实还有可用的历史。
"""
from datetime import datetime
from typing import List

from sqlalchemy import delete, func, insert, select

from app.db.mysql import (
    get_mysql_engine,
    memory_recent_messages_table,
)
from app.memory.config import CONTEXT_MYSQL_KEEP
from app.memory.models import RecentMessage


class RecentMessageStore:
    def __init__(self, keep: int | None = None):
        # 每个 scope 在 MySQL 里保留的条数，默认取配置（与 Redis 缓存容量对齐）。
        # 这里曾经写死 10，比 Redis 容量小得多：Redis 过期回源时只能捞回 10 条，
        # 剩下的消息实际上永久丢了（写入时就被 trim 掉了）。
        self.keep = keep or CONTEXT_MYSQL_KEEP
        # 这里**不调用 init_db()**（建表要连库）：
        # 一旦 MySQL 不可用，构造函数就会抛，读路径连「Redis 优先」都走不到，
        # 整个问答会在第一个节点直接失败。表结构改为启动时初始化，
        # 运行期连不上库时由 callers 做降级（见 RecentMessageService）。
        # create_engine 本身是惰性的，不会建立连接。
        self.engine = get_mysql_engine()

    def add(self, message: RecentMessage) -> None:
        message.created_at = datetime.now()
        with self.engine.begin() as conn:
            conn.execute(
                insert(memory_recent_messages_table).values(
                    session_scope=message.session_scope,
                    role=message.role,
                    content=message.content,
                    name=message.name,
                    created_at=message.created_at,
                )
            )
            self._trim(conn, message.session_scope)

    def _trim(self, conn, session_scope: str) -> None:
        rows = conn.execute(
            select(memory_recent_messages_table.c.id)
            .where(
                memory_recent_messages_table.c.session_scope == session_scope
            )
            .order_by(memory_recent_messages_table.c.id.desc())
            .offset(self.keep)
        ).fetchall()
        ids_to_delete = [row[0] for row in rows]
        if ids_to_delete:
            conn.execute(
                delete(memory_recent_messages_table).where(
                    memory_recent_messages_table.c.id.in_(ids_to_delete)
                )
            )

    @staticmethod
    def _to_message(row) -> RecentMessage:
        return RecentMessage(
            id=row.id,
            session_scope=row.session_scope,
            role=row.role,
            content=row.content,
            name=row.name,
            created_at=row.created_at,
        )

    def get_recent(self, session_scope: str, limit: int = 10) -> List[RecentMessage]:
        stmt = (
            select(memory_recent_messages_table)
            .where(memory_recent_messages_table.c.session_scope == session_scope)
            .order_by(memory_recent_messages_table.c.id.desc())
            .limit(limit)
        )
        with self.engine.connect() as conn:
            rows = conn.execute(stmt).fetchall()
        rows = list(reversed(rows))
        return [self._to_message(row) for row in rows]

    def count_since(self, session_scope: str, after_id: int) -> int:
        """统计某个会话里 id 大于 after_id 的消息条数（即「距上次抽取新增了多少条」）。"""
        stmt = select(func.count()).select_from(memory_recent_messages_table).where(
            memory_recent_messages_table.c.session_scope == session_scope,
            memory_recent_messages_table.c.id > int(after_id or 0),
        )
        with self.engine.connect() as conn:
            return int(conn.execute(stmt).scalar() or 0)

    def get_since(
        self,
        session_scope: str,
        after_id: int,
        limit: int = 60,
    ) -> List[RecentMessage]:
        """按「id 大于 after_id」取一批消息，从旧到新返回（长期记忆抽取的输入）。"""
        stmt = (
            select(memory_recent_messages_table)
            .where(
                memory_recent_messages_table.c.session_scope == session_scope,
                memory_recent_messages_table.c.id > int(after_id or 0),
            )
            .order_by(memory_recent_messages_table.c.id.asc())
            .limit(limit)
        )
        with self.engine.connect() as conn:
            rows = conn.execute(stmt).fetchall()
        return [self._to_message(row) for row in rows]

    def get_before(
        self,
        session_scope: str,
        before_id: int,
        limit: int = 10,
    ) -> List[RecentMessage]:
        """取 before_id 之前的最近 limit 条消息（从旧到新）。

        用途：给长期记忆抽取的批次补「前导上下文」——批次是 40 条时它自带语境，
        这个前导只是让批次与上一批的边界衔接更自然。
        必须从**真实会话的 scope**（run:{session_id}）读，而不是长期记忆的 scope，
        否则会把别的对话的内容混进来。
        """
        stmt = (
            select(memory_recent_messages_table)
            .where(
                memory_recent_messages_table.c.session_scope == session_scope,
                memory_recent_messages_table.c.id < int(before_id),
            )
            .order_by(memory_recent_messages_table.c.id.desc())
            .limit(limit)
        )
        with self.engine.connect() as conn:
            rows = conn.execute(stmt).fetchall()
        return [self._to_message(row) for row in reversed(rows)]

    def clear_scope(self, session_scope: str) -> int:
        with self.engine.begin() as conn:
            result = conn.execute(
                delete(memory_recent_messages_table).where(
                    memory_recent_messages_table.c.session_scope
                    == session_scope
                )
            )
        return int(result.rowcount)
