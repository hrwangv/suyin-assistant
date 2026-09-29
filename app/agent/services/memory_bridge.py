"""Agent 与记忆系统的桥接。

不动现有记忆实现，直接把查询链路里已经调通的
`step_5_write_history` / `step_6_extract_long_term_memory` 拿来复用，
保证 Agent 会话和 /query 会话在多轮上下文、长期记忆上完全一致。
"""
from app.core.logger import logger
from app.memory.recent_message_service import get_recent_message_service
from app.memory.utils.scope import build_scope
from app.rag.query_process.agent.nodes.node_answer_output import (
    step_5_write_history,
    step_6_extract_long_term_memory,
)


def recent_history(state: dict, limit: int = 6) -> list[dict]:
    """读最近几轮对话（给 Supervisor / 回答节点提供上下文，失败降级为空）。"""
    session_id = state.get("session_id") or state.get("thread_id")
    if not session_id:
        return []
    try:
        return get_recent_message_service().get_messages(build_scope(run_id=session_id), limit)
    except Exception as exc:
        logger.warning(f"[memory_bridge] 读取短期记忆失败，本轮按无历史继续：{exc}")
        return []


def format_history(messages: list[dict], max_chars: int = 600) -> str:
    """把最近消息压成一段短文本（Supervisor 只需要看懂上下文）。"""
    lines = []
    for message in messages or []:
        role = "用户" if message.get("role") == "user" else "助手"
        content = str(message.get("content") or "").replace("\n", " ")
        lines.append(f"{role}：{content[:max_chars]}")
    return "\n".join(lines) if lines else "（无）"


def record_user_message(state: dict) -> None:
    """写用户消息（同一轮只写一次）。

    RAG 检索节点内部已经会写用户消息，所以知识问答分支不再重复写；
    文档/申请书分支由这里补上。
    """
    if state.get("user_message_recorded"):
        return
    session_id = state.get("session_id") or state.get("thread_id")
    text = state.get("request_text") or state.get("original_query")
    if not session_id or not text:
        return
    try:
        get_recent_message_service().add_messages(
            build_scope(run_id=session_id), [{"role": "user", "content": text}]
        )
    except Exception as exc:
        logger.error(f"[memory_bridge] 写入用户消息失败（忽略）：{exc}")


def record_answer(state: dict) -> None:
    """写助手回答 + 触发长期记忆抽取（复用现有实现，失败只降级）。"""
    record_user_message(state)
    try:
        step_5_write_history(state)
    except Exception as exc:
        logger.error(f"[memory_bridge] 写入助手回答失败（忽略）：{exc}")
    try:
        step_6_extract_long_term_memory(state)
    except Exception as exc:
        logger.warning(f"[memory_bridge] 触发长期记忆抽取失败（忽略）：{exc}")
