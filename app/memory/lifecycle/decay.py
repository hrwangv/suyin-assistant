"""Strength Score 与生命周期状态。"""
import math
from datetime import datetime

from app.memory.config import DECAY_ALPHA, WEAK_STRENGTH_THRESHOLD, ARCHIVE_STRENGTH_THRESHOLD
from app.memory.utils.time import elapsed_days


def half_life_days(importance: float) -> float:
    if importance >= 0.9:
        return 180.0
    if importance >= 0.7:
        return 90.0
    if importance >= 0.5:
        return 30.0
    return 7.0


def compute_strength(
    importance: float,
    last_accessed_at: datetime | None,
    access_count: int,
    now: datetime | None = None,
) -> float:
    """strength = importance * time_decay * access_boost。"""
    now = now or datetime.now()
    days = elapsed_days(now, last_accessed_at)
    half_life = half_life_days(importance)
    lambda_value = math.log(2) / half_life
    time_decay = math.exp(-lambda_value * days)

    access_boost = 1 + DECAY_ALPHA * math.log(1 + min(access_count, 100))
    access_boost = min(access_boost, 2.0)

    strength = importance * time_decay * access_boost
    return max(0.0, min(1.0, strength))


def determine_lifecycle(strength: float, expired: bool = False) -> str:
    if expired or strength < ARCHIVE_STRENGTH_THRESHOLD:
        return "ARCHIVE"
    if strength < WEAK_STRENGTH_THRESHOLD:
        return "WEAK"
    return "ACTIVE"
