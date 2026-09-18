"""History 审计存储。

Vector Store 保存当前状态，History 保存状态变化。
"""
from datetime import datetime
from typing import List

from sqlalchemy import insert, select

from app.db.mysql import (
    get_mysql_engine,
    init_db,
    memory_history_table,
)
from app.memory.models import HistoryRecord


class HistoryStore:
    def __init__(self):
        self.engine = get_mysql_engine()
        init_db()

    def add(self, record: HistoryRecord) -> None:
        now = datetime.now()
        with self.engine.begin() as conn:
            conn.execute(
                insert(memory_history_table).values(
                    memory_id=record.memory_id,
                    scope=record.scope,
                    event=record.event,
                    old_memory=record.old_memory,
                    new_memory=record.new_memory,
                    actor_id=record.actor_id,
                    role=record.role,
                    is_deleted=record.is_deleted,
                    created_at=now,
                    updated_at=now,
                )
            )

    def history(self, memory_id: str, scope: str) -> List[HistoryRecord]:
        stmt = (
            select(memory_history_table)
            .where(
                memory_history_table.c.memory_id == memory_id,
                memory_history_table.c.scope == scope,
            )
            .order_by(memory_history_table.c.created_at.asc())
        )
        with self.engine.connect() as conn:
            rows = conn.execute(stmt).fetchall()

        return [
            HistoryRecord(
                id=row.id,
                memory_id=row.memory_id,
                scope=row.scope,
                event=row.event,
                old_memory=row.old_memory,
                new_memory=row.new_memory,
                actor_id=row.actor_id,
                role=row.role,
                is_deleted=int(row.is_deleted),
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in rows
        ]
