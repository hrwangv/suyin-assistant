"""Token Window 上下文裁剪。

Session Memory 不做语义检索，只按时间顺序返回最近消息；
在进入 LLM 之前，这里负责根据 Token 预算从新到旧截断。
"""
import re
from datetime import datetime
from typing import List, Optional

import tiktoken

from app.conf.memory_config import TOKENIZER_ENCODING
from app.memory.models import SessionMessage


class ContextWindow:
    """把最近消息压缩进一个 Token 窗口。"""

    def __init__(self, encoding_name: str = TOKENIZER_ENCODING):
        # tiktoken 在离线环境下可能无法下载编码文件，因此失败时退化为
        # 一个简单的中英文混合估算器，保证 Token Window 始终可用。
        try:
            self.encoding = tiktoken.get_encoding(encoding_name)
        except Exception:
            self.encoding = None

    def _estimate_tokens(self, text: str) -> int:
        if not text:
            return 0

        cjk_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
        words = len(re.findall(r"[A-Za-z0-9]+", text))
        remaining = len(
            re.sub(r"[A-Za-z0-9\u4e00-\u9fff]", "", text)
        )
        return cjk_chars + words + remaining

    def count_tokens(self, text: str) -> int:
        if self.encoding is not None:
            return len(self.encoding.encode(text or ""))
        return self._estimate_tokens(text or "")

    def _message_tokens(self, message: SessionMessage) -> int:
        # 角色名本身也有少量 Token 开销，给一个固定余量。
        return 4 + self.count_tokens(message.content)

    def _normalize_roles(
        self,
        messages: List[SessionMessage],
    ) -> List[SessionMessage]:
        """对 user/assistant 这类对话做最小角色顺序校验。"""
        if not messages:
            return messages

        roles = {m.role for m in messages}
        if roles <= {"user", "assistant"}:
            # 避免窗口从 assistant 开始，造成上下文缺少问题。
            first_user_index = next(
                (
                    i
                    for i, m in enumerate(messages)
                    if m.role == "user"
                ),
                None,
            )
            if first_user_index is not None:
                messages = messages[first_user_index:]
            else:
                return []

        return messages

    def build(
        self,
        messages: List[SessionMessage],
        max_tokens: Optional[int] = None,
    ) -> List[dict]:
        """返回最终可供 LLM 使用的消息列表。

        messages 应为 oldest-first，输出也保持 oldest-first。
        """
        from app.conf.memory_config import MAX_TOKEN_WINDOW_SIZE

        max_tokens = max_tokens or MAX_TOKEN_WINDOW_SIZE

        valid_messages = [
            message
            for message in messages
            if message.role and message.content
        ]

        selected: List[SessionMessage] = []
        used_tokens = 0

        # 从最新消息向前遍历，优先保留最近内容。
        for message in reversed(valid_messages):
            tokens = self._message_tokens(message)
            if selected and used_tokens + tokens > max_tokens:
                break
            selected.append(message)
            used_tokens += tokens

        selected.reverse()
        selected = self._normalize_roles(selected)

        return [
            {
                "message_id": message.message_id,
                "conversation_id": message.conversation_id,
                "role": message.role,
                "content": message.content,
                "name": message.name,
                "ts": (
                    message.ts.isoformat()
                    if isinstance(message.ts, datetime)
                    else message.ts
                ),
            }
            for message in selected
        ]
