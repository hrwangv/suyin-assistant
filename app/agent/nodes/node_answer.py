"""Main Agent 的最终回答节点。

职责（规范第 4.1 节）：读取工具结果 → 组织最终回答。
这里不做任何检索 / OCR / 企业查询，那些已经在各自的 Tool / Subgraph 里完成。
"""
import json
import re
import sys
from datetime import date

from app.agent.config_helpers import agent_model_name
from app.agent.events import AgentEvent, emit
from app.agent.nodes.clarify import ACTION_LABELS, compose_clarification
from app.agent.services import company_service
from app.agent.services.memory_bridge import (
    format_history,
    recent_history,
    record_answer,
)
from app.conf.answer_config import answer_config
from app.core.load_prompt import load_prompt
from app.core.logger import logger
from app.core.tracing import llm_config, observe, update_current_span
from app.llm.lm_utils import get_llm_client
from app.utils.task_utils import add_done_task, add_running_task, set_task_result
from app.utils.token_budget import count_tokens, fit_token_budget

_IMAGE_RE = re.compile(r"!\[.*?\]\((.*?)\)")
_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp")


@observe(name="node:answer", as_type="span", capture_input=False, capture_output=False)
def node_answer(state: dict) -> dict:
    node = sys._getframe().f_code.co_name
    session_id = state["session_id"]
    is_stream = state.get("is_stream", False)
    add_running_task(session_id, node, is_stream)

    needs_clarification = _needs_clarification(state)
    # 入参/出参显式上报：只留「问的是什么、手里有几条证据、走的是反问还是作答」
    update_current_span(
        input={
            "question": state.get("request_text") or state.get("original_query") or "",
            "documents": len(state.get("retrieved_documents") or []),
            "clarify": needs_clarification,
        }
    )

    if needs_clarification:
        answer = compose_clarification(state)
        _push_text(state, answer)
    else:
        answer = _generate_answer(state, build_answer_prompt(state))

    images = _extract_images(state.get("retrieved_documents") or [])
    update_current_span(output={"answer": answer, "images": len(images)})
    _push_final(state, answer, images)
    emit(session_id, AgentEvent.AGENT_MESSAGE, {"content": answer})

    answer_state = {**state, "answer": answer}
    record_answer(answer_state)

    add_done_task(session_id, node, is_stream)
    return {"answer": answer, "image_urls": images, "awaiting_user_confirmation": False}


def _needs_clarification(state: dict) -> bool:
    """需要用户补充信息时，回答节点直接输出反问，而不是让模型编。

    只读结构化结论：地址歧义 / 不支持 / 没找到 都由 Document Agent 判定并写在
    document_task_result 里，这里只决定"要不要问用户"。
    """
    if state.get("application_phase") in {"need_user_input", "cancelled", "error"}:
        return True

    task_result = state.get("document_task_result") or {}
    if task_result.get("status") in {"ambiguous", "unsupported", "not_found", "failed"}:
        return True

    analysis = state.get("document_analysis") or {}
    if analysis.get("error") and not state.get("retrieved_documents"):
        return True
    return False


def build_answer_prompt(state: dict) -> str:
    """组装回答 prompt，并对工具结果做 token 预算裁剪。"""
    blocks = _context_blocks(state)
    budget = answer_config.available_input_budget
    kept, dropped = fit_token_budget(blocks, budget, drop="last")
    if dropped:
        logger.info(f"[answer] 工具结果超预算，已丢弃 {dropped} 条（预算 {budget} token）")
    context = "\n\n".join(kept) if kept else "（本轮没有工具结果，属于普通对话）"

    history = format_history(recent_history(state))
    question = state.get("request_text") or ""
    prompt = load_prompt(
        "agent_answer",
        today=date.today().isoformat(),
        history=history,
        context=context,
        question=question,
    )
    logger.info(f"[answer] prompt 组装完成，约 {count_tokens(prompt)} token")
    return prompt


def _context_blocks(state: dict) -> list[str]:
    """把 State 里的工具结果整理成条目（便于按 token 整条裁剪）。"""
    blocks: list[str] = []

    analysis = state.get("document_analysis") or {}
    if analysis:
        blocks.append("【文档理解结果】\n" + _format_document_evidence(state, analysis))

    docs = state.get("retrieved_documents") or []
    if docs:
        blocks.append("【企业知识库证据】")
        for index, doc in enumerate(docs, start=1):
            parts = [str(index), f"source={doc.get('source')}"]
            if doc.get("title"):
                parts.append(f"title={doc['title']}")
            if doc.get("file_title"):
                parts.append(f"file={doc['file_title']}")
            if doc.get("date"):
                parts.append(f"date={doc['date']}")
            if doc.get("category"):
                parts.append(f"category={doc['category']}")
            if doc.get("company"):
                parts.append(f"company={doc['company']}")
            if doc.get("score") is not None:
                parts.append(f"score={doc['score']}")
            blocks.append("[" + "][".join(parts) + "]\n" + str(doc.get("text") or ""))

    application_block = _format_application_evidence(state)
    if application_block:
        blocks.append("【业务申请书进展】\n" + application_block)

    return blocks


