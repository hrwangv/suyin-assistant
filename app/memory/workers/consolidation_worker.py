"""Consolidation Worker 函数入口。"""
from app.memory.api import get_memory_coordinator


def run_consolidation(scope: str) -> int:
    return get_memory_coordinator().consolidate(scope)
