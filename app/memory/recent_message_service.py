"""统一最近消息服务。

短期/工作记忆统一为一份：
- MySQL memory_recent_messages 是 Source of Truth
- Redis Context Cache 是最近消息热缓存
- 统一维度为 scope，不再维护 conversation_id 那一套
"""
from typing import List, Optional

from app.conf.memory_config import CONTEXT_READ_LIMIT
from app.core.logger import logger
from app.memory.cache.context_cache import ContextCache
from app.memory.config import CONTEXT_CACHE_CAPACITY
from app.memory.models import RecentMessage
from app.memory.storage.recent_message_store import RecentMessageStore


class RecentMessageService:
    def __init__(
        self,
        cache: ContextCache = None,
        store: RecentMessageStore = None,
    ):
        # 缓存redis
        self.cache = cache or ContextCache()
        # 存数据库
        self.store = store or RecentMessageStore()

    def _to_dict(self, message: RecentMessage) -> dict:
        return {
            "role": message.role,
            "content": message.content,
            "name": message.name,
            "ts": (
                message.created_at.isoformat()
                if message.created_at
                else None
            ),
        }

    def add_messages(self, scope: str, messages: List[dict]) -> None:
        """MySQL 先写，Redis 后写。写失败只降级，不上抛。

        短期记忆是「增强能力」，而调用点都在问答主流程里（用户消息 / 助手回答）。
        一旦把写失败抛出去：用户消息那次会让整个问答失败，助手回答那次更糟——
        答案已经推给前端了，却把会话标记成 failed。所以这里的取舍是
        **本轮短期记忆丢失，换取问答不受影响**。

        失败时必须清 Redis 缓存：缓存里现在缺这几条消息，不清的话下次读取
        直接命中旧缓存，读到的是「没有本轮对话」的历史（多轮追问会答非所问），
        而且这个缺口会一直留着直到缓存过期。
        """
        try:
            for message in messages:
                # 写数据库
                self.store.add(
                    RecentMessage(
                        session_scope=scope,
                        role=message.get("role", "user"),
                        content=message.get("content", ""),
                        name=message.get("name"),
                    )
                )
        except Exception as exc:
            logger.error(
                f"短期记忆写入 MySQL 失败，本轮降级（问答不受影响）："
                f"scope={scope}, roles={[m.get('role') for m in messages]}, err={exc}"
            )
            self._clear_cache_quietly(scope)
            return

        try:
            # 存缓存redis
            self.cache.add_messages(scope, messages)
        except Exception as exc:
            # Redis 写失败时把缓存整个清掉，让下一次读取必然回源 MySQL 重建。
            # 否则缓存会长期停留在"缺了几条新消息"的状态：读取侧看到缓存非空就不再回源，
            # 那几条消息会一直缺席（MySQL 里数据是完整的，重建无损）。
            logger.warning(f"Redis Context 写入失败，清空缓存待下次重建：{exc}")
            self._clear_cache_quietly(scope)

    def _clear_cache_quietly(self, scope: str) -> None:
        """清缓存（失败忽略）：缓存是可重建的，清不动也不该影响主流程。"""
        try:
            self.cache.clear_messages(scope)
        except Exception as clear_exc:
            logger.warning(f"Redis Context 清理失败，忽略：{clear_exc}")

    def get_messages(
        self,
        scope: str,
        limit: int = CONTEXT_READ_LIMIT,
    ) -> List[dict]:
        """读取最近消息。

        :param limit: 本轮最多读取多少条（读取上限），与缓存里存了多少条无关；
                      缓存容量见 CONTEXT_CACHE_CAPACITY。
        """
        if limit <= 0:
            return []

        # Redis 优先：只要缓存里有数据就直接用。
        #
        # 这里原先的条件是 len(cached) >= limit，看着更"严格"，实际是个坑：
        # Redis 的容量上限是 CONTEXT_CACHE_CAPACITY，而 limit 取的是 CONTEXT_READ_LIMIT，
        # 两个值一旦不一致（例如 30 vs 50），条件永远不成立 ——
        # 每次读取都会绕过缓存回源 MySQL，拿回更少的消息，再用它覆盖缓存，
        # 结果短期记忆实际退化成 MySQL 的 keep 条数。
        # 另外短对话同样永远不命中：新会话只有几条消息时也会白跑一次 MySQL。
        try:
            cached = self.cache.get_messages(scope, limit)
            if cached:
                return cached
        except Exception as exc:
            logger.warning(f"Redis Context 读取失败，回源 MySQL：{exc}")

        # MySQL 是 Source of Truth，回源后重建 Redis 缓存。
        try:
            messages = self.store.get_recent(scope, CONTEXT_CACHE_CAPACITY)
        except Exception as exc:
            # 回源失败就降级成「没有历史」：宁可这一轮少一点上下文，
            # 也不要让整个问答因为短期记忆不可用而失败。
            logger.warning(
                f"短期记忆回源 MySQL 失败，本轮降级为空历史（问答继续）：{exc}"
            )
            return []
        message_dicts = [self._to_dict(message) for message in messages]
        try:
            # 这里是完整重建，不能用 add_messages 追加，否则会重复。
            self.cache.rebuild_messages(scope, message_dicts)
        except Exception as exc:
            logger.warning(f"Redis Context 重建失败，忽略：{exc}")

        return message_dicts[-limit:]

    def clear(self, scope: str) -> int:
        try:
            self.cache.clear_messages(scope)
        except Exception as exc:
            logger.warning(f"Redis Context 清空失败，忽略：{exc}")
        return self.store.clear_scope(scope)


_recent_message_service: Optional[RecentMessageService] = None


class _DegradedRecentMessageService:
    """短期记忆整体不可用时的兜底实现（只在构造期就失败时使用）。

    显式降级而不是静默：读返回空历史、清空返回 0，每次被丢弃的写入都打 error 日志，
    这样既不会把异常抛进问答主流程，日志里也能看出「本轮对话没记住」。
    构造期失败通常意味着配置错误（URL 写错之类），修好配置重启即可恢复，
    所以这里缓存降级实例，避免每个请求都重试一次注定失败的构造并刷日志。
    """

    def get_messages(self, scope: str, limit: int = CONTEXT_READ_LIMIT) -> List[dict]:
        return []

    def add_messages(self, scope: str, messages: List[dict]) -> None:
        logger.error(
            f"短期记忆服务不可用，放弃写入（问答不受影响）："
            f"scope={scope}, roles={[m.get('role') for m in messages]}"
        )

    def clear(self, scope: str) -> int:
        logger.error(f"短期记忆服务不可用，清空请求未执行：scope={scope}")
        return 0


_degraded_service: Optional[_DegradedRecentMessageService] = None


def get_recent_message_service() -> RecentMessageService:
    """获取短期记忆服务；构造失败时降级，绝不把异常抛进问答主流程。"""
    global _recent_message_service, _degraded_service
    if _recent_message_service is None:
        try:
            _recent_message_service = RecentMessageService()
        except Exception as exc:
            logger.error(
                f"短期记忆服务初始化失败，本进程内降级为不可用（问答继续）：{exc}"
            )
            if _degraded_service is None:
                _degraded_service = _DegradedRecentMessageService()
            return _degraded_service
    return _recent_message_service
