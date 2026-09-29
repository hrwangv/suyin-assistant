"""Business Application Subgraph。

    业务申请书生成（6 个节点：准备 → 确认 → 生成 三段 + 两个中断点 + 一个解析）

    START
     ↓
    node_parse_application_request   解析请求（只抽参数，不查企业）
     ↓
    node_resolve_company             调 Company MCP 查企业
     ↓  ├── 命中唯一 → prepare
        ├── 多个候选 → node_company_select（interrupt）
        └── 没找到   → render_and_finalize（提示用户补全 / 上传营业执照）
     ↓
    node_prepare_application         准备数据（合并字段 → 校验必填 → 选模板）
     ↓  ├── 缺字段   → render_and_finalize（反问补字段，轮次上限内）
        ├── 模板缺失 → render_and_finalize（报错）
        └── 就绪     → node_human_confirmation（要求确认时）
     ↓
    node_human_confirmation          interrupt：生成前确认
     ↓  ├── 用户顺手补了字段 → 回 node_prepare_application
        └── 确认 / 取消     → render_and_finalize
     ↓
    node_render_and_finalize         需要时渲染 DOCX，然后统一写出 AgentResult
     ↓
    END（回到主图 node_supervisor 做第二次决策，由它决定报结果还是反问）

【两条边界，都是 LangGraph 的语义要求】
1. interrupt 只放在「只负责暂停」的节点里（node_company_select /
   node_human_confirmation）：恢复时会**重放整个中断节点**，把 interrupt 和业务写入
   放进同一个节点，前半段的副作用（查 MCP、渲染 DOCX、SSE 推送）会被重复执行。
2. 非中断的纯计算节点可以自由合并：node_prepare_application 就是原来的
   merge_document_information + collect_required_fields + choose_template 三个节点
   合成的（它们之间没有中断点、没有模型 / MCP / 文件副作用，拆开只是让图上多两跳）。
"""
import re
import sys
from datetime import date
from functools import partial
from typing import Optional, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import BaseModel

from app.agent.events import AgentEvent, emit
from app.agent.llm import json_completion
from app.agent.schemas.agent_result import APPLICATION_PHASE_MAP, make_agent_result
from app.agent.schemas.application import REQUIRED_APPLICATION_FIELDS, ApplicationData
from app.agent.services import company_service, docx_service
from app.agent.services.docx_service import TemplateNotFound
from app.conf.agent_config import agent_config
from app.core.load_prompt import load_prompt
from app.core.logger import logger
from app.utils.task_utils import add_done_task, add_running_task

FIELD_LABELS = {
    "company_name": "企业名称",
    "unified_social_credit_code": "统一社会信用代码",
    "province": "省份",
    "registered_address": "注册地址",
    "city": "城市",
    "legal_person": "法定代表人",
    "enterprise_nature": "企业性质",
    "registered_capital": "注册资本",
    "notes": "补充说明",
}


