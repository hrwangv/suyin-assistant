"""Session Memory 数据模型。"""
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class SessionMessage(BaseModel):
    """一条会话消息。

    对应 MySQL conversation_message 表，同时也会被序列化为 Redis
    List 中的 JSON 元素。
    """

    message_id: Optional[int] = None
    conversation_id: Optional[int] = None
    tenant_id: Optional[int] = None
    user_id: Optional[int] = None
    role: str
    content: str
    message_type: str = "chat"
    name: Optional[str] = None
    ts: Optional[datetime] = None


class Memory(BaseModel):
    """L2 Durable Memory。

    这是长期记忆的权威数据结构，最终持久化到 Qdrant Payload。
    """

    id: str
    data: str
    hash: str = ""
    text_lemmatized: str = ""

    created_at: datetime
    updated_at: datetime

    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    run_id: Optional[str] = None

    role: Optional[str] = None
    actor_id: Optional[str] = None
    attributed_to: Optional[str] = None

    metadata: Dict[str, Any] = {}

    importance: float = 0.5
    strength_score: float = 0.5

    last_accessed_at: Optional[datetime] = None
    access_count: int = 0

    expiration_date: Optional[datetime] = None

    lifecycle_state: str = "ACTIVE"
    scope: str = ""


class EntityRecord(BaseModel):
    """Entity Store 中的实体。"""

    id: str
    data: str
    linked_memory_ids: List[str] = []
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    run_id: Optional[str] = None
    entity_key: str = ""


class HistoryRecord(BaseModel):
    """History 审计记录。"""

    id: Optional[int] = None
    memory_id: str
    scope: str
    event: str
    old_memory: Optional[str] = None
    new_memory: Optional[str] = None
    actor_id: Optional[str] = None
    role: Optional[str] = None
    is_deleted: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class TaskState(BaseModel):
    """当前会话任务状态。"""

    goal: Optional[str] = None
    step: Optional[int] = None
    summary: Optional[str] = None
    updated_at: Optional[datetime] = None


class RecentMessage(BaseModel):
    """最近消息的持久化 fallback。"""

    id: Optional[int] = None
    session_scope: str
    role: str
    content: str
    name: Optional[str] = None
    created_at: Optional[datetime] = None
