"""反问用户的文案组织（HITL 的"提问"半边）。"""
import json

from app.agent.llm import text_completion
from app.core.load_prompt import load_prompt
from app.core.logger import logger

ACTION_LABELS = {
    "extract_mailing_address": "提取邮寄地址",
    "summarize_document": "总结文档内容",
    "extract_company_info": "提取企业信息",
    "generate_business_application": "生成业务申请书",
}


def interrupt_text(payload: dict) -> str:
    """给 SSE 前端用的人类可读提示（不调用模型，纯结构化拼装）。"""
    payload = payload or {}
    reason = payload.get("reason") or ""
    if reason == "multiple_company_candidates":
        return "检测到多个可能的企业主体，请回复序号选择：\n" + _format_candidates(
            payload.get("candidates") or []
        )
    if reason == "confirm_before_generate":
        data = payload.get("application_data") or {}
        return (
            "请确认以下信息，回复「确认」后我将生成业务申请书：\n"
            f"企业名称：{data.get('company_name') or '—'}\n"
            f"统一社会信用代码：{data.get('unified_social_credit_code') or '—'}\n"
            f"省份：{data.get('province') or '—'}\n"
            f"注册地址：{data.get('registered_address') or '—'}\n"
            f"申请日期：{data.get('application_date') or '—'}"
        )
    if reason == "missing_application_fields":
        from app.agent.subgraphs.business_application import FIELD_LABELS

        labels = [FIELD_LABELS.get(field, field) for field in (payload.get("missing_fields") or [])]
        return (
            "生成业务申请书还需要以下信息："
            + "、".join(labels)
            + "。请直接补充，或上传营业执照由系统自动识别。"
        )
    if reason == "company_not_found":
        return payload.get("message") or "暂未找到匹配的企业，请提供企业完整名称，或上传营业执照。"
    if reason == "missing_company_query":
        return payload.get("message") or "请提供企业完整名称，或上传营业执照。"
    if reason == "invalid_company_selection":
        return "没有识别到你的选择，请回复候选序号（如 1），或直接给出企业完整名称。"
    return payload.get("message") or "需要你确认后继续。"

DOCUMENT_TYPE_LABELS = {
    "inquiry_letter": "询证函",
    "business_license": "营业执照",
    "generic_document": "普通文档",
    "unknown": "未能识别的文档",
}


def compose_clarification(state: dict) -> str:
    """生成「请用户补充信息」的话术；模型失败时直接给结构化说明。"""
    situation = build_situation(state)
    text = text_completion(load_prompt("agent_ask_user", situation=situation), tag="ask_user")
    if text:
        return text.strip()
    logger.warning("[clarify] 反问文案生成失败，使用结构化兜底文案")
    return situation


