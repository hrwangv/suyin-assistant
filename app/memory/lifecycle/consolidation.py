"""Consolidation 触发条件与分布式锁。"""
from datetime import datetime

from app.memory.config import CONSOLIDATION_LOCK_TTL
from app.memory.redis_client import RedisClient


class ConsolidationLock:
    def __init__(self, client: RedisClient = None):
        self.client = client or RedisClient()
        self.redis = self.client.redis

    def acquire(self, scope: str, ttl: int = CONSOLIDATION_LOCK_TTL) -> bool:
        key = f"mem:consolidation:{scope}:lock"
        # SET key 1 NX EX ttl
        return bool(self.redis.set(key, "1", nx=True, ex=ttl))

    def release(self, scope: str) -> None:
        self.redis.delete(f"mem:consolidation:{scope}:lock")


def should_consolidate(
    context_count: int,
    last_run_at: datetime | None,
    now: datetime | None = None,
    threshold: int = 20,
    interval_hours: int = 4,
) -> bool:
    if context_count >= threshold:
        return True
    if last_run_at is None:
        return False
    now = now or datetime.now()
    elapsed_hours = (now - last_run_at).total_seconds() / 3600
    return elapsed_hours >= interval_hours
