"""Document Understanding Subgraph（内部是 LangGraph 子图）。

职责边界（重构后，设计见 docs/Supervisor契约改造设计.md）：
    Main Agent      负责"用户要什么"——只传一句 task 目标陈述，**不指定动作**
    Document Agent  负责"文件是什么 + 里面有什么 + 本轮该做什么"
                    （动作 resolved_action 由本子图在同一次理解调用里判定）
    OCR MCP         负责"把图片 / PDF 变成文本与版面"

为什么动作判定放在本子图：动作名（extract_mailing_address / summarize_document /
extract_company_info）是**本子图的内部白名单**。放在主 Agent 那边，主 Agent 就要
跟着本子图一起改；放在这里，加动作只改本文件与 document_service。
代价为零：判定与理解共用同一次模型调用（document_service.understand_document）。

输入是一个**结构化任务**（`document_task = {file_id, action}`），子图不读用户原话、
不猜意图；输出分两层：

    document_facts        文件里有什么（8 个通用字段 + 候选地址），可被申请书子图复用
    document_task_result  这次任务做得怎么样（status / address_decision / preferred_address / data）

编排：一次准备 + 一次理解 + 单一收口（4 个节点，其中 confirm 只在歧义时进入）

    START → node_prepare_document
              ├─(复用已理解)──────► node_understand_document ──┐
              ├─(新附件，OCR 成功)─► node_understand_document ──┤
              └─(出错 / 无附件)────────────────────────────────┤
                                                               ▼
                                  node_confirm_address（仅地址歧义时 interrupt）
                                                               │
                                                               ▼
                                               node_finalize_document → END

为什么把"定位文件"与"OCR"合成一个节点（原 node_load_document + node_run_ocr）：
两者之间只有一条"要不要真的 OCR"的分叉，且都不含 interrupt 与人工确认，
合成一个"拿到正文"的步骤后图少一跳、少一次状态合并。**不含中断点，所以可安全合并**。
反过来，node_confirm_address 必须独立——LangGraph 恢复时会重放整个中断节点。

两个关键约定：

1. **一次理解，全部产出**：分类 / 通用字段 / 地址候选 / 摘要在同一次模型调用里产出
   （document_service.understand_document）。于是任何 action 的执行都只是读这些字段——
   同一份文件换任务复用时不需要重新 OCR，也不需要重新调用模型。
2. **地址优选是确定性规则**，由 document_service.pick_mailing_address 按
   "邮寄地址 > 回函地址 > 通讯地址 > 办公地址 > 注册地址"判断，不交给模型。

一轮完整的数据流（以"询证函 → 提取邮寄地址"为例，括号里是节点写进 State 的键）：

    node_prepare_document     （document_file / document_task / document_reuse；
                               新附件时顺带 OCR：document_text / document_ocr_raw /
                               document_pages / document_confidence）
        ↓
    node_understand_document  （document_type / document_classification / document_fields /
                               candidate_addresses / address_labels / document_summary
                               + address_decision / preferred_address）
        ↓ 仅地址歧义时
    node_confirm_address      （preferred_address / address_decision / interrupt_*）
        ↓
    node_finalize_document    （document_facts / document_task_result / document_analysis /
                               document_processed)

每个节点的入参、出参示例见各节点的 docstring；字段语义集中定义在 app/agent/state.py。

v1 能力白名单（不做"万能文档 Agent"）：
    文档类型：inquiry_letter / business_license / generic_document / unknown
    通用字段：公司名称、统一社会信用代码、地址、法人、联系人、电话、日期、金额
    专项能力：extract_mailing_address / extract_company_info / summarize_document
    超出白名单 → task_result.status = unsupported，由主 Agent 决定怎么回复
"""
import sys
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agent.events import AgentEvent, emit
from app.agent.schemas.agent_result import DOCUMENT_STATUS_MAP, make_agent_result
from app.agent.schemas.document import ACTIONS_BY_DOCUMENT_TYPE
from app.agent.services import document_service, file_store
from app.agent.services.file_store import UploadedFile
from app.agent.services.ocr_providers import OCRUnavailable, run_ocr
from app.conf.agent_config import agent_config
from app.core.logger import logger
from app.utils.task_utils import add_done_task, add_running_task

# 通用字段白名单（v1 只认这些，其余交给专项能力）
_COMMON_FACT_KEYS = (
    "company_name",
    "unified_social_credit_code",
    "address",
    "province",
    "city",
    "legal_person",
    "enterprise_nature",
    "registered_capital",
    "contact_person",
    "contact_phone",
    "document_date",
    "amount",
)


