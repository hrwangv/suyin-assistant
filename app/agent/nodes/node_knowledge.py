"""Knowledge Skill 节点：把整条老 RAG 链路跑到底。

为什么不是"只取证据"：
    知识问答的答案必须由老链路的 node_answer_output 产出——它负责长期记忆注入、
    answer_out.prompt、图片回传、写短期记忆与触发长期记忆抽取。
    所以这里直接复用编译好的 query_app（services/rag_service.answer_with_legacy_chain），
    跑完把答案写回 State，然后图直接结束（knowledge → END），不再回 Main Agent 组织答案。
"""
import sys

from app.agent.events import AgentEvent, emit
from app.agent.services.rag_service import answer_with_legacy_chain
from app.core.logger import logger
from app.core.tracing import observe, update_current_span
from app.utils.task_utils import add_done_task, add_running_task


# 入参/出参显式上报（装饰器不自动抓参）：本节点整体接收 / 返回的是整份 AgentState，
# 里面带着附件、检索原文、记忆等一大堆内部字段，自动上报既大又难读。
# 这里只挑排查知识问答用得上的：问了什么 → 命中几条 → 答了什么。
@observe(name="node:knowledge", as_type="span", capture_input=False, capture_output=False)
def node_knowledge(state: dict) -> dict:
    node = sys._getframe().f_code.co_name
    session_id = state["session_id"]
    is_stream = state.get("is_stream", False)
    add_running_task(session_id, node, is_stream)

    query = state.get("request_text") or state.get("original_query") or ""
    filters = state.get("rag_filters") or {}
    update_current_span(
        input={"query": query, "filters": filters, "stream": is_stream},
        metadata={"source": "legacy_rag_chain", "user_id": state.get("user_id") or ""},
    )
    emit(
        session_id,
        AgentEvent.TOOL_CALL,
        {"tool": "rag_chain", "arguments": {"query": query, **filters}},
    )

    try:
        result = answer_with_legacy_chain(
            query=query,
            session_id=session_id,
            user_id=state.get("user_id") or "",
            is_stream=is_stream,
        )
    except Exception as exc:
        # 与 /query 保持一致：不吞异常，交给上层推 error 事件并把本轮标记 failed
        emit(
            session_id,
            AgentEvent.TOOL_RESULT,
            {"tool": "rag_chain", "count": 0, "error": str(exc)},
        )
        add_done_task(session_id, node, is_stream)
        logger.exception(f"[knowledge] 老 RAG 链路执行失败：{exc}")
        update_current_span(output={"error": f"{type(exc).__name__}: {exc}"}, level="ERROR")
        raise

    documents = result.get("documents") or []
    emit(
        session_id,
        AgentEvent.TOOL_RESULT,
        {"tool": "rag_chain", "count": len(documents), "error": None},
    )

    answer = result.get("answer") or ""
    update_current_span(
        output={
            "answer": answer,
            "documents": len(documents),
            "rag_query": result.get("rewritten_query") or query,
            "images": len(result.get("image_urls") or []),
        }
    )
    add_done_task(session_id, node, is_stream)
    return {
        # 答案由老链路的 node_answer_output 生成并推给前端；写回 State 供
        # /api/agent/chat 的同步返回与 /api/agent/state 查看
        "answer": answer,
        "retrieved_documents": documents,
        "rag_query": result.get("rewritten_query") or query,
        "rag_filters": result.get("filters") or {},
        "image_urls": result.get("image_urls") or [],
        # 用户消息由老链路的 node_item_name_confirm 写入；回答与长期记忆抽取由
        # node_answer_output 负责，所以这里不再走 memory_bridge
        "user_message_recorded": True,
        "error": "",
    }
