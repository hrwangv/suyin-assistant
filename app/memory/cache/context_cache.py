"""Redis Context：最近消息、任务、实体上下文、摘要。"""
import json
import uuid
from typing import List, Optional

from app.memory.config import (
    CONTEXT_CACHE_CAPACITY,
    EXTRACT_WATERMARK_TTL,
    REDIS_CONTEXT_TTL,
    REDIS_ENTITY_TTL,
    REDIS_TASK_TTL,
)
from app.memory.models import TaskState
from app.memory.redis_client import RedisClient


class ContextCache:
    def __init__(self, client: RedisClient = None):
        self.client = client or RedisClient()
        self.redis = self.client.redis

    def _msgs_key(self, scope: str) -> str:
        return f"mem:ctx:{scope}:msgs"

    def _task_key(self, scope: str) -> str:
        return f"mem:ctx:{scope}:task"

    def _entities_key(self, scope: str) -> str:
        return f"mem:ctx:{scope}:entities"

    def _summary_key(self, scope: str) -> str:
        return f"mem:ctx:{scope}:summary"

    def _watermark_key(self, scope: str) -> str:
        return f"mem:ctx:{scope}:extract_watermark"

    def set_extract_watermark(self, scope: str, message_id: int) -> None:
        """记录「长期记忆已抽取到哪条消息」（存消息 id，不是条数）。

        用 id 而不是条数：条数会随窗口 trim 封顶失真，id 是自增主键，能稳定定位。
        """
        self.redis.set(self._watermark_key(scope), int(message_id), ex=EXTRACT_WATERMARK_TTL)

    def get_extract_watermark(self, scope: str) -> int:
        """读取抽取水位；没有记录时返回 0（表示一条都还没抽过）。"""
        raw = self.redis.get(self._watermark_key(scope))
        try:
            return int(raw) if raw is not None else 0
        except (TypeError, ValueError):
            return 0

    def add_messages(self, scope: str, messages: List[dict]) -> None:
        """增量写入最近消息。

        适用于每轮新增 1~2 条消息，不删除旧缓存。
        """
        key = self._msgs_key(scope)
        pipe = self.redis.pipeline()
        # messages 是 oldest-first。按 oldest-first 逐个 LPUSH，
        # 最后写入 newest，最终 Redis List index 0 才是最新消息。
        for message in messages:
            pipe.lpush(key, json.dumps(message, ensure_ascii=False))
        pipe.ltrim(key, 0, CONTEXT_CACHE_CAPACITY - 1)
        pipe.expire(key, REDIS_CONTEXT_TTL)
        pipe.execute()

    def rebuild_messages(self, scope: str, messages: List[dict]) -> None:
        """用完整数据重建最近消息缓存。

        先写入临时 Key，全部构建完成后用 RENAME 原子替换旧 Key。
        这样在重建期间，读请求仍然可以命中旧缓存，不会读到空 Key。
        """
        key = self._msgs_key(scope)

        if not messages:
            # 
            self.redis.delete(key)
            return

        temp_key = f"{key}:rebuild:{uuid.uuid4().hex}"
        try:
            pipe = self.redis.pipeline()
            # messages 为 oldest-first，按此顺序 LPUSH，
            # 最终 Redis List index 0 是最新消息。
            for message in messages:
                pipe.lpush(
                    temp_key,
                    json.dumps(message, ensure_ascii=False),
                )
            pipe.ltrim(temp_key, 0, CONTEXT_CACHE_CAPACITY - 1)
            pipe.expire(temp_key, REDIS_CONTEXT_TTL)
            pipe.execute()

            # RENAME 是原子操作，读者要么看到旧缓存，要么看到新缓存。
            self.redis.rename(temp_key, key)
        except Exception:
            # 失败时清理临时 Key，旧 Key 仍然保留，避免出现半成品缓存。
            try:
                self.redis.delete(temp_key)
            except Exception:
                pass
            raise

    def get_messages(self, scope: str, limit: int = 10) -> List[dict]:
        if limit <= 0:
            return []
        key = self._msgs_key(scope)
        # 从redis里读
        items = self.redis.lrange(key, 0, limit - 1)
        messages = [json.loads(item) for item in items]
        messages.reverse()
        return messages

    def clear_messages(self, scope: str) -> None:
        self.redis.delete(self._msgs_key(scope))

    def set_task(self, scope: str, task: TaskState) -> None:
        payload = task.model_dump(mode="json")
        self.redis.set(
            self._task_key(scope),
            json.dumps(payload, ensure_ascii=False),
            ex=REDIS_TASK_TTL,
        )

    def get_task(self, scope: str) -> Optional[TaskState]:
        raw = self.redis.get(self._task_key(scope))
        if not raw:
            return None
        return TaskState.model_validate(json.loads(raw))

    def add_entities(self, scope: str, entities: List[str]) -> None:
        key = self._entities_key(scope)
        now = json.dumps({"last_seen_at": None})
        pipe = self.redis.pipeline()
        for entity in entities:
            pipe.hset(key, entity, now)
        pipe.expire(key, REDIS_ENTITY_TTL)
        pipe.execute()

    def get_entities(self, scope: str) -> List[str]:
        raw = self.redis.hgetall(self._entities_key(scope))
        return list(raw.keys())

    def set_summary(self, scope: str, summary: str) -> None:
        self.redis.set(self._summary_key(scope), summary, ex=REDIS_CONTEXT_TTL)

    def get_summary(self, scope: str) -> Optional[str]:
        return self.redis.get(self._summary_key(scope))
