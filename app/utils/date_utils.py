"""日期格式转换工具。

知识库里的日期是从文件名里提取出来的（如"经营晨报20260410" → "20260410"），
属于紧凑字符串，没法直接做 Qdrant 的 gte/lte 范围比较。

这里把「提取原始值」和「存储格式」两件事分开：
上游只负责提取，转换统一走这个函数，保证 payload 里只存一种日期格式。
"""
from __future__ import annotations

import re

_COMPACT_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")
_ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
_SEP_RE = re.compile(r"^(\d{4})[/.](\d{1,2})[/.](\d{1,2})$")


def to_rfc3339_date(value) -> str | None:
    """把各种写法的日期统一成 RFC3339 的当天零点（UTC）。

    支持：20260410 / 2026-04-10 / 2026/4/10 / 2026.4.10
    已经是 RFC3339 的（2026-04-10T00:00:00Z）原样返回，所以这个函数可以重复调用。

    识别不了时返回 None：调用方应保持空值，不要凭空补一个日期出来。
    """
    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text:
        return None

    matched = _COMPACT_RE.match(text) or _SEP_RE.match(text) or _ISO_DATE_RE.match(text)
    if not matched:
        return None

    year, month, day = matched.group(1), int(matched.group(2)), int(matched.group(3))
    # 简单范围校验，避免 "20269999" 这类恰好能匹配正则但不是日期的值混进来
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return f"{year}-{month:02d}-{day:02d}T00:00:00Z"
