"""Redis 客户端封装。"""
import redis

from app.conf.memory_config import REDIS_URL


class RedisClient:
    def __init__(self):
        self.url = REDIS_URL
        self.redis = redis.Redis.from_url(
            self.url,
            decode_responses=True,
        )

    def close(self):
        self.redis.close()