class DocumentState(TypedDict, total=False):
    """Document 子图用到的状态（AgentState 的子集，字段语义见 app/agent/state.py）。

    这里只列本子图会读写的键，目的是让"这个子图能改哪些状态"一目了然；
    键必须已存在于 AgentState，否则父图合并状态时会失败。
    """

    # --- 运行环境（只读） ---
    session_id: str          # = thread_id，节点进度与 SSE 用
    thread_id: str
    is_stream: bool          # 决定是否推 SSE 进度
    attachments: list        # 本轮附件；为空时看能否复用已理解过的文件
    # 主 Agent 给的用户目标陈述（"提取这份询证函中可用于寄送的地址"）。
    # 本子图据此判定动作，不读用户原话（避免变成第二个意图识别器）。
    document_task_text: str
    # 本子图自己判定的动作（本轮，node_understand_document 写）。
    resolved_action: str
    # 显式覆盖通道（测试 / 评测 / 老客户端直接指定动作时用）。
    document_action: str
    # --- 本轮任务与解析过程 ---
    document_task: dict      # {file_id, action}
    document_reuse: bool     # True = 复用已理解结果，只重算本轮 action 的结论
    document_file: dict      # 附件元数据 + 本地路径
    document_ocr_raw: dict   # OCR 原始返回
    document_error: str      # 失败原因，一路带到收口
    document_text: str       # 截断后的正文
    document_pages: list
    document_confidence: float
    # --- 一次理解的全部产出（会话级，跨轮保留） ---
    document_type: str
    document_classification: dict
    document_fields: dict
    candidate_addresses: list
    address_labels: dict
    address_decision: str    # single / ambiguous / none
    preferred_address: str
    document_summary: str
    document_processed: bool
    # --- 输出契约 ---
    document_analysis: dict
    document_facts: dict
    document_task_result: dict
    # 上行汇报（AgentResult）：必须在这里声明，否则本子图写出去的键不会被父图合并。
    agent_result: dict
    agent_results: dict
    # --- HITL ---
    interrupt_reason: str
    interrupt_payload: dict
    missing_info: list


document_state_t = DocumentState


def _task(state: dict, node: str):
    add_running_task(state["session_id"], node, state.get("is_stream", False))


def _done(state: dict, node: str):
    add_done_task(state["session_id"], node, state.get("is_stream", False))


def _current_action(state: dict) -> str:
    """本轮要执行的动作：**本子图自己判定的优先**。

    优先级：
        1. resolved_action      本子图在理解阶段判定的（生产路径）
        2. document_action      显式覆盖：测试 / 评测 / 老客户端直接指定
        3. document_task.action 本子图自己上一轮写的（单独调用本子图时的残留）
        4. "none"

    注意 "none" 要当成"没有判定"继续往下找，不能用 or 链直接返回——
    "none" 是非空字符串，用 or 会挡掉后面的显式覆盖通道（测试与评测都用它）。

    第 2 条是迁移期的兼容通道，**主图 -> 子图不再写它**：
    主 Agent 现在只传 document_task_text（用户目标），动作由本子图判定。
    """
    for key in ("resolved_action", "document_action"):
        value = state.get(key)
        if value and value != "none":
            return value
    task = state.get("document_task") or {}
    return task.get("action") or "none"


