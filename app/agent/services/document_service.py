"""Document Understanding Agent 的语义层（分类 / 字段 / 地址 / 摘要 / 本轮动作）。

子图入口只调用一个函数：
    understand_document 一次完成 分类 + 通用字段 + 地址候选 + 摘要 + 动作判定

下面这些是它的组成部分，也保留单独调用（评测的规则基线、单元级调试用）：
    classify_document    文档分类
    extract_fields       通用字段（v1 白名单 8 个 + 省市）
    extract_addresses    地址候选（含标签，按优先级排序）
    pick_mailing_address 地址优选（唯一 / 高优先级领先 → 给出；否则交给 HITL）
    generate_summary     摘要
    infer_action_from_task  把主 Agent 给的 task 文本映射成本 Agent 的动作（纯规则）

不做事：调用 OCR（在 ocr_providers）、全局路由（在 Main Agent）、
组装 Document Facts / Task Result（在子图的收口节点）。

【职责边界】"这份文件要抽地址还是抽企业信息"是**本 Agent 的判断**，不再由主 Agent 指定；
主 Agent 只传一句用户目标（task）。见 docs/Supervisor契约改造设计.md。
"""
import re
from typing import Optional

from pydantic import BaseModel, Field

from app.agent.schemas.document import (
    AddressCandidate,
    DocumentClassification,
    DocumentTaskAction,
    DocumentUnderstanding,
    DocumentType,
    ExtractedDocumentFields,
)
from app.agent.llm import json_completion, text_completion
from app.conf.agent_config import agent_config
from app.core.load_prompt import load_prompt
from app.core.logger import logger

# 地址标签优先级（规范第 20 节：邮寄地址 > 回函地址 > 通讯地址）
ADDRESS_LABEL_PRIORITY = {
    "邮寄地址": 30,
    "回函地址": 20,
    "通讯地址": 10,
    "通信地址": 10,
    "办公地址": 5,
    "注册地址": 3,
    # 营业执照上的"住所"就是注册地址
    "住所": 3,
    "地址": 1,
}
_ADDRESS_LABEL_RE = re.compile(
    r"(邮寄地址|回函地址|通讯地址|通信地址|办公地址|注册地址|注册地|住所|地址)"
)
_ADDRESS_HINT_RE = re.compile(r"(省|市|区|县|镇|乡|街道|路|号|大厦|广场|楼|园区|大道)")

# 规则兜底用的关键词（大模型不可用时的降级路径）
_INQUIRY_KEYWORDS = ("询证函", "函证", "回函", "往来款项", "余额")
_LICENSE_KEYWORDS = ("营业执照", "统一社会信用代码", "注册资本", "经营范围", "法定代表人")

# 由 task 文本推断动作的关键词（纯规则，用于两条路径：
#   1) 同一份文件复用理解结果时，不调模型也要能得出本轮动作；
#   2) 模型不可用时的整体降级。
# 注意它是"文本 → 本 Agent 动作"的映射，只存在于 Document Agent 内部，
# 与主 Agent 无关（主 Agent 从头到尾不知道这些动作名）。
_ADDRESS_TASK_HINTS = ("地址", "寄送", "寄到", "邮寄", "回函", "寄给", "收件")
_SUMMARY_TASK_HINTS = ("总结", "摘要", "概括", "讲了什么", "内容是什么", "要点")
_COMPANY_TASK_HINTS = ("企业信息", "公司信息", "工商", "信用代码", "营业执照信息", "法定代表人")


class _AddressPayload(BaseModel):
    addresses: list[AddressCandidate] = Field(default_factory=list)


class _ActionPayload(BaseModel):
    """本轮动作判定的模型返回（复用路径用，轻量：不吃文档正文）。"""

    action: DocumentTaskAction = "none"
    reason: str = ""


def infer_action_from_task(task: str) -> str:
    """把主 Agent 给的 task 文本映射成本 Agent 的动作（纯规则，不调模型）。

    用途见上方常量注释。判不出来返回 "none"——那会让主 Agent 反问用户，
    而不是替用户决定要做什么（"上传文件 ≠ 明确业务意图"）。
    """
    text = (task or "").strip()
    if not text:
        return "none"
    if any(hint in text for hint in _ADDRESS_TASK_HINTS):
        return "extract_mailing_address"
    if any(hint in text for hint in _SUMMARY_TASK_HINTS):
        return "summarize_document"
    if any(hint in text for hint in _COMPANY_TASK_HINTS):
        return "extract_company_info"
    return "none"


