"""Main Agent 的决策节点（Supervisor）。

决策只用 SupervisorDecision 结构化输出，业务代码读 decision.next_action。

【契约】本模块只产出"用户要什么"：next_action（让谁做）+ task（一句话目标）+ context。
它不产出、也不得引用子 Agent 的内部动作名——动作由子 Agent 自己判定
（见 app/agent/services/document_service.py 的 infer_action_from_task 与
subgraphs/document_understanding.py 的 resolved_action）。

规则兜底（fallback_decision）是**降级路径**：模型不可用时按关键词给出最保守的判断，
产出同样是 task 文本而不是能力名。
"""
import json
from datetime import date

from app.agent.llm import json_completion
from app.agent.schemas.supervisor import SupervisorDecision
from app.agent.services import company_service
from app.agent.services.memory_bridge import format_history, recent_history
from app.conf.agent_config import agent_config
from app.core.load_prompt import load_prompt
from app.core.logger import logger

# 兜底规则使用的关键词（只在模型不可用时生效）
_ADDRESS_HINTS = ("邮寄地址", "回函地址", "通讯地址", "地址", "寄给", "寄到")
_SUMMARY_HINTS = ("总结", "摘要", "概括", "讲了什么", "内容是什么")
_COMPANY_HINTS = ("企业信息", "公司信息", "信用代码", "营业执照信息", "统一社会信用代码")
_APPLICATION_HINTS = ("申请书", "业务申请", "生成申请")

# 统一动作名 → 用户目标文本的桥接表。
# 【为什么需要】agent_config.default_inquiry_action 是历史遗留的"动作名"配置
# （取值如 extract_mailing_address），而契约要求产出 task 文本；这里做一次性桥接。
# 新增的兜底分支不要再用这张表——直接写 task 文本。
_LEGACY_ACTION_TASKS = {
    "extract_mailing_address": "提取该文件中可用于寄送的地址",
    "summarize_document": "总结该文件的内容",
    "extract_company_info": "提取该文件中的企业信息",
}


def _resolved_action(state: dict) -> str:
    """当前生效的文档动作（子 Agent 判定优先，显式覆盖兜底）。仅用于兜底分支的判断。"""
    return str(state.get("resolved_action") or state.get("document_action") or "")

# 一次意图识别
def decide(state: dict) -> SupervisorDecision:
    """做一次结构化决策。"""
    prompt = build_supervisor_prompt(state)
    # SupervisorDecision是返回的目标结构，json schema
    decision = json_completion(SupervisorDecision, prompt, tag="supervisor")
    if decision is None:
        logger.warning("[supervisor] 结构化决策失败，使用规则兜底")
        return fallback_decision(state)
    if decision.next_action == "document" and not decision.task.strip():
        # 不是错误：只上传文件、还没说要做什么时 task 就该是空的（由子 Agent 反问用户）。
        # 但如果模型"忘了说目标"，这里能留下线索，便于区分"模型没说"与"用户没说"。
        logger.info("[supervisor] 本轮 document 分支没有给出 task（用户未说明要做什么？）")
    logger.info(
        f"[supervisor] next={decision.next_action} task={decision.task!r} "
        f"confidence={decision.confidence} reason={decision.reason}"
    )
    return decision


def build_supervisor_prompt(state: dict) -> str:
    return load_prompt(
        "agent_supervisor",
        today=date.today().isoformat(),
        request=state.get("request_text") or "",
        attachments=_format_attachments(state.get("attachments")),
        history=format_history(recent_history(state)),
        subagent_block=_format_subagent_block(state), # 子agent汇报
        document_block=_format_document_block(state), # 文档理解子agent的返回
        knowledge_block=_format_knowledge_block(state), 
        application_block=_format_application_block(state),
    )


def _format_subagent_block(state: dict) -> str:
    """子 Agent 的上行汇报（AgentResult）：主 Agent 据此判断"要不要再补一步"。

    只看 status 三态即可（success / need_user_input / failed），result 内部字段
    属于子 Agent 的私有结构，这里只做摘要展示，不做分支判断。

    刻意**不展示 task_type**：它是子 Agent 的动作名（extract_mailing_address 之类），
    一旦进提示词，模型就可能学着把这套词汇写回自己的 task 里，行为上的耦合会重新长出来。
    要看它请去日志或 GET /api/agent/state。

    source 同理不进提示词（它也是内部标识），只展示子 Agent 自己声明的 source_label。

    只展示**本轮最近一次**汇报（agent_result 是轮次级的）：
    document → application 串起来时，每个决策点看到的都是"刚刚跑完的那个子 Agent"。
    跨轮归档在 agent_results 里，供 /api/agent/state 溯源，不进提示词——
    否则上一轮的 success 会让模型以为"本轮已经做过这件事了"。
    """
    latest = state.get("agent_result") or {}
    if not latest:
        return "（本轮还没有子 Agent 汇报）"
    payload = {
        # 只给"人话"来源名；没有就整个字段省掉，绝不回落到内部标识（那是耦合的入口）
        "来源": latest.get("source_label") or "",
        "status": latest.get("status") or "",
        "message": latest.get("message") or "",
        "reason": latest.get("reason") or "",
        "artifacts": [art.get("name") for art in (latest.get("artifacts") or [])],
    }
    payload = {key: value for key, value in payload.items() if value not in ("", [], None)}
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _format_attachments(attachments) -> str:
    if not attachments:
        return "（无）"
    return "\n".join(
        f"- {item.get('filename') or item.get('file_id')}（{item.get('mime_type') or '未知类型'}）"
        for item in attachments
    )