def _format_document_evidence(state: dict, analysis: dict) -> str:
    # 动作以子 Agent 自己判定的为准（resolved_action），显式覆盖通道兜底
    action = state.get("resolved_action") or state.get("document_action")
    payload = {
        "document_type": analysis.get("document_type"),
        # 事实层（文件里有什么）：公司名/信用代码/地址/法人/联系人/电话/日期/金额
        "facts": state.get("document_facts") or analysis.get("facts") or {},
        # 任务结果（这次任务做得怎么样）：status / address_decision / preferred_address
        "task_result": state.get("document_task_result") or analysis.get("task_result") or {},
        "structured_fields": analysis.get("structured_fields") or {},
        "candidate_addresses": analysis.get("candidate_addresses") or [],
        "summary": analysis.get("summary") or "",
        "error": analysis.get("error"),
        "requested_action": action,
        "action_label": ACTION_LABELS.get(action, ""),
        # 子 Agent 的上行汇报（status / message）：主 Agent 只按 status 决策
        "agent_result": _agent_result_brief(state),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _agent_result_brief(state: dict) -> dict:
    """把子 Agent 的上行汇报压成一行摘要（status / task_type / message / reason）。"""
    result = state.get("agent_result") or {}
    if not result:
        return {}
    return {
        "source": result.get("source"),
        "status": result.get("status"),
        "task_type": result.get("task_type"),
        "message": result.get("message"),
        "reason": result.get("reason"),
        "artifacts": [item.get("name") for item in (result.get("artifacts") or [])],
    }


def _format_application_evidence(state: dict) -> str:
    if not any(
        state.get(key)
        for key in (
            "application_data", "selected_company", "company_candidates",
            "template_type", "generated_file_url", "missing_fields", "error",
        )
    ):
        return ""
    payload = {
        "company_query": state.get("company_query"),
        # 只给模型事实字段：本地算的名称相似度这类内部分数不进提示词
        "selected_company": company_service.public_candidate(state.get("selected_company")),
        "application_data": state.get("application_data"),
        "template_type": state.get("template_type"),
        "missing_fields": state.get("missing_fields"),
        "generated_file_name": state.get("generated_file_name"),
        "generated_file_url": state.get("generated_file_url"),
        "phase": state.get("application_phase"),
        "error": state.get("error"),
        "agent_result": _agent_result_brief(state),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _generate_answer(state: dict, prompt: str) -> str:
    """调用模型生成回答：流式推 delta，非流式写任务结果。"""
    model = get_llm_client(model=agent_model_name())
    session_id = state["session_id"]
    if state.get("is_stream", False):
        answer = ""
        for chunk in model.stream(prompt, config=llm_config()):
            delta = getattr(chunk, "content", "") or ""
            if not delta:
                continue
            answer += delta
            emit(session_id, "delta", {"delta": delta})
        return answer.strip()

    response = model.invoke(prompt, config=llm_config())
    content = getattr(response, "content", "") or ""
    content = content.strip()
    set_task_result(session_id, "answer", content)
    return content


def _push_text(state: dict, text: str) -> None:
    """把已经生成好的整段文本按流式协议推给前端。"""
    if state.get("is_stream", False):
        emit(state["session_id"], "delta", {"delta": text})
    else:
        set_task_result(state["session_id"], "answer", text)


def _push_final(state: dict, answer: str, images: list[str]) -> None:
    if not state.get("is_stream", False):
        return
    payload = {"answer": answer, "status": "completed", "image_urls": images}
    # 判"本轮是否出了文件"要用轮次级标记：generated_file_* 是会话级的，
    # 拿它判会让生成过一次之后每轮回答都带下载卡片（历史 bug）
    if state.get("generated_this_turn"):
        payload["type"] = "file"
        payload["file"] = {
            "name": state.get("generated_file_name"),
            "url": state.get("generated_file_url"),
        }
    emit(state["session_id"], "final", payload)


def _extract_images(docs: list[dict]) -> list[str]:
    """从检索证据里提取图片地址（沿用 /query 的回答行为）。"""
    images: list[str] = []
    seen: set[str] = set()
    for doc in docs:
        url = doc.get("url")
        if isinstance(url, str) and url.lower().endswith(_IMAGE_SUFFIXES) and url not in seen:
            images.append(url)
            seen.add(url)
        text = doc.get("text") or doc.get("content") or ""
        for image_url in _IMAGE_RE.findall(text):
            if image_url not in seen:
                images.append(image_url)
                seen.add(image_url)
    return images