def decide_action_from_task(task: str, document_type: str = "") -> str:
    """把用户目标映射成本轮动作（模型优先，失败退回关键词规则）。

    什么时候用：同一份文件换问题时（document_reuse），理解结果已经缓存，
    只需要重新判定"这轮做什么"。这条路**不喂文档正文**，只给目标 + 文档类型，
    所以是一次很小的调用；模型不可用时退回 infer_action_from_task。

    为什么要模型：纯规则的关键词表覆盖不住口语说法——"这份文件的法人是谁"
    命中不了"法定代表人"，会返回 none 让主 Agent 反问用户，而同一句话
    在上传当轮（走 understand_document）是能判出 extract_company_info 的。
    """
    text = (task or "").strip()
    if not text:
        return "none"

    prompt = load_prompt(
        "document_action", task=text, document_type=document_type or "unknown"
    )
    payload = json_completion(_ActionPayload, prompt, tag="document_action")
    if payload is not None and payload.action and payload.action != "none":
        return payload.action

    # 模型不可用 / 判成 none：用规则补一次（与 _normalize_understanding 同一思路）
    inferred = infer_action_from_task(text)
    if inferred != "none":
        logger.info(f"[document] 动作判定：模型={getattr(payload, 'action', None)} "
                    f"规则={inferred}，采用规则")
    return inferred


def classify_document(text: str) -> DocumentClassification:
    """文档分类：大模型优先，失败时用关键词规则兜底。"""
    prompt = load_prompt("document_classify", text=_truncate(text, 6000))
    decision = json_completion(DocumentClassification, prompt, tag="document_classify")
    if decision is not None:
        return decision
    return _rule_based_classification(text)


def _rule_based_classification(text: str) -> DocumentClassification:
    head = text[:1500]
    if any(keyword in head for keyword in _INQUIRY_KEYWORDS):
        return DocumentClassification(
            document_type="inquiry_letter", confidence=0.6, reason="关键词规则：命中询证函特征"
        )
    if any(keyword in head for keyword in _LICENSE_KEYWORDS):
        return DocumentClassification(
            document_type="business_license", confidence=0.6, reason="关键词规则：命中营业执照特征"
        )
    if text.strip():
        return DocumentClassification(
            document_type="generic_document", confidence=0.4, reason="关键词规则：其它可读文档"
        )
    return DocumentClassification(document_type="unknown", confidence=0.0, reason="正文为空")


def extract_fields(text: str, document_type: DocumentType) -> ExtractedDocumentFields:
    """字段抽取：大模型优先，失败时退化为正则兜底。"""
    prompt = load_prompt(
        "document_extract",
        document_type=document_type,
        text=_truncate(text, 8000),
    )
    fields = json_completion(ExtractedDocumentFields, prompt, tag="document_extract")
    if fields is None:
        fields = _regex_extract_fields(text)

    # 信用代码只认 18 位合法编码，模型编的要丢掉（规范第 4.6 节）
    if fields.unified_social_credit_code:
        code = re.sub(r"\s", "", fields.unified_social_credit_code).upper()
        fields.unified_social_credit_code = code if _CREDIT_RE.match(code) else None
    return fields


_CREDIT_RE = re.compile(r"^[0-9A-HJ-NPQRTUWXY]{18}$")


def _regex_extract_fields(text: str) -> ExtractedDocumentFields:
    fields = ExtractedDocumentFields()
    patterns = {
        "unified_social_credit_code": r"统一社会信用代码[：:\s]*([0-9A-HJ-NPQRTUWXY]{18})",
        "legal_person": r"法定代表人[：:\s]*([^\s，,。；;]{2,10})",
        "company_name": r"(?:名称|企业名称|公司名称)[：:\s]*([^\s，,。；;]{4,40})",
        "registered_address": r"(?:注册地址|住所|地址)[：:\s]*([^\n]{6,60})",
        # v1 通用字段白名单里的联系人 / 电话 / 金额也要能靠规则兜底（模型不可用时）
        "contact_person": r"联系人[：:\s]*([^\s，,。；;]{2,10})",
        "contact_phone": r"(?:联系电话|电话|Tel)[：:\s]*([0-9\-()（） ]{6,20})",
        "balance": r"(?:余额|金额|合计|共计)[：:\s]*([0-9][0-9,，.]*)",
        "document_date": r"(20\d{2})[-年/.](\d{1,2})[-月/.](\d{1,2})",
        # 企业性质：营业执照上常写"类型：有限责任公司"。
        # 值里要排除 "：" 与 "/"，否则"证件类型：/ 证件号码"会被回溯捕获成 "：/"
        "enterprise_nature": (
            r"(?:企业性质|企业类型|公司性质|公司类型|类型)[：:\s]+([^\s，,。；;:/]{2,20})"
        ),
        "registered_capital": r"注册资本[：:\s]*([^\n，,。；;]{1,40})",
    }
    for field, pattern in patterns.items():
        match = re.search(pattern, text)
        if not match:
            continue
        if field == "document_date":
            year, month, day = match.groups()
            setattr(fields, field, f"{year}-{int(month):02d}-{int(day):02d}")
        elif field == "enterprise_nature" and _looks_like_non_nature(match.group(1)):
            continue
        else:
            setattr(fields, field, match.group(1).strip())
    if fields.registered_address:
        fields.province = _guess_province(fields.registered_address)
    return fields