def node_prepare_document(state: dict) -> dict:
    """准备文档正文：定位文件（含复用判断），需要时顺带 OCR。

    合并了原来的 node_load_document 与 node_run_ocr——两者之间只有一条"要不要真的
    调 OCR"的分叉，且都不含 interrupt，合成一个"拿到正文"的步骤后图少一跳、
    少一次状态合并。中断点仍然是独立的（node_confirm_address）：LangGraph 恢复时会
    重放整个中断节点，把 OCR 那类有代价的操作并进去会重复执行。

    ---- 入参（只读） ----
    attachments      本轮附件；为空时才会尝试复用
                     [{"file_id": "file_9f3c2a1b", "filename": "询证函.png",
                       "mime_type": "image/png"}]
    document_task_text 主 Agent 给的用户目标 "提取这份询证函中可用于寄送的地址"
    document_analysis  复用判断用；{"document_id": "file_9f3c2a1b", "error": None, ...}

    ---- 出参 A：本轮带了新附件，且 OCR 成功 ----
    {
      "document_file": {"file_id": "file_9f3c2a1b", "filename": "询证函.png",
                        "mime_type": "image/png", "size": 20480,
                        "path": "output/agent_files/file_9f3c2a1b/询证函.png"},
      "document_task": {"file_id": "file_9f3c2a1b", "action": "extract_mailing_address"},
      "document_reuse": False,
      "document_error": "",
      # 换新文件时顺带清空上一份文件的派生结果（_empty_document_derivations）：
      "document_type": "", "document_classification": {}, "document_text": "",
      "document_pages": [], "document_confidence": 0.0, "document_fields": {},
      "candidate_addresses": [], "address_labels": {},
      "address_decision": "", "preferred_address": "", "document_summary": "",
      # OCR 结果（原 node_run_ocr 的产物，现在就写在同一个节点里）：
      "document_ocr_raw": {"document_id": "…", "full_text": "…", "pages": [], "confidence": 0.98},
      "document_text": "询证函\\n致：…", "document_pages": [...], "document_confidence": 0.98,
    }

    ---- 出参 B：没有新附件，但缓存里有理解过的同一份文件 ----
    {
      "document_file": {...},                      # 与 A 相同，从缓存 id 还原
      "document_task": {"file_id": "file_9f3c2a1b", "action": "summarize_document"},
      "document_reuse": True,                      # ← 下游据此跳过 OCR 与模型调用
      "document_error": "",
    }

    ---- 出参 C：失败（没有附件也没有可复用的缓存 / 附件不存在 / OCR 失败） ----
    {"document_error": "no_attachment", "document_processed": True}      # 或
    {"document_error": "attachment_not_found", "document_processed": True,
     "document_task": {"file_id": "", "action": "extract_mailing_address"}}   # 或
    {"document_error": "ocr_unavailable: …" / "ocr_failed: …" / "ocr_empty_text"}
    """
    node = sys._getframe().f_code.co_name
    _task(state, node)
    action = _current_action(state)
    attachments = state.get("attachments") or []

    if attachments:
        first = attachments[0]
        file = file_store.resolve(first.get("file_id", ""))
        if file is None and first.get("path"):
            file = file_store.resolve_path(first["path"])
        if file is None:
            logger.warning(f"[document] 附件不存在：{first}")
            _done(state, node)
            # 附件既不存在，也不可复用
            return {
                "document_error": "attachment_not_found",
                "document_processed": True,
                "document_task": {"file_id": first.get("file_id", ""), "action": action},
            }
        # 换了新文件：清掉上一份文件留下的派生结果，避免新文件继承旧事实，
        # 然后紧接着 OCR —— 原 node_run_ocr 的整段逻辑，见 _run_ocr_and_merge。
        base = {
            "document_file": {**file.to_dict(), "path": file.path},
            "document_task": {"file_id": file.file_id, "action": action},
            "document_reuse": False,
            "document_error": "",
            **_empty_document_derivations(),
        }
        return _run_ocr_and_merge(state, node, file, base)

    # 没有新附件：同一份文件之前已经理解过 → 复用结果，只重算本轮 action 的结论
    cached = state.get("document_analysis") or {}
    cached_id = cached.get("document_id")
    if cached_id and not cached.get("error"):
        file = file_store.resolve(cached_id)
        if file is not None:
            logger.info(f"[document] 复用已理解的文件 {cached_id}，只执行 action={action}")
            _done(state, node)
            return {
                "document_file": {**file.to_dict(), "path": file.path},
                "document_task": {"file_id": cached_id, "action": action},
                "document_reuse": True,
                "document_error": "",
            }

    _done(state, node)
    return {"document_error": "no_attachment", "document_processed": True}


def _empty_document_derivations() -> dict:
    """清空"由文件内容派生出来"的字段（换新文件时用）。"""
    return {
        "document_type": "",
        "document_classification": {},
        "document_text": "",
        "document_pages": [],
        "document_confidence": 0.0,
        "document_fields": {},
        "candidate_addresses": [],
        "address_labels": {},
        "address_decision": "",
        "preferred_address": "",
        "resolved_action": "",
        "document_summary": "",
    }