class ApplicationState(TypedDict, total=False):
    """申请书子图用到的状态（AgentState 的子集，字段语义见 app/agent/state.py）。

    与 Document 子图不同，这里的多数字段是**会话级**：申请书流程天生跨轮
    （选主体 → 补字段 → 确认 → 生成），所以 company_* / application_* /
    user_fields / generated_* 都必须在多轮之间保留。
    """

    # --- 运行环境与输入（只读） ---
    session_id: str          # 节点进度与 SSE 用
    thread_id: str
    is_stream: bool
    request_text: str        # 本轮用户原话（解析企业名 / 业务类型）
    attachments: list        # 只用于判断"本轮是否又上传了文件"
    document_fields: dict    # Document 子图给的字段，作为最低优先级来源
    # Document 子图给的事实层（含 address / province / city / legal_person 等映射后的字段）。
    # 必须在这里声明：LangGraph 在子图作为节点被调用时，只把"子图 schema 里声明过的键"
    # 传进来——以前没声明，所以 node_resolve_company / node_merge_document_information
    # 读它永远是 None（静默降级到 document_fields），这是一处真实的隐式契约 bug。
    document_facts: dict
    # --- 企业主体（会话级：多候选时下一轮才回复序号） ---
    company_query: str
    company_candidates: list  # 已排序候选，interrupt 的候选项来自它
    selected_company: dict    # 企业事实的唯一权威来源
    company_source: str       # company_mcp / user_input / unconfigured / error
    # --- 申请书字段（会话级） ---
    application_data: dict    # 已收集字段（合并后的结果）
    user_fields: dict         # 用户明确给出的值，优先级最高
    # 本轮确认环节是否新补了字段（轮次级）。回边判据必须用它而不是 user_fields：
    # user_fields 是会话级累积的，一旦非空就永远非空，会让人卡在"确认 → 准备"的循环里。
    user_fields_updated: bool
    required_fields: list     # 必填字段清单
    missing_fields: list      # 本轮缺什么（反问文案读它）
    application_date: str
    field_ask_rounds: int     # 单轮补充上限保护
    # --- 阶段机（回答节点据此决定报结果还是反问） ---
    application_phase: str
    # 本轮是否已经跑过申请书子图（每轮重置）。主图的路由保护读它：
    # 子图出口回到 node_supervisor 后，Supervisor 再选 application 也不会重复进子图。
    application_processed: bool
    # --- 模板与产物（会话级：生成结果要能被下一轮引用） ---
    template_type: str        # jiangsu / other
    template_path: str
    generated_file_path: str
    generated_file_name: str
    generated_file_url: str
    # 本轮真的渲染出了文件（轮次级）：回答出口据此决定要不要给下载卡片
    generated_this_turn: bool
    # --- HITL ---
    interrupt_reason: str     # 为什么暂停
    interrupt_payload: dict   # 暂停时给用户看的载荷（也是 interrupt(value)）
    missing_info: list        # 需要用户补充的信息
    resume_value: object      # 占位（resume 值由 LangGraph 交给节点）
    # --- 出口 ---
    error: str                # 取消 / 模板缺失 / 渲染失败等原因
    # 子 Agent 上行汇报（见 schemas/agent_result.py）：主 Agent 只按 status 决策。
    # 必须声明，否则本子图写出去的键不会被父图合并。
    agent_result: dict
    agent_results: dict


application_state_t = ApplicationState


def _task(state: dict, node: str):
    add_running_task(state["session_id"], node, state.get("is_stream", False))


def _done(state: dict, node: str):
    add_done_task(state["session_id"], node, state.get("is_stream", False))


def _emit(state: dict, event: str, data: dict):
    emit(state.get("session_id", ""), event, data)


# ------------------------- 解析请求 -------------------------


class _ParsedRequest(BaseModel):
    company_query: Optional[str] = None
    notes: Optional[str] = None


def node_parse_application_request(state: dict) -> dict:
    """理解请求，提取结构化参数；不查企业。"""
    node = sys._getframe().f_code.co_name
    _task(state, node)
    # 调用大模型
    parsed = json_completion(
        _ParsedRequest,
        load_prompt("application_parse", text=state.get("request_text") or ""),
        tag="application_parse",
    )
    updates: dict = {"application_phase": "parsed"}
    if parsed is None:
        _done(state, node)
        return updates

    if parsed.company_query:
        updates["company_query"] = parsed.company_query.strip()
    data = dict(state.get("application_data") or {})
    if parsed.notes:
        data["notes"] = parsed.notes
    updates["application_data"] = data
    _done(state, node)
    return updates


# ------------------------- 企业主体 -------------------------