def _looks_like_non_nature(value: str) -> bool:
    """把"证件类型：/""证件类型：身份证"这类误命中排除掉。"""
    text = (value or "").strip()
    return (not text) or text in {"/", "无"} or "证件" in text or "号码" in text


def extract_addresses(text: str) -> list[dict]:
    """地址候选抽取 + 按业务标签优先级排序（单独调用时用；子图走 understand_document）。"""
    candidates: list[dict] = list(_regex_addresses(text))

    prompt = load_prompt("document_address", text=_truncate(text, 8000))
    llm_result = json_completion(_AddressPayload, prompt, tag="document_address")
    if llm_result:
        candidates.extend(_address_items(llm_result.addresses))

    return _merge_addresses(candidates)


def _address_items(items) -> list[dict]:
    """把模型输出的地址候选规范化成内部 dict，丢掉空值。"""
    results: list[dict] = []
    for item in items or []:
        value = (getattr(item, "value", "") or "").strip().strip("，,。;；")
        if value:
            results.append(
                {
                    "value": value,
                    "label": getattr(item, "label", "") or "地址",
                    "confidence": getattr(item, "confidence", 0.0),
                }
            )
    return results


def _merge_addresses(candidates: list[dict]) -> list[dict]:
    """同一地址去重（保留标签优先级更高的那条），再按优先级 / 置信度排序。"""
    merged: dict[str, dict] = {}
    for item in candidates:
        key = _norm_address(item["value"])
        if not key:
            continue
        existing = merged.get(key)
        if existing is None or ADDRESS_LABEL_PRIORITY.get(
            item["label"], 0
        ) > ADDRESS_LABEL_PRIORITY.get(existing["label"], 0):
            merged[key] = item

    return sorted(
        merged.values(),
        key=lambda item: (
            -ADDRESS_LABEL_PRIORITY.get(item["label"], 0),
            -float(item.get("confidence") or 0.0),
        ),
    )


def _regex_addresses(text: str) -> list[dict]:
    """逐行按标签抠地址；标签后内容太短时顺延到下一行。"""
    lines = [line.strip() for line in re.split(r"[\n\r]+", text) if line.strip()]
    results = []
    for index, line in enumerate(lines):
        match = _ADDRESS_LABEL_RE.search(line)
        if not match:
            continue
        label = match.group(1)
        value = line[match.end():].lstrip("：: 　|·-")
        if len(value) < 6 and index + 1 < len(lines):
            value = f"{value}{lines[index + 1]}".strip()
        value = value.strip("：: ，,。;；|")
        if len(value) >= 6 and _ADDRESS_HINT_RE.search(value):
            results.append({"value": value, "label": label, "confidence": 0.9})
    return results


def generate_summary(text: str, document_type: DocumentType) -> str:
    """文档摘要：大模型优先，失败时给前 200 字。"""
    prompt = load_prompt(
        "document_summary",
        document_type=document_type,
        text=_truncate(text, 8000),
    )
    summary = text_completion(prompt, tag="document_summary")
    if summary:
        return summary
    return text[:200]


