"""Scope 生成与校验。

所有 Memory 操作必须显式携带 Scope，禁止仅凭 memory_id 跨租户访问。
"""
from typing import Optional


def build_scope(
    user_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> str:
    parts = []
    if user_id:
        parts.append(f"user:{user_id}")
    if agent_id:
        parts.append(f"agent:{agent_id}")
    if run_id:
        parts.append(f"run:{run_id}")

    if not parts:
        raise ValueError("至少需要 user_id / agent_id / run_id 中的一个")

    return ":".join(parts)


def normalize_scope(scope: str) -> str:
    scope = (scope or "").strip()
    if not scope:
        raise ValueError("scope 不能为空")
    return scope


def build_long_term_scope(
    user_id: Optional[str] = None,
    default_user_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> str:
    """长期记忆的 scope：统一按「用户」聚合，实现跨对话共享。

    优先级：
    1. 调用方传来的 user_id（未来接入真实用户体系后由网关/前端带上）；
    2. 配置的默认用户 MEMORY_DEFAULT_USER_ID（当前没有用户体系时的过渡方案）；
    3. 都没有时退化为 run:{session_id} —— 相当于维持旧的会话级行为，
       只是不会报错。注意这种情况下长期记忆无法跨对话。

    长期记忆的读写必须用同一个 scope，所以统一走这个函数，
    避免一边按 user 写、另一边按 session 读。
    """
    identity = user_id or default_user_id
    if identity:
        return build_scope(user_id=identity)
    return build_scope(run_id=run_id)


def parse_scope(scope: str) -> dict:
    """把 build_scope 生成的 scope 还原为 user/agent/run。"""
    result = {}
    parts = normalize_scope(scope).split(":")
    index = 0
    while index < len(parts):
        key = parts[index]
        if key in {"user", "agent", "run"} and index + 1 < len(parts):
            result[f"{key}_id"] = parts[index + 1]
            index += 2
        else:
            index += 1
    return result
