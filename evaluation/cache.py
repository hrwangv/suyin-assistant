"""磁盘缓存：把检索结果与 LLM 调用结果缓存到 evaluation/results/_cache/。

为什么必须有：一次完整评测 = 100 题 × 6 个层级 × (改写 + HyDE + 答案 + 判分)，
轻松上千次外部调用。缓存之后，改报告格式、调指标口径都能秒级重跑，
失败中断也能接着跑，不会重复烧钱。

注意：缓存 key 必须包含**影响结果的输入**（模型、prompt、参数、collection）。
改了 prompt 或换了模型，key 自然变化，不会读到脏缓存。
需要强制重跑时用 run_eval 的 --refresh。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from evaluation.config import CACHE_DIR, CACHE_ENABLED


def make_key(payload: Any) -> str:
    """把任意可 JSON 化的输入压成一个稳定的短 key。"""
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]


def _path(namespace: str, key: str) -> Path:
    return CACHE_DIR / namespace / f"{key}.json"


def load(namespace: str, key: str) -> Any | None:
    path = _path(namespace, key)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        # 缓存损坏不能让评测崩掉，当作未命中重算
        return None


def store(namespace: str, key: str, value: Any) -> None:
    path = _path(namespace, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def cached(
    namespace: str,
    payload: Any,
    fn: Callable[[], Any],
    refresh: bool = False,
    enabled: bool | None = None,
) -> Any:
    """命中缓存直接返回，否则执行 fn 并落盘。"""
    enabled = CACHE_ENABLED if enabled is None else enabled
    if not enabled:
        return fn()
    key = make_key(payload)
    if not refresh:
        hit = load(namespace, key)
        if hit is not None:
            return hit
    value = fn()
    store(namespace, key, value)
    return value