def node_resolve_company(state: dict) -> dict:
    """调 Company MCP 查企业；不唯一就交给 HITL。"""
    node = sys._getframe().f_code.co_name
    _task(state, node)

    document_fields = state.get("document_fields") or {}
    document_facts = state.get("document_facts") or {}
    application_data = state.get("application_data") or {}
    query = (
        state.get("company_query")
        or document_fields.get("company_name")
        # 文档事实层（Document Agent 的新输出契约）也可以作为企业名来源
        or document_facts.get("company_name")
        or application_data.get("company_name")
    )
    if not query:
        _done(state, node)
        _emit(
            state,
            AgentEvent.HUMAN_CONFIRMATION_REQUIRED,
            {"reason": "missing_company_query", "message": "请提供企业完整名称，或上传营业执照。"},
        )
        return {
            "application_phase": "need_user_input",
            "interrupt_reason": "missing_company_query",
            "interrupt_payload": {
                "reason": "missing_company_query",
                "message": "请提供企业完整名称，或上传营业执照。",
            },
            "missing_info": ["企业名称或营业执照"],
        }

    result = company_service.resolve_company(query)
    candidates = result.get("candidates") or []
    updates: dict = {
        "company_query": query,
        "company_candidates": candidates,
        "company_source": result.get("source"),
        "application_phase": "company_resolved",
        "interrupt_reason": "",
        "interrupt_payload": {},
    }

    if not candidates:
        updates.update(
            {
                "application_phase": "need_user_input",
                "interrupt_reason": "company_not_found",
                "interrupt_payload": {
                    "reason": "company_not_found",
                    "query": query,
                    "message": f"暂未找到与「{query}」匹配的企业，请提供企业完整名称，或上传营业执照。",
                },
                "missing_info": [f"企业主体（{query} 未匹配到）"],
            }
        )
        _emit(
            state,
            AgentEvent.HUMAN_CONFIRMATION_REQUIRED,
            {"reason": "company_not_found", "query": query},
        )
        _done(state, node)
        return updates

    picked = (
        company_service.pick_company(candidates, query)
        if agent_config.company_require_confirmation
        else candidates[0]
    )
    if picked:
        updates["selected_company"] = picked
        updates["application_phase"] = "company_selected"
        logger.info(f"[application] 自动选中企业：{picked.get('company_name')}")
    else:
        updates.update(
            {
                "application_phase": "need_company_choice",
                "interrupt_reason": "multiple_company_candidates",
                "interrupt_payload": {
                    "reason": "multiple_company_candidates",
                    "query": query,
                    # 给用户看的候选只带事实字段（内部分数不外泄到前端）
                    "candidates": [
                        company_service.public_candidate(item) for item in candidates
                    ],
                },
                "missing_info": ["请确认企业主体"],
            }
        )
        _emit(
            state,
            AgentEvent.COMPANY_CANDIDATES,
            {"query": query, "candidates": _candidate_brief(candidates)},
        )
        _emit(
            state,
            AgentEvent.HUMAN_CONFIRMATION_REQUIRED,
            {"reason": "multiple_company_candidates", "candidates": _candidate_brief(candidates)},
        )
    _done(state, node)
    return updates


def node_company_select(state: dict) -> dict:
    """只负责暂停：让用户从多个候选里选（interrupt + Command(resume)）。"""
    node = sys._getframe().f_code.co_name
    _task(state, node)
    payload = state.get("interrupt_payload") or {}
    value = interrupt(payload)
    options = payload.get("candidates") or state.get("company_candidates") or []
    parsed = _parse_user_input(
        pending="请确认企业主体",
        options=[f"{item.get('company_name')}" for item in options],
        text=_as_text(value),
    )
    _done(state, node)

    updates: dict = {"resume_value": None, "interrupt_reason": "", "interrupt_payload": {}}
    candidates = state.get("company_candidates") or []

    index = parsed.selected_index
    if index and 1 <= index <= len(candidates):
        updates["selected_company"] = candidates[index - 1]
        updates["application_phase"] = "company_selected"
        _emit(
            state,
            AgentEvent.COMPANY_CANDIDATES,
            {"selected": candidates[index - 1].get("company_name")},
        )
        return updates

    if parsed.company_name:
        updates["company_query"] = parsed.company_name
        updates["company_candidates"] = []
        updates["selected_company"] = {}
        updates["application_phase"] = "parsed"
        return updates

    if parsed.unified_social_credit_code:
        company = {
            "company_name": parsed.company_name or state.get("company_query"),
            "unified_social_credit_code": parsed.unified_social_credit_code,
            "province": parsed.province,
            "registered_address": parsed.registered_address,
            "city": parsed.city,
            "source": "user_input",
        }
        updates["selected_company"] = _prune(company)
        updates["application_phase"] = "company_selected"
        return updates

    if parsed.confirm is False:
        updates["application_phase"] = "cancelled"
        updates["error"] = "用户取消了申请书生成"
        return updates

    updates.update(
        {
            "application_phase": "need_company_choice",
            "interrupt_reason": "invalid_company_selection",
            "interrupt_payload": {
                "reason": "invalid_company_selection",
                "candidates": candidates,
            },
            "missing_info": ["请回复候选序号，或给出企业完整名称"],
        }
    )
    return updates


