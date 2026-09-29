"""反问节点：信息不足或需要用户确认时结束本轮，等用户补充。"""
import sys

from app.agent.events import AgentEvent, emit
from app.agent.nodes.clarify import compose_clarification
from app.agent.services.memory_bridge import record_answer
from app.core.tracing import observe, update_current_span
from app.utils.task_utils import add_done_task, add_running_task, set_task_result


@observe(name="node:ask_user", as_type="span", capture_input=False, capture_output=False)
def node_ask_user(state: dict) -> dict:
    node = sys._getframe().f_code.co_name
    session_id = state["session_id"]
    is_stream = state.get("is_stream", False)
    add_running_task(session_id, node, is_stream)

    update_current_span(
        input={
            "question": state.get("request_text") or state.get("original_query") or "",
            "missing_info": state.get("missing_info") or [],
            "phase": state.get("application_phase") or "",
        }
    )
    answer = compose_clarification(state)
    update_current_span(output={"answer": answer, "awaiting_user_confirmation": True})
    if is_stream:
        emit(session_id, "delta", {"delta": answer})
        emit(session_id, "final", {"answer": answer, "status": "completed"})
    else:
        set_task_result(session_id, "answer", answer)

    emit(session_id, AgentEvent.AGENT_MESSAGE, {"content": answer})
    record_answer({**state, "answer": answer})
    add_done_task(session_id, node, is_stream)
    return {"answer": answer, "awaiting_user_confirmation": True}