def _run_ocr_and_merge(state: dict, node: str, file: UploadedFile, base: dict) -> dict:
    """对 file 执行 OCR（外部 OCR MCP，唯一通路），并把结果叠加到 base。

    node_prepare_document 的第二段。单独拆成函数只是为了可读性——它**不是**一个图节点，
    与前半段（定位文件）之间没有分叉，也没有中断点。

    ---- 入参 ----
    file   已定位好的附件（UploadedFile）
    base   前半段已经算好的状态（document_file / document_task / 清空后的派生字段）

    ---- 返回 ----
    成功：base + {document_ocr_raw, document_text, document_pages, document_confidence,
                  document_error: ""}
    失败：base + {document_error: "ocr_empty_text" / "ocr_unavailable: …" / "ocr_failed: …"}
          三种都由路由直接送收口节点（node_finalize_document）
    """
    emit(
        state.get("session_id", ""),
        AgentEvent.DOCUMENT_PROCESSING,
        {"filename": file.filename, "provider": "mcp"},
    )
    try:
        result = run_ocr(file)
    except OCRUnavailable as exc:
        logger.warning(f"[document] OCR 不可用：{exc}")
        _done(state, node)
        return {**base, "document_error": f"ocr_unavailable: {exc}"}
    except Exception as exc:
        logger.exception(f"[document] OCR 异常：{exc}")
        _done(state, node)
        return {**base, "document_error": f"ocr_failed: {exc}"}

    raw = {
        "document_id": result.get("document_id"),
        "full_text": result.get("full_text") or "",
        "pages": result.get("pages") or [],
        "confidence": result.get("confidence") or 0.0,
        "provider": result.get("provider", "mcp"),
    }
    emit(
        state.get("session_id", ""),
        AgentEvent.OCR_COMPLETED,
        {"provider": raw["provider"], "chars": len(raw["full_text"])},
    )

    text = raw["full_text"][: agent_config.document_max_chars]
    _done(state, node)
    if not text.strip():
        return {
            **base,
            "document_ocr_raw": raw,
            "document_error": "ocr_empty_text",
            "document_text": "",
        }
    return {
        **base,
        "document_ocr_raw": raw,
        "document_text": text,
        "document_pages": raw["pages"],
        "document_confidence": raw["confidence"],
        "document_error": "",
    }


def node_understand_document(state: dict) -> dict:
    """一次理解：分类 + 通用字段 + 地址候选 + 摘要，随后按 action 得出本轮结论。

    复用（document_reuse=True）时理解结果已经在 State 里，这里不调模型，只重算
    本轮 action 的结论——这就是"同一份文件换任务"不重复 OCR、不重复调模型的原因。

    ---- 入参 A：新文件（会调一次模型） ----
    document_text    "询证函\\n致：江苏某某科技有限公司\\n截至 2026-08-31，贵公司欠本公司
                      货款余额 1,234,567.89 元。\\n回函地址：江苏省南京市鼓楼区中山路1号
                      苏银大厦12层\\n联系人：王会计    电话：025-88886666"
    document_task_text  "提取这份询证函中可用于寄送的地址"（动作由本节点判定，不由主 Agent 给）
    document_reuse   False

    ---- 入参 B：复用（不调模型，这些键已在 State 里） ----
    document_reuse   True
    document_type    "inquiry_letter"
    document_fields  {"registered_address": "江苏省南京市鼓楼区中山路1号苏银大厦12层", …}
    candidate_addresses / address_labels / document_summary  同上一次理解的产物

    ---- 出参 A：理解结果（只有新文件才会出现这些键） ----
    {
      "document_type": "inquiry_letter",
      "document_classification": {"document_type": "inquiry_letter",
                                  "confidence": 0.95, "reason": "出现回函地址与货款余额"},
      "document_fields": {                          # ExtractedDocumentFields
        "registered_address": "江苏省南京市鼓楼区中山路1号苏银大厦12层",
        "province": "江苏省",
        "contact_person": "王会计",
        "contact_phone": "025-88886666",
        "document_date": "2026-08-31",
        "balance": "1,234,567.89",
      },
      "candidate_addresses": ["江苏省南京市鼓楼区中山路1号苏银大厦12层"],
      "address_labels": {"江苏省南京市鼓楼区中山路1号苏银大厦12层": "回函地址"},
      "document_summary": "这是一份询证函，用于向对方确认往来款项余额…",
    }

    ---- 出参 B：本轮 action 的结论（两种入口都会追加） ----
    {"address_decision": "single",                    # 唯一 / 高优先级领先
     "preferred_address": "江苏省南京市鼓楼区中山路1号苏银大厦12层"}
    {"address_decision": "ambiguous", "preferred_address": ""}   # 同优先级多个 → 下游 interrupt
    {"address_decision": "none", "preferred_address": ""}        # 没抽到地址
    {}                                               # 不是"要地址"、非询证函、或已确认过时
    """
    node = sys._getframe().f_code.co_name
    _task(state, node)

    # 主agent进行意图识别之后得到的目标
    task_text = state.get("document_task_text") or ""

    if state.get("document_reuse"):
        # 复用：理解结果已在 State 里，不再重跑"分类+字段+地址+摘要"那一次大调用；
        # 本轮动作单独判定（只吃 task + 文档类型，不吃正文，代价很小）。
        # 这就是"同一份文件换任务不必重新 OCR / 重新调模型"的落点。
        resolved = document_service.decide_action_from_task(
            task_text, state.get("document_type") or ""
        )
        if resolved == "none":
            # task 没给出可判定的目标时，沿用显式覆盖通道（测试 / 评测 / 老客户端）
            resolved = state.get("document_action") or "none"
        updates: dict = {"resolved_action": resolved}
        logger.info(
            f"[document] 复用理解结果，按 task 判定动作：{resolved} task={task_text!r}"
        )
    # 不需要复用，新文件理解
    else:
        understanding = document_service.understand_document(
            state.get("document_text") or "", task=task_text
        )
        updates = {
            "document_type": understanding.document_type,
            "document_classification": {
                "document_type": understanding.document_type,
                "confidence": understanding.confidence,
                "reason": understanding.reason,
            },
            "document_fields": understanding.fields.model_dump(exclude_none=True),
            "candidate_addresses": [item.value for item in understanding.addresses],
            "address_labels": {item.value: item.label for item in understanding.addresses},
            "document_summary": understanding.summary,
            "resolved_action": understanding.resolved_action,
        }
        # 观测用：把"模型判定"与"纯规则判定"都记下来，便于对比两条路径的差异
        logger.info(
            f"[document] 动作判定：模型={understanding.resolved_action} "
            f"规则={document_service.infer_action_from_task(task_text)} task={task_text!r}"
        )

    merged = {**state, **updates}
    updates.update(_resolve_address(merged))
    _done(state, node)
    logger.info(
        f"[document] 理解完成：type={merged.get('document_type') or 'unknown'} "
        f"action={_current_action(merged)} reuse={bool(state.get('document_reuse'))} "
        f"地址候选={len(merged.get('candidate_addresses') or [])}"
    )
    return updates