def _route_next(state: dict, source: str = "") -> str:
    """阶段机 → 下一个节点。

    原来四个路由函数（_route_after_resolve / _route_after_company_select /
    _route_after_prepare / _route_after_confirmation）合并成这一个：它们读的是同一个
    `application_phase`，差别只在"从哪个节点出来"。source 参数就是用来区分这一点的，
    因此行为与合并前完全一致：

        node_resolve_company     company_selected → 准备；need_company_choice → HITL；其余收口
        node_company_select      company_selected → 准备；parsed（用户给了新名字）→ 回查；其余收口
        node_prepare_application template_ready → 确认（关掉确认就直出）；缺字段 / 模板错 → 收口
        node_human_confirmation  本轮补了字段 → 回准备重算；确认 / 取消 → 收口

    【为什么 need_company_choice 要按 source 区分】同一个阶段名在两个位置含义不同：
    从 resolve 出来表示"刚查到多个候选"，要进 HITL 让用户选；从 select 出来表示
    "用户这次的回复没解析出有效选择"，这时应当收口给主 Agent 重新问，
    而不是原地再弹一次同样的中断。
    """
    phase = state.get("application_phase")

    if phase == "company_selected":
        return "node_prepare_application"

    if source == "node_resolve_company":
        if phase == "need_company_choice":
            return "node_company_select"
        return "node_render_and_finalize"

    if source == "node_company_select":
        if phase == "parsed":  # 用户换了企业名 → 带着新 query 回查
            return "node_resolve_company"
        return "node_render_and_finalize"

    if source == "node_prepare_application":
        if phase == "template_ready" and agent_config.require_confirm_before_render:
            return "node_human_confirmation"
        return "node_render_and_finalize"

    if source == "node_human_confirmation":
        # 判据必须是"本轮"有没有新补（user_fields_updated），不能用累积的 user_fields：
        # 一旦用户补过一次它就永远非空，会让人卡在"确认 → 准备"的循环里出不来。
        if phase != "cancelled" and state.get("user_fields_updated"):
            return "node_prepare_application"
        return "node_render_and_finalize"

    return "node_render_and_finalize"


# ------------------------- 字段合并与校验 -------------------------


def node_prepare_application(state: dict) -> dict:
    """把生成文档需要的数据准备齐：合并字段 → 校验必填 → 选模板。

    合并了原来的三个纯计算节点（node_merge_document_information /
    node_collect_required_fields / node_choose_template）：三步之间没有中断点，
    也没有模型 / MCP / 文件副作用，拆成三个节点只是让图上多两跳、多两轮状态合并。
    现在整条链路只剩"准备 → 确认 → 生成"三段。

    【边界】中断点 node_human_confirmation 仍然独立：LangGraph 恢复时会重放整个
    中断节点，把它并进这里会让重复合并、重复选模板，甚至重复渲染。
    """
    node = sys._getframe().f_code.co_name
    _task(state, node)

    allowed = set(ApplicationData.model_fields.keys())
    merged: dict = {}
    # 字段来源与优先级（写在后面的覆盖前面的）：
    #   1. application_data  上一轮合并出的快照，仅作兜底（会话级、跨轮保留）
    #   2. document_fields   文档子图的原始抽取字段（只给原始字段、没给事实层的调用方靠它）
    #   3. document_facts    文档子图的对外事实层（契约字段，键名映射后合并）
    #   4. selected_company  Company MCP 查到的主体信息，企业事实的唯一权威来源
    #   5. user_fields       用户明确给出的值，优先级最高
    # 【为什么 application_data 必须排第一】它装的是**上一轮的合并结果**。
    # 排在最后（改动前就是）等于优先级最高：用户这轮说"公司名改成 XX"，
    # user_fields 收下了，但重新合并时被旧快照盖回去，最终仍按旧企业名出文件。
    for source in (
        state.get("application_data") or {},
        state.get("document_fields") or {},
        _facts_to_application_fields(state.get("document_facts") or {}),
        state.get("selected_company") or {},
        state.get("user_fields") or {},
    ):
        for key, value in source.items():
            if key in allowed and value not in (None, "", [], {}):
                merged[key] = value

    if not merged.get("province") and merged.get("registered_address"):
        guessed = _guess_province(merged["registered_address"])
        if guessed:
            merged["province"] = guessed
    merged.setdefault("application_date", date.today().isoformat())

    updates: dict = {
        "application_data": merged,
        "application_date": merged["application_date"],
    }

    # ---- 二、校验必填字段：缺了就反问用户（本轮到此结束） ----
    missing = [
        field
        for field in REQUIRED_APPLICATION_FIELDS
        if not str(merged.get(field) or "").strip()
    ]
    rounds = int(state.get("field_ask_rounds") or 0)
    updates["required_fields"] = list(REQUIRED_APPLICATION_FIELDS)
    updates["missing_fields"] = missing
    if missing:
        labels = [FIELD_LABELS.get(field, field) for field in missing]
        updates.update(
            {
                "application_phase": "need_user_input",
                "interrupt_reason": "missing_application_fields",
                "interrupt_payload": {
                    "reason": "missing_application_fields",
                    "missing_fields": missing,
                    "labels": labels,
                },
                "missing_info": labels,
                "field_ask_rounds": rounds + 1,
            }
        )
        if rounds + 1 >= agent_config.max_field_ask_rounds:
            logger.warning("[application] 缺字段补充轮次已达上限，结束本轮")
        else:
            _emit(
                state,
                AgentEvent.HUMAN_CONFIRMATION_REQUIRED,
                {"reason": "missing_application_fields", "missing_fields": missing},
            )
        _done(state, node)
        return updates

    # ---- 三、选模板：江苏 → 江苏省版；其它地区 → 通用版 ----
    province = merged.get("province")
    try:
        template_type, template_path = docx_service.choose_template(province)
    except TemplateNotFound as exc:
        logger.error(f"[application] 模板缺失：{exc}")
        _done(state, node)
        updates.update(
            {
                "application_phase": "error",
                "error": str(exc),
                "missing_info": ["系统模板缺失，请联系管理员"],
            }
        )
        return updates

    _emit(
        state,
        AgentEvent.TEMPLATE_SELECTED,
        {"template_type": template_type, "province": province},
    )
    if agent_config.require_confirm_before_render:
        _emit(
            state,
            AgentEvent.HUMAN_CONFIRMATION_REQUIRED,
            {"reason": "confirm_before_generate", "application_data": merged},
        )
    _done(state, node)
    updates.update(
        {
            "template_type": template_type,
            "template_path": template_path,
            "application_phase": "template_ready",
            "interrupt_reason": "",
        }
    )
    return updates


