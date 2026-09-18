"""知识库文件元数据持久化。

文件信息以 MySQL 为 Source of Truth，文件列表和删除操作都基于该表。
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, insert, select, update

from app.db.mysql import (
    get_mysql_engine,
    init_db,
    knowledge_files_table,
)


def _now() -> datetime:
    return datetime.now()


def insert_knowledge_file(
    file_id: str,
    task_id: str,
    filename: str,
    file_path: str,
    size: int,
    status: str = "pending",
) -> None:
    init_db()
    now = _now()
    with get_mysql_engine().begin() as conn:
        conn.execute(
            insert(knowledge_files_table).values(
                file_id=file_id,
                task_id=task_id,
                filename=filename,
                file_path=file_path,
                size=size,
                status=status,
                created_at=now,
                updated_at=now,
            )
        )


def list_knowledge_files() -> List[Dict[str, Any]]:
    init_db()
    with get_mysql_engine().connect() as conn:
        rows = conn.execute(
            # 查知识库表，并按照创建时间倒序排列
            select(knowledge_files_table).order_by(
                knowledge_files_table.c.created_at.desc()
            )
        ).fetchall()

    # 返回一个字典，键为列名，值为对应的值
    return [dict(row._mapping) for row in rows]


def get_knowledge_file(file_id: str) -> Optional[Dict[str, Any]]:
    init_db()
    with get_mysql_engine().connect() as conn:
        row = conn.execute(
            select(knowledge_files_table).where(
                knowledge_files_table.c.file_id == file_id
            )
        ).first()
    return dict(row._mapping) if row else None


def update_knowledge_file_status(
    file_id: str,
    status: str,
    error_message: Optional[str] = None,
) -> None:
    init_db()
    values = {
        "status": status,
        "updated_at": _now(),
    }
    if error_message is not None:
        values["error_message"] = error_message

    with get_mysql_engine().begin() as conn:
        conn.execute(
            update(knowledge_files_table)
            .where(knowledge_files_table.c.file_id == file_id)
            .values(**values)
        )


def delete_knowledge_file(file_id: str) -> bool:
    init_db()
    with get_mysql_engine().begin() as conn:
        result = conn.execute(
            delete(knowledge_files_table).where(
                knowledge_files_table.c.file_id == file_id
            )
        )
    return bool(result.rowcount)
