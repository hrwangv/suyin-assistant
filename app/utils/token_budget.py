"""按 token 预算裁剪「要送进模型」的文本条目。

为什么要有这个工具：**任何进模型的内容都可能超限，不只是最终回答那一次**。
而且不同调用点的上限不一样——对话模型的窗口很大，embedding / rerank 这类
模型本身有硬上限，超了往往是静默截断（只编码前一部分，日志里看不出来）。

所以这里只提供两个最小工具，由调用方声明「预算多少」和「先丢谁」：

    items, dropped = fit_token_budget(items, budget=4000, drop="oldest")

统一放在这里的好处是 token 计数只有一份实现（复用 ContextWindow），
中文/英文的换算口径一致，不会出现某个模块用字符、某个模块用 token 的混乱。
"""
from __future__ import annotations

from typing import Callable, Sequence

from app.memory.context_window import ContextWindow

_window = ContextWindow()


def count_tokens(text) -> int:
    """估算文本的 token 数（tiktoken；离线时退化为中英文字符估算）。"""
    return _window.count_tokens(text or "")


def _default_text_of(item) -> str:
    """默认的取文本方式：dict 取 content/text 字段，字符串直接用。"""
    if isinstance(item, dict):
        return item.get("content") or item.get("text") or ""
    return item if isinstance(item, str) else ""


def fit_token_budget(
    items: Sequence,
    budget: int,
    text_of: Callable | None = None,
    drop: str = "oldest",
    keep_one: bool = False,
) -> tuple[list, int]:
    """把一批条目裁到 token 预算以内，整条整条地丢。

    :param items:    待裁剪的条目（消息 / 文档 / 记忆，类型不限）
    :param budget:   预算（token），<=0 表示只保留一条（若 keep_one）
    :param text_of:  如何取一条的文本，默认见 _default_text_of
    :param drop:     "oldest" 丢最前面的（对话历史用它）；
                     "last" 丢最后面的（按分数/相关度降序的列表用它）
    :param keep_one: 至少保留一条（即使它自己就超预算）——
                     用于"最新一条永远不能丢"的场景
    :return: (保留的条目列表, 被丢弃的条数)
    """
    # 从单个 item 中提取文本
    text_of = text_of or _default_text_of
    items = list(items or [])
    if not items:
        return [], 0

    if budget <= 0:
        return (items[-1:], len(items) - 1) if keep_one else ([], len(items))

    # 每条的 token 只算一次，之后丢弃时做减法即可，不整体重算
    tokens = [count_tokens(text_of(item)) for item in items]
    total = sum(tokens)

    # 去掉最新的，最前的元素
    if drop == "last":
        end = len(items)
        dropped = 0
        while end > (1 if keep_one else 0) and total > budget:
            end -= 1
            total -= tokens[end]
            dropped += 1
        return items[:end], dropped

    start = 0
    dropped = 0

    # 当 keep_one=True 时
    # start 最大为 len(items)-1，此时 items[start:] 只包含最后一个元素。

    # 当 keep_one=False 时
    # start 最大为 len(items)，此时 items[start:] 为空列表
    while start < (len(items) - 1 if keep_one else len(items)) and total > budget:
        total -= tokens[start] # 从总 token 中减去当前起始元素的 token
        start += 1 # 起始索引后移，相当于丢弃当前第一个元素
        dropped += 1 # 丢弃计数加一
    return items[start:], dropped