def _facts_to_application_fields(facts: dict) -> dict:
    """把 Document Facts（对外契约的字段名）映射成申请书字段名。"""
    if not facts:
        return {}
    return {
        "company_name": facts.get("company_name"),
        "unified_social_credit_code": facts.get("unified_social_credit_code"),
        "registered_address": facts.get("address"),
        "province": facts.get("province"),
        "city": facts.get("city"),
        "legal_person": facts.get("legal_person"),
        "enterprise_nature": facts.get("enterprise_nature"),
        "registered_capital": facts.get("registered_capital"),
    }


# ------------------------- 模板与 HITL -------------------------


def node_human_confirmation(state: dict) -> dict:
    """只负责暂停：生成前的最终确认。"""
    node = sys._getframe().f_code.co_name
    _task(state, node)
    value = interrupt(
        {
            "reason": "confirm_before_generate",
            "application_data": state.get("application_data"),
            "template_type": state.get("template_type"),
        }
    )
    parsed = _parse_user_input(
        pending="确认是否生成业务申请书",
        options=[],
        text=_as_text(value),
    )
    _done(state, node)

    updates: dict = {"resume_value": None}
    user_fields = dict(state.get("user_fields") or {})
    supplied: dict = {}
    for field in ApplicationData.model_fields.keys():
        field_value = getattr(parsed, field, None)
        if field_value:
            supplied[field] = field_value
    # 【为什么要这个标记】user_fields 是会话级累积的（跨轮保留），不能用它判断
    # "本轮有没有新补字段"——一旦用户补过一次，它就永远非空，路由会一直回到准备节点，
    # 用户再怎么确认都生成不出文档。这个标记是轮次级的，只描述本轮。
    updates["user_fields_updated"] = bool(supplied)
    if supplied:
        updates["user_fields"] = {**user_fields, **supplied}
    if parsed.confirm is False:
        updates["application_phase"] = "cancelled"
        updates["error"] = "用户取消了申请书生成"
    return updates


# ------------------------- 渲染 -------------------------


