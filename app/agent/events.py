"""Agent 的 SSE 事件协议（规范第 66 节）。

复用现有 SSE 队列（app/utils/sse_utils.py），只是新增了若干事件名，
前端可以逐步接入；不认识这些事件的旧前端会直接忽略，不会报错。
"""
from app.utils.sse_utils import push_to_session


class AgentEvent:
    AGENT_STARTED = "agent_started"
    ROUTING = "routing"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    DOCUMENT_PROCESSING = "document_processing"
    OCR_COMPLETED = "ocr_completed"
    COMPANY_CANDIDATES = "company_candidates"
    HUMAN_CONFIRMATION_REQUIRED = "human_confirmation_required"
    RESUME = "resume"
    TEMPLATE_SELECTED = "template_selected"
    DOCUMENT_GENERATING = "document_generating"
    DOCUMENT_READY = "document_ready"
    AGENT_MESSAGE = "agent_message"


def emit(session_id: str, event: str, data: dict) -> None:
    """往当前 thread 的 SSE 队列推事件（没有队列时是空操作）。"""
    if not session_id:
        return
    try:
        push_to_session(session_id, event, data)
    except Exception:
        # SSE 是展示层，任何推送失败都不能影响主流程
        pass