def _format_document_block(state: dict) -> str:
    analysis = state.get("document_analysis")
    if not analysis:
        return "（尚未理解任何文档）"
    payload = {
        "document_type": analysis.get("document_type"),
        "confidence": analysis.get("confidence"),
        "structured_fields": analysis.get("structured_fields") or {},
        "candidate_addresses": analysis.get("candidate_addresses") or [],
        "available_actions": analysis.get("available_actions") or [],
        "summary": (analysis.get("summary") or "")[:300],
        "error": analysis.get("error"),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _format_knowledge_block(state: dict) -> str:
    docs = state.get("retrieved_documents")
    if not docs:
        return "（本轮还没有检索知识库）"
    brief = [
        {
            "title": doc.get("title"),
            "date": doc.get("date"),
            "company": doc.get("company"),
            "score": doc.get("score"),
        }
        for doc in docs[:5]
    ]
    return json.dumps({"count": len(docs), "top": brief}, ensure_ascii=False)


def _format_application_block(state: dict) -> str:
    if not any(
        state.get(key)
        for key in (
            "company_query", "selected_company", "application_data",
            "template_type", "generated_file_url", "missing_fields",
        )
    ):
        return "（本轮没有进行业务申请书流程）"
    payload = {
        "company_query": state.get("company_query"),
        "company_candidates": [
            item.get("company_name") for item in (state.get("company_candidates") or [])[:5]
        ],
        # 只给模型事实字段：名称相似度这类内部分数不进提示词
        "selected_company": company_service.public_candidate(state.get("selected_company")),
        "application_data": state.get("application_data"),
        "missing_fields": state.get("missing_fields"),
        "template_type": state.get("template_type"),
        "generated_file_url": state.get("generated_file_url"),
        "phase": state.get("application_phase"),
        # 本轮是否已经跑过申请书子图：模型据此避免第二次决策又选 application
        "processed_this_turn": bool(state.get("application_processed")),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)

# 
def fallback_decision(state: dict) -> SupervisorDecision:
    """模型不可用时的规则兜底：只做最保守的判断，绝不编造事实。"""
    request = state.get("request_text") or ""
    analysis = state.get("document_analysis")

    if state.get("generated_file_url"):
        return SupervisorDecision(next_action="answer", reason="申请书已生成", confidence=0.6)
    if state.get("application_processed"):
        return SupervisorDecision(
            next_action="answer", reason="申请书流程本轮已执行", confidence=0.6
        )
    if state.get("retrieved_documents"):
        return SupervisorDecision(next_action="answer", reason="已有检索证据", confidence=0.6)
    if analysis:
        if _resolved_action(state) == "extract_mailing_address":
            return SupervisorDecision(
                next_action="answer",
                task=_LEGACY_ACTION_TASKS["extract_mailing_address"],
                reason="规则兜底：用户要求地址",
                confidence=0.5,
            )
        if any(hint in request for hint in _ADDRESS_HINTS) and analysis.get("candidate_addresses"):
            return SupervisorDecision(
                next_action="answer",
                task="提取该文件中可用于寄送的地址",
                reason="规则兜底：用户要求地址",
                confidence=0.5,
            )
        if any(hint in request for hint in _SUMMARY_HINTS):
            return SupervisorDecision(
                next_action="answer",
                task="总结该文件的内容",
                reason="规则兜底：用户要求总结",
                confidence=0.5,
            )
        if any(hint in request for hint in _COMPANY_HINTS):
            return SupervisorDecision(
                next_action="answer",
                task="提取该文件中的企业信息",
                reason="规则兜底：用户要求企业信息",
                confidence=0.5,
            )
        if any(hint in request for hint in _APPLICATION_HINTS):
            return SupervisorDecision(
                next_action="application",
                task="生成该公司的业务申请书",
                reason="规则兜底：用户要求生成申请书",
                confidence=0.5,
            )
        if agent_config.default_inquiry_action:
            return SupervisorDecision(
                next_action="answer",
                task=_LEGACY_ACTION_TASKS.get(agent_config.default_inquiry_action, ""),
                reason="规则兜底：走配置的默认文档动作",
                confidence=0.4,
            )
        return SupervisorDecision(
            next_action="ask_user",
            task="",
            reason="规则兜底：文档已理解但用户未说明动作",
            confidence=0.4,
            missing_info=["需要用户说明对文档执行什么操作"],
        )
    if state.get("attachments"):
        return SupervisorDecision(
            next_action="document", task="", reason="有附件待理解", confidence=0.6
        )
    if any(hint in request for hint in _APPLICATION_HINTS):
        return SupervisorDecision(
            next_action="application",
            task="生成该公司的业务申请书",
            reason="规则兜底：用户要求生成申请书",
            confidence=0.5,
        )
    if request.strip():
        return SupervisorDecision(
            next_action="knowledge", task="", reason="规则兜底：默认查知识库", confidence=0.4
        )
    return SupervisorDecision(
        next_action="ask_user", task="", reason="没有可处理的内容", confidence=0.3
    )