def node_render_and_finalize(state: dict) -> dict:
    """收口：需要生成就渲染，然后无论成败都写出统一的 AgentResult。

    合并了原来的 node_render_docx 与 node_application_result——前者是"有副作用的生成"，
    后者是"出口组装"，两者是所有分支的共同终点，中间没有中断点，合成一个出口节点后
    图上少一跳、少一次状态合并。

    【为什么不能把 node_human_confirmation 也并进来】恢复会重放整个中断节点：
    合并之后用户一确认，DOCX 就会被渲染两次（重复生成文件）。
    """
    node = sys._getframe().f_code.co_name
    _task(state, node)
    updates: dict = {}
    enter_phase = state.get("application_phase") or "unknown"

    # ---- 一、需要时渲染（字段齐全且模板已选好才会走到这里） ----
    if enter_phase == "template_ready" and state.get("template_path"):
        data = state.get("application_data") or {}
        _emit(state, AgentEvent.DOCUMENT_GENERATING, {"company_name": data.get("company_name")})
        try:
            rendered = docx_service.render_application(data)
        except Exception as exc:
            logger.exception(f"[application] DOCX 渲染失败：{exc}")
            updates = {"application_phase": "error", "error": f"DOCX 渲染失败：{exc}"}
        else:
            _emit(
                state,
                AgentEvent.DOCUMENT_READY,
                {
                    "file_name": rendered["file_name"],
                    "url": rendered["url"],
                    "template_type": rendered["template_type"],
                },
            )
            updates = {
                "generated_file_path": rendered["file_path"],
                "generated_file_name": rendered["file_name"],
                "generated_file_url": rendered["url"],
                "template_type": rendered["template_type"],
                "application_phase": "generated",
                # 轮次级标记：只有本轮真的渲染了，回答才带下载卡片
                "generated_this_turn": True,
            }

    # 渲染后的真实阶段：状态码与对外的 AgentResult.status 都以它为准
    effective_phase = updates.get("application_phase") or enter_phase
    public_phase = effective_phase
    if public_phase in {"parsed", "company_resolved", "merged", "template_ready"}:
        public_phase = "need_user_input"
    _done(state, node)

    # ---- 二、出口组装：内部阶段机 → 对外三态（主 Agent 只按 status 决策） ----
    # 以后本子图加阶段，只改 schemas/agent_result.py 的 APPLICATION_PHASE_MAP。
    merged = {**state, **updates}
    artifacts = []
    # 只报**本轮**产出的文件：generated_file_* 是会话级的，用它判会把上一轮的文件
    # 当成这一轮的结果带上行（与回答出口那个 bug 同源）
    if merged.get("generated_this_turn") and merged.get("generated_file_url"):
        artifacts.append(
            {
                "type": "file",
                "name": merged.get("generated_file_name") or "",
                "url": merged.get("generated_file_url") or "",
            }
        )
    payload = merged.get("interrupt_payload") or {}
    result = make_agent_result(
        "application_skill",
        APPLICATION_PHASE_MAP.get(effective_phase, "need_user_input"),
        source_label="业务申请书",
        task_type=merged.get("template_type") or public_phase,
        result={
            "application_data": merged.get("application_data") or {},
            "selected_company": merged.get("selected_company") or {},
            "company_candidates": merged.get("company_candidates") or [],
            "missing_fields": merged.get("missing_fields") or [],
            "template_type": merged.get("template_type") or "",
        },
        message=str(payload.get("message") or ""),
        artifacts=artifacts,
        reason=str(merged.get("error") or ""),
    )
    # application_processed 是主图路由的防重复保护：子图出口回 node_supervisor 后，
    # 即使 Supervisor 再次选 application，routing 也会收敛到 node_answer。
    return {
        **updates,
        "application_phase": public_phase,
        "application_processed": True,
        "agent_result": result,
        "agent_results": {**(state.get("agent_results") or {}), "application_skill": result},
    }


