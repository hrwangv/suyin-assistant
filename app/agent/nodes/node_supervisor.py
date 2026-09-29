"""Main Agent 的 Supervisor 节点。"""
import sys

from app.agent.events import AgentEvent, emit
from app.agent.supervisor import decide
from app.core.tracing import observe, update_current_span
from app.utils.task_utils import add_done_task, add_running_task


@observe(name="node:supervisor", as_type="span", capture_input=False, capture_output=False)
def node_supervisor(state: dict) -> dict:
    """做一次决策并写入 State（真正的路由由 routing.route_after_supervisor 决定）。"""
    node = sys._getframe().f_code.co_name
    session_id = state["session_id"]
    add_running_task(session_id, node, state.get("is_stream", False))

    turns = int(state.get("supervisor_turns") or 0) + 1
    # 入参/出参显式上报：整份 state 太大，只留「谁在问 + 这是第几次决策」这类关键信息
    update_current_span(
        input={
            "question": state.get("request_text") or state.get("original_query") or "",
            "turns": turns,
            "attachments": len(state.get("attachments") or []),
        }
    )
    # 第一轮 
    if turns == 1:
        emit(session_id, AgentEvent.AGENT_STARTED, {"thread_id": session_id})

    # 主agent进行意图判断
    decision = decide({**state, "supervisor_turns": turns})
    emit(
        session_id,
        AgentEvent.ROUTING,
        {
            "next_action": decision.next_action,
            "reason": decision.reason,
            "confidence": decision.confidence,
        },
    )

    updates: dict = {
        "intent": decision.next_action,
        "route": decision.next_action,
        "decision_reason": decision.reason,
        "supervisor_turns": turns,
        "supervisor_decisions": list(state.get("supervisor_decisions") or [])
        + [decision.model_dump()],
    }
    # 本轮要在文档上完成什么（用户目标陈述），交给 Document Agent 自己映射成动作。
    # 只在"要让文档 Agent 干活"的分支写入；其它分支写了会污染观察与后续决策。
    # 注意这里写的是 task 文本，不是动作名——动作由子 Agent 判定（resolved_action）。
    if decision.next_action == "document" and decision.task.strip():
        updates["document_task_text"] = decision.task.strip()
    # 请求侧事实（文件 id / 用户明确给出的值）：原样留存，供子 Agent 与排障读取。
    if decision.context:
        updates["request_context"] = dict(decision.context)
    if decision.missing_info:
        updates["missing_info"] = decision.missing_info

    update_current_span(
        output={
            "next_action": decision.next_action,
            "reason": decision.reason,
            "confidence": decision.confidence,
            "task": decision.task,
        }
    )
    add_done_task(session_id, node, state.get("is_stream", False))
    return updates