def _resolve_address(state: dict) -> dict:
    """地址优选：只在"要地址 + 询证函 + 还没有结论"时执行。

    白名单：非询证函不做地址提取，交给 node_finalize_document 判 unsupported；
    上一轮已经确认过地址（含 HITL 的用户选择）时直接沿用，不再问第二遍。
    """
    if _current_action(state) != "extract_mailing_address":
        return {}
    if state.get("document_type") != "inquiry_letter":
        return {}
    if state.get("preferred_address"):
        return {"address_decision": "single", "preferred_address": state["preferred_address"]}

    candidates = state.get("candidate_addresses") or []
    labels = state.get("address_labels") or {}
    preferred = document_service.pick_mailing_address(candidates, labels)
    if preferred:
        decision = "single"
    elif candidates:
        decision = "ambiguous"
    else:
        decision = "none"
    logger.info(
        f"[document] 地址优选：decision={decision} preferred={preferred or '—'} "
        f"候选={len(candidates)}"
    )
    return {"address_decision": decision, "preferred_address": preferred or ""}


def node_confirm_address(state: dict) -> dict:
    """只负责暂停：地址歧义时让用户选（与公司多候选同一套 interrupt 机制）。

    领域判断（"有多个同样优先级的地址"）在 _resolve_address 里完成；
    这里只做"把歧义抛给用户 + 解析用户的回复"。

    ---- 入参 ----
    candidate_addresses  ["江苏省南京市鼓楼区中山路1号", "江苏省苏州市工业园区星湖街2号"]
    address_labels       {"江苏省南京市鼓楼区中山路1号": "邮寄地址",
                          "江苏省苏州市工业园区星湖街2号": "邮寄地址"}

    ---- 暂停时推给用户的 payload（interrupt 的值，也是 interrupt_payload） ----
    {
      "reason": "multiple_address_candidates",
      "candidates": [{"value": "江苏省南京市鼓楼区中山路1号", "label": "邮寄地址"},
                     {"value": "江苏省苏州市工业园区星湖街2号", "label": "邮寄地址"}],
    }
    用户回复（Command(resume=...)）："2" 或 "第2个" 或直接贴完整地址

    ---- 出参 A：解析出选择（支持序号 / "第N个" / 直接贴地址） ----
    {"preferred_address": "江苏省苏州市工业园区星湖街2号",
     "address_decision": "single",
     "interrupt_reason": "", "interrupt_payload": {}, "missing_info": []}

    ---- 出参 B：没解析出来（本轮不结束流程，交给 finalize 判 ambiguous 再反问） ----
    {"address_decision": "ambiguous", "preferred_address": "",
     "interrupt_reason": "invalid_address_selection",
     "interrupt_payload": {...同上...},
     "missing_info": ["请回复候选地址的序号，或直接给出完整地址"]}
    """
    node = sys._getframe().f_code.co_name
    _task(state, node)
    candidates = state.get("candidate_addresses") or []
    labels = state.get("address_labels") or {}
    payload = {
        "reason": "multiple_address_candidates",
        "candidates": [
            {"value": value, "label": labels.get(value, "地址")} for value in candidates
        ],
    }
    value = interrupt(payload)
    chosen = _pick_address_from_reply(candidates, _as_text(value))
    _done(state, node)

    if chosen:
        return {
            "preferred_address": chosen,
            "address_decision": "single",
            "interrupt_reason": "",
            "interrupt_payload": {},
            "missing_info": [],
        }
    return {
        "address_decision": "ambiguous",
        "preferred_address": "",
        "interrupt_reason": "invalid_address_selection",
        "interrupt_payload": payload,
        "missing_info": ["请回复候选地址的序号，或直接给出完整地址"],
    }