def understand_document(text: str, task: str = "") -> DocumentUnderstanding:
    """一次调用完成 分类 + 通用字段 + 地址候选 + 摘要 + 本轮动作。

    新增的 `task` 参数是本 Agent 的输入契约（主 Agent 给的一句用户目标）。
    它顺带解决了原设计的一个矛盾：以前为了"不让子 Agent 变成第二个意图识别器"，
    动作由主 Agent 指定；现在主 Agent 只给目标陈述，动作由本 Agent 在同一次调用里判定
    （resolved_action），**调用次数不变，决策权回到本 Agent**。

    兜底是**整体**的：模型不可用时一次退化成关键词分类 + 正则字段 + 正则地址 +
    前 200 字摘要，不会再出现"分类成功、字段失败"这种半截状态。
    """
    prompt = load_prompt(
        "document_understanding",
        text=_truncate(text, 8000), # 正文超长，截断
        task=(task or "").strip() or "（用户没有说明要做什么）",
    )
    payload = json_completion(DocumentUnderstanding, prompt, tag="document_understanding")
    if payload is None:
        logger.warning("[document] 结构化理解失败，退化为规则/正则兜底")
        payload = _rule_understand(text, task)
    return _normalize_understanding(payload, text, task)

def _rule_understand(text: str, task: str = "") -> DocumentUnderstanding:
    """规则兜底：关键词分类 + 正则字段 + 正则地址 + 前 200 字摘要 + 规则动作。"""
    classification = _rule_based_classification(text)
    return DocumentUnderstanding(
        document_type=classification.document_type,
        confidence=classification.confidence,
        reason=classification.reason,
        fields=_regex_extract_fields(text),
        addresses=[AddressCandidate(**item) for item in _regex_addresses(text)],
        summary=text[:200] if text.strip() else "",
        resolved_action=infer_action_from_task(task),
    )


def _normalize_understanding(
    payload: DocumentUnderstanding, text: str, task: str = ""
) -> DocumentUnderstanding:
    """校验模型输出：信用代码必须合法、省份缺失可补、地址与正则结果合并去重。

    动作侧同样有一道兜底：模型给了 none 但 task 明显指向某个动作时，用规则补上
    （与"正则地址补模型漏掉的地址"是同一个思路）。规则判不出来才保持 none，
    交给主 Agent 反问用户。
    """
    fields = payload.fields or ExtractedDocumentFields()
    # 信用代码只认 18 位合法编码，模型编的要丢掉（规范第 4.6 节）
    if fields.unified_social_credit_code:
        code = re.sub(r"\s", "", fields.unified_social_credit_code).upper()
        fields.unified_social_credit_code = code if _CREDIT_RE.match(code) else None
    if fields.registered_address and not fields.province:
        fields.province = _guess_province(fields.registered_address)

    # 模型抽的地址与正则抽的地址合并：正则能补上模型漏掉的带标签地址
    merged = _merge_addresses(_address_items(payload.addresses) + _regex_addresses(text))

    resolved_action = payload.resolved_action or "none"
    if resolved_action == "none":
        inferred = infer_action_from_task(task)
        if inferred != "none":
            logger.info(
                f"[document] 模型未给出动作，按 task 文本用规则补上：{inferred}"
            )
            resolved_action = inferred

    return DocumentUnderstanding(
        document_type=payload.document_type,
        confidence=payload.confidence,
        reason=payload.reason,
        fields=fields,
        addresses=[AddressCandidate(**item) for item in merged],
        summary=(payload.summary or "").strip() or (text[:200] if text.strip() else ""),
        resolved_action=resolved_action,
    )


def pick_mailing_address(candidates: list[str], labels: Optional[dict] = None) -> Optional[str]:
    """地址优选（规范第 20 节：邮寄地址 > 回函地址 > 通讯地址 > …）。

    能唯一确定的情况：
      · 只有一个候选；
      · 最高优先级的标签严格高于第二个候选（例如"邮寄地址"压过"注册地址"）。
    其余（同优先级多个、或都是低优先级地址）返回 None，交给 HITL 让用户选。
    """
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    labels = labels or {}
    top_priority = ADDRESS_LABEL_PRIORITY.get(labels.get(candidates[0]), 0)
    second_priority = ADDRESS_LABEL_PRIORITY.get(labels.get(candidates[1]), 0)
    # 邮寄地址(30) > 回函地址(20) > 通讯地址(10)：只有高优先级明显领先才算唯一
    if top_priority >= 10 and top_priority > second_priority:
        return candidates[0]
    return None


def _guess_province(address: str) -> Optional[str]:
    match = re.match(r"(.{2,8}?(?:省|自治区|北京市|上海市|天津市|重庆市))", address)
    return match.group(1) if match else None


def _norm_address(value: str) -> str:
    return re.sub(r"[\s，,。;；:：]", "", value or "")


def _truncate(text: str, limit: Optional[int] = None) -> str:
    limit = limit or agent_config.document_max_chars
    text = text or ""
    if len(text) <= limit:
        return text
    logger.info(f"[document] 正文超长（{len(text)} 字符），截断到 {limit}")
    return text[:limit]