def build_situation(state: dict) -> str:
    """把 State 里需要用户处理的信息整理成一段结构化描述。"""
    pieces: list[str] = []

    phase = state.get("application_phase")
    if phase == "need_user_input":
        reason = state.get("interrupt_reason") or ""
        payload = state.get("interrupt_payload") or {}
        if reason == "company_not_found" or reason == "missing_company_query":
            pieces.append(
                payload.get("message")
                or f"没有找到与「{state.get('company_query')}」匹配的企业。"
                "请提供企业完整名称，或上传营业执照。"
            )
        elif reason == "missing_application_fields" or state.get("missing_fields"):
            from app.agent.subgraphs.business_application import FIELD_LABELS

            labels = [
                FIELD_LABELS.get(field, field) for field in (state.get("missing_fields") or [])
            ]
            pieces.append(
                "生成业务申请书还需要以下信息："
                + "、".join(labels)
                + "。请直接补充这些信息，或上传营业执照由系统自动识别。"
            )
        elif payload.get("candidates"):
            pieces.append("检测到多个可能的企业主体，请选择：" + _format_candidates(payload["candidates"]))
        elif phase == "need_user_input" and state.get("interrupt_reason"):
            pieces.append("需要你补充信息后继续。")
    elif phase == "cancelled":
        pieces.append("本次业务申请书生成已取消，如需继续请告诉我新的要求。")
    elif phase == "error":
        pieces.append(f"申请书生成失败：{state.get('error') or '未知原因'}。请稍后重试或联系管理员。")

    analysis = state.get("document_analysis") or {}
    task_result = state.get("document_task_result") or analysis.get("task_result") or {}
    status = task_result.get("status")
    addresses = state.get("candidate_addresses") or analysis.get("candidate_addresses") or []
    # 动作以子 Agent 自己判定的为准（resolved_action），显式覆盖通道兜底
    document_action = (
        state.get("resolved_action")
        or state.get("document_action")
        or task_result.get("action")
    )

    if status == "ambiguous":
        # 地址歧义由 Document Agent 判定，主 Agent 只负责问用户
        candidates = (task_result.get("data") or {}).get("candidates") or [
            {"value": value} for value in addresses
        ]
        pieces.append(
            "文档中发现了多个同样优先级的地址，请确认使用哪一个：\n"
            + "\n".join(
                f"{index}. {item.get('value')}（{item.get('label') or '地址'}）"
                for index, item in enumerate(candidates, start=1)
            )
        )
    elif status == "unsupported":
        doc_label = DOCUMENT_TYPE_LABELS.get(
            analysis.get("document_type"), "这份文档"
        )
        actions = [
            ACTION_LABELS.get(action, action) for action in (analysis.get("available_actions") or [])
        ]
        pieces.append(
            f"{task_result.get('reason') or doc_label + '不支持这个操作'}。"
            + (f"可以帮你" + "、".join(actions) + "，要不要换一个？" if actions else "请换一份对应的文档。")
        )
    elif status == "not_found":
        pieces.append(
            "没能在文档里识别到需要的信息。建议换一张更清晰的扫描件，或直接把内容告诉我。"
        )
    elif status == "failed" or (analysis and analysis.get("error")):
        pieces.append(
            f"文档处理失败：{analysis.get('error') or task_result.get('reason')}。"
            "请重新上传更清晰的图片或 PDF。"
        )
    elif analysis and not document_action:
        doc_label = DOCUMENT_TYPE_LABELS.get(analysis.get("document_type"), "文档")
        actions = [
            ACTION_LABELS.get(action, action) for action in (analysis.get("available_actions") or [])
        ]
        pieces.append(
            f"已识别为{doc_label}。可以帮你" + "、".join(actions) + "，请告诉我需要进行哪项操作。"
        )

    # 子 Agent 自己组织的用户可见说明（AgentResult.message）作为兜底：
    # 谁最清楚"该问用户什么"，谁就负责把这句话写好。
    # 放在最后是为了不与上面的细节分支重复——两边都写得出来时以主 Agent 的更具体版本为准。
    if not pieces:
        agent_result = state.get("agent_result") or {}
        if agent_result.get("status") == "need_user_input" and agent_result.get("message"):
            pieces.append(str(agent_result["message"]))

    if not pieces:
        missing = state.get("missing_info") or []
        pieces.append(
            "还需要以下信息才能继续：" + "、".join(map(str, missing))
            if missing
            else "请补充一下你需要我做什么，我会继续处理。"
        )
    return "\n\n".join(str(piece) for piece in pieces)


def _format_candidates(candidates: list) -> str:
    lines = []
    for index, item in enumerate(candidates, start=1):
        if isinstance(item, str):
            lines.append(f"{index}. {item}")
            continue
        name = item.get("company_name") or "未知企业"
        code = item.get("unified_social_credit_code") or "信用代码未知"
        region = item.get("province") or ""
        lines.append(f"{index}. {name}（统一社会信用代码：{code}{'，' + region if region else ''}）")
    return "\n" + "\n".join(lines)