def _pick_address_from_reply(candidates: list[str], text: str) -> str | None:
    """从用户回复里解析地址选择：支持序号，也支持直接贴地址（确定性，不调模型）。"""
    import re

    cleaned = (text or "").strip()
    if not cleaned or not candidates:
        return None
    for index, value in enumerate(candidates, start=1):
        if cleaned == str(index):
            return value
    match = re.search(r"(?:第\s*)?([1-9]\d?)\s*(?:个|条|号)", cleaned)
    if match:
        index = int(match.group(1))
        if 1 <= index <= len(candidates):
            return candidates[index - 1]
    normalized = document_service._norm_address(cleaned)
    for value in candidates:
        if normalized and normalized in document_service._norm_address(value):
            return value
    return None


def node_finalize_document(state: dict) -> dict:
    """组装 Document Facts（文件里有什么）+ Task Result（这次任务做得怎么样）。

    所有分支（成功 / 复用 / 出错 / 无附件）最后都汇到这里，所以这是唯一写
    document_analysis 的地方，也是主 Agent 能看到的出口。

    ---- 入参（读前面各节点累积的结果） ----
    document_file        {"file_id": "file_9f3c2a1b", "filename": "询证函.png", "path": "…"}
    document_type        "inquiry_letter"
    document_fields      {"registered_address": "…", "contact_person": "王会计", …}
    candidate_addresses  ["江苏省南京市鼓楼区中山路1号苏银大厦12层"]
    address_labels       {"江苏省南京市鼓楼区中山路1号苏银大厦12层": "回函地址"}
    address_decision     "single"
    preferred_address    "江苏省南京市鼓楼区中山路1号苏银大厦12层"
    resolved_action      "extract_mailing_address"（本子图自己判定的本轮动作）
    document_error       ""（出错时是 no_attachment / ocr_failed: … 等）

    ---- 出参 ----
    {
      "document_facts": {                            # 事实层：文件里有什么（可被申请书子图复用）
        "document_type": "inquiry_letter",
        "address": "江苏省南京市鼓楼区中山路1号苏银大厦12层",
        "amount": "1,234,567.89",
        "company_name": None,
        "unified_social_credit_code": None,
        "province": "江苏省",
        "city": None,
        "legal_person": None,
        "contact_person": "王会计",
        "contact_phone": "025-88886666",
        "document_date": "2026-08-31",
        "candidate_addresses": [{"value": "江苏省南京市鼓楼区中山路1号苏银大厦12层",
                                 "label": "回函地址", "priority": 20}],
        "raw_ref": "output/agent_files/file_9f3c2a1b/询证函.png",
      },
      "document_task_result": {                      # 任务层：这次任务做得怎么样
        "action": "extract_mailing_address",
        "status": "success",                         # success / ambiguous / not_found /
                                                     # unsupported / failed / none
        "address_decision": "single",
        "preferred_address": "江苏省南京市鼓楼区中山路1号苏银大厦12层",
        "data": {"address": "江苏省南京市鼓楼区中山路1号苏银大厦12层"},
        "reason": None,                              # 失败/不支持时写原因
      },
      "document_analysis": {                         # 上面两者的汇总 + 元信息（兼容层）
        "document_id": "file_9f3c2a1b",
        "document_type": "inquiry_letter",
        "confidence": 0.95,
        "ocr_text": "询证函\\n…",
        "structured_fields": {...},                  # = document_fields
        "candidate_addresses": [...],
        "address_labels": {...},
        "available_actions": ["extract_mailing_address", "summarize_document",
                              "extract_company_info"],
        "summary": "…",
        "ocr_confidence": 0.98,
        "filename": "询证函.png",
        "error": None,
        "facts": {...},                              # = document_facts
        "task_result": {...},                        # = document_task_result
      },
      "document_processed": True,                    # 路由与主图据此判断"本轮已理解完"
    }
    """
    node = sys._getframe().f_code.co_name
    _task(state, node)

    file_meta = state.get("document_file") or {}
    document_type = state.get("document_type") or "unknown"
    error = state.get("document_error") or None
    action = _current_action(state)

    facts = _build_facts(state, document_type, file_meta)
    task_result = _build_task_result(state, action, document_type, error, facts)

    analysis = {
        "document_id": file_meta.get("file_id", ""),
        "document_type": document_type,
        "confidence": (state.get("document_classification") or {}).get("confidence", 0.0),
        "ocr_text": state.get("document_text") or "",
        "structured_fields": state.get("document_fields") or {},
        "candidate_addresses": state.get("candidate_addresses") or [],
        "address_labels": state.get("address_labels") or {},
        "available_actions": ACTIONS_BY_DOCUMENT_TYPE.get(
            document_type, ACTIONS_BY_DOCUMENT_TYPE["unknown"]
        ),
        "summary": state.get("document_summary") or "",
        "ocr_confidence": state.get("document_confidence") or 0.0,
        "filename": file_meta.get("filename"),
        "error": error,
        # 新契约：事实层 + 任务结果（主 Agent 只消费这两块）
        "facts": facts,
        "task_result": task_result,
    }
    _done(state, node)
    logger.info(
        f"[document] 完成理解：type={document_type} action={action} "
        f"status={task_result['status']}"
    )

    # 上行汇报（AgentResult）：主 Agent 只按 status 决策，不解析 result 内部字段。
    # 内部状态（ambiguous / not_found / unsupported / none）在这里一次性翻译成对外三态，
    # 以后本子图加状态只改 DOCUMENT_STATUS_MAP，主 Agent 不受影响。
    result = make_agent_result(
        "document_skill",
        DOCUMENT_STATUS_MAP.get(task_result["status"], "need_user_input"),
        source_label="文档理解",
        task_type=action if action not in ("", "none") else document_type,
        result={
            **(task_result.get("data") or {}),
            # 带上事实快照，使结果自包含（下游不必再去翻 document_facts）
            "facts": facts,
        },
        message=str(task_result.get("reason") or ""),
        reason=str(error or task_result.get("reason") or ""),
    )
    return {
        "document_analysis": analysis,
        "document_facts": facts,
        "document_task_result": task_result,
        "agent_result": result,
        "agent_results": {**(state.get("agent_results") or {}), "document_skill": result},
        "document_processed": True,
    }