def build_application_app(checkpointer=True):
    """编译申请书子图。

    :param checkpointer: 作为 Main Agent 的子图使用时传 True（继承父图检查点）；
                         单独调用（评测 / 脚本 / 测试）时传一个 InMemorySaver 实例。
    """
    builder = StateGraph(ApplicationState)
    builder.add_node("node_parse_application_request", node_parse_application_request)
    builder.add_node("node_resolve_company", node_resolve_company)
    builder.add_node("node_company_select", node_company_select)
    builder.add_node("node_prepare_application", node_prepare_application)
    builder.add_node("node_human_confirmation", node_human_confirmation)
    builder.add_node("node_render_and_finalize", node_render_and_finalize)

    builder.add_edge(START, "node_parse_application_request")
    builder.add_edge("node_parse_application_request", "node_resolve_company")
    builder.add_conditional_edges(
        "node_resolve_company",
        partial(_route_next, source="node_resolve_company"),
        {
            "node_prepare_application": "node_prepare_application",
            "node_company_select": "node_company_select",
            "node_render_and_finalize": "node_render_and_finalize",
        },
    )
    builder.add_conditional_edges(
        "node_company_select",
        partial(_route_next, source="node_company_select"),
        {
            "node_prepare_application": "node_prepare_application",
            "node_resolve_company": "node_resolve_company",
            "node_render_and_finalize": "node_render_and_finalize",
        },
    )
    builder.add_conditional_edges(
        "node_prepare_application",
        partial(_route_next, source="node_prepare_application"),
        {
            "node_human_confirmation": "node_human_confirmation",
            "node_render_and_finalize": "node_render_and_finalize",
        },
    )
    builder.add_conditional_edges(
        "node_human_confirmation",
        partial(_route_next, source="node_human_confirmation"),
        {
            "node_prepare_application": "node_prepare_application",
            "node_render_and_finalize": "node_render_and_finalize",
        },
    )
    builder.add_edge("node_render_and_finalize", END)
    return builder.compile(checkpointer=checkpointer)


application_app = build_application_app(checkpointer=True)


# ------------------------- 用户回复解析 -------------------------


class _UserInput(BaseModel):
    confirm: Optional[bool] = None
    selected_index: Optional[int] = None
    company_name: Optional[str] = None
    unified_social_credit_code: Optional[str] = None
    province: Optional[str] = None
    registered_address: Optional[str] = None
    city: Optional[str] = None
    notes: Optional[str] = None


def _parse_user_input(pending: str, options: list[str], text: str) -> _UserInput:
    """把用户的自由文本解析成结构化意图（模型优先，规则兜底）。"""
    options_text = (
        "\n".join(f"{index}. {item}" for index, item in enumerate(options, start=1)) or "（无）"
    )
    prompt = load_prompt("application_user_input", pending=pending, options=options_text, text=text)
    parsed = json_completion(_UserInput, prompt, tag="application_user_input")
    if parsed is None:
        parsed = _rule_parse_user_input(text)
    if parsed.selected_index and not (1 <= parsed.selected_index <= max(len(options), 1)):
        parsed.selected_index = None
    return parsed


def _rule_parse_user_input(text: str) -> _UserInput:
    """模型不可用时的兜底：只认「第几个」和「确认 / 取消」。"""
    parsed = _UserInput()
    cleaned = (text or "").strip()
    if not cleaned:
        return parsed

    digit = re.search(r"([1-9])", cleaned)
    if digit and (len(cleaned) <= 6 or "第" in cleaned or cleaned.isdigit()):
        parsed.selected_index = int(digit.group(1))
    if any(word in cleaned for word in ("确认", "可以", "同意", "好的", "生成吧", "没问题")):
        parsed.confirm = True
    elif any(word in cleaned for word in ("取消", "不用", "算了", "不要再", "否")):
        parsed.confirm = False
    return parsed


def _as_text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("text", "message", "content", "value"):
            if isinstance(value.get(key), str):
                return value[key]
        return str(value)
    return "" if value is None else str(value)


def _candidate_brief(candidates: list[dict]) -> list[dict]:
    return [
        {
            "company_name": item.get("company_name"),
            "unified_social_credit_code": item.get("unified_social_credit_code"),
            "province": item.get("province"),
            "city": item.get("city"),
            # 厂商不给分数，这里给的是本地名称相似度，仅用于前端排序展示
            "name_similarity": item.get("name_similarity"),
        }
        for item in candidates
    ]


def _guess_province(address: str) -> Optional[str]:
    match = re.match(r"(.{2,8}?(?:省|自治区|北京市|上海市|天津市|重庆市))", address or "")
    return match.group(1) if match else None


def _prune(data: dict) -> dict:
    return {key: value for key, value in data.items() if value not in (None, "", [], {})}
