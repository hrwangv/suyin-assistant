"""Lifecycle Worker 函数入口。"""
from app.memory.api import get_memory_coordinator


def run_lifecycle(scope: str) -> dict:
    return get_memory_coordinator().run_lifecycle(scope)