def _build_facts(state: dict, document_type: str, file_meta: dict) -> dict:
    """文件里有什么（可复用）：通用字段 + 候选地址 + 原文引用。"""
    fields = state.get("document_fields") or {}
    candidates = state.get("candidate_addresses") or []
    labels = state.get("address_labels") or {}
    facts = {"document_type": document_type}
    # 字段名映射：模型输出用 registered_address / balance，事实层统一叫 address / amount
    facts["address"] = fields.get("registered_address")
    facts["amount"] = fields.get("balance")
    for key in _COMMON_FACT_KEYS:
        if key in {"address", "amount"}:
            continue
        facts[key] = fields.get(key)
    facts["candidate_addresses"] = [
        {
            "value": value,
            "label": labels.get(value, "地址"),
            "priority": document_service.ADDRESS_LABEL_PRIORITY.get(labels.get(value, "地址"), 0),
        }
        for value in candidates
    ]
    facts["raw_ref"] = file_meta.get("path") or ""
    return facts


def _build_task_result(
    state: dict,
    action: str,
    document_type: str,
    error: str | None,
    facts: dict,
) -> dict:
    """这次任务做得怎么样：成功 / 歧义 / 没找到 / 不支持 / 失败 / 没有任务。"""
    result = {
        "action": action,
        "status": "none",
        "address_decision": None,
        "preferred_address": None,
        "data": {},
        "reason": None,
    }

    if error:
        result.update(status="failed", reason=error)
        return result

    if action in ("", "none", None):
        result.update(status="none", reason="用户只上传了文件，没有说明要做什么")
        return result

    if action == "extract_mailing_address":
        candidates = facts.get("candidate_addresses") or []
        decision = state.get("address_decision") or (
            "single" if state.get("preferred_address") else ("ambiguous" if candidates else "none")
        )
        result["address_decision"] = decision
        if document_type != "inquiry_letter":
            result.update(
                status="unsupported",
                reason=f"文档类型 {document_type} 不支持邮寄地址提取",
            )
            return result
        preferred = state.get("preferred_address") or ""
        if preferred:
            result.update(
                status="success",
                preferred_address=preferred,
                data={"address": preferred},
            )
            return result
        if candidates:
            result.update(status="ambiguous", data={"candidates": candidates})
            return result
        result.update(status="not_found", reason="未在文档中识别到地址")
        return result

    if action == "summarize_document":
        summary = state.get("document_summary") or ""
        if summary:
            result.update(status="success", data={"summary": summary})
        else:
            result.update(status="not_found", reason="未能生成摘要")
        return result

    if action == "extract_company_info":
        keys = (
            "company_name",
            "unified_social_credit_code",
            "address",
            "province",
            "city",
            "legal_person",
        )
        data = {key: facts.get(key) for key in keys if facts.get(key)}
        if data:
            result.update(status="success", data=data)
        else:
            result.update(status="not_found", reason="未识别到企业信息")
        return result

    result.update(status="unsupported", reason=f"不支持的文档任务：{action}")
    return result


def _as_text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("text", "message", "content", "value", "address"):
            if isinstance(value.get(key), str):
                return value[key]
        return str(value)
    return "" if value is None else str(value)


def _route_after_prepare(state: dict) -> str:
    """node_prepare_document 之后的分叉。

    document_error 非空 → "node_finalize_document"
        （no_attachment / attachment_not_found / ocr_unavailable / ocr_failed / ocr_empty_text）
    否则（复用命中，或新附件 OCR 成功）→ "node_understand_document"

    合并前这里是两个路由函数（_route_after_load / _route_after_ocr），因为中间横着一个
    OCR 节点；现在准备正文一步做完，只剩"成没成"一条分叉。
    """
    return "node_finalize_document" if state.get("document_error") else "node_understand_document"


def _route_after_understand(state: dict) -> str:
    """地址歧义才交给 HITL，其余情况直接收口。

    address_decision == "ambiguous" → "node_confirm_address"（interrupt，等用户选）
    其余（single / none / 非地址任务）→ "node_finalize_document"
    """
    if state.get("address_decision") == "ambiguous":
        return "node_confirm_address"
    return "node_finalize_document"


def build_document_app(checkpointer=True):
    """编译 Document 子图。

    :param checkpointer: 作为 Main Agent 的子图使用时传 True（继承父图检查点）；
                         单独调用（评测 / 脚本）时传一个 InMemorySaver 实例。
    """
    builder = StateGraph(DocumentState)
    builder.add_node("node_prepare_document", node_prepare_document)
    builder.add_node("node_understand_document", node_understand_document)
    builder.add_node("node_confirm_address", node_confirm_address)
    builder.add_node("node_finalize_document", node_finalize_document)

    builder.add_edge(START, "node_prepare_document")
    builder.add_conditional_edges(
        "node_prepare_document",
        _route_after_prepare,
        {
            "node_understand_document": "node_understand_document",
            "node_finalize_document": "node_finalize_document",
        },
    )
    builder.add_conditional_edges(
        "node_understand_document",
        _route_after_understand,
        {
            "node_confirm_address": "node_confirm_address",
            "node_finalize_document": "node_finalize_document",
        },
    )
    builder.add_edge("node_confirm_address", "node_finalize_document")
    builder.add_edge("node_finalize_document", END)
    return builder.compile(checkpointer=checkpointer)


document_app = build_document_app(checkpointer=True)
