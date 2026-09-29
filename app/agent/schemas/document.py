"""Document Understanding Skill 的结构化输出。"""
from typing import Literal, Optional

from pydantic import BaseModel, Field

DocumentType = Literal[
    "inquiry_letter",
    "business_license",
    "generic_document",
    "unknown",
]

# 文档动作：Document Agent 自己的内部白名单。
#
# 【边界】这套枚举**只属于 Document Agent**，不再出现在 SupervisorDecision 里。
# 主 Agent 只传 task（"提取这份询证函中可用于寄送的地址"），由 Document Agent
# 自己映射成下面这四个动作（resolved_action）。加第 5 个动作时不必改主 Agent。
DocumentTaskAction = Literal[
    "extract_mailing_address",
    "summarize_document",
    "extract_company_info",
    "none",
]

# 任务结果状态：成功 / 歧义待确认 / 没找到 / 类型不支持 / 执行失败 / 没有任务
DocumentTaskStatus = Literal[
    "success",
    "ambiguous",
    "not_found",
    "unsupported",
    "failed",
    "none",
]

# 地址优选结论
AddressDecision = Literal["single", "ambiguous", "none"]


class DocumentTask(BaseModel):
    """本轮任务对象（Document Agent 自己写进 State 的中间产物）。

    注意它不是"主 Agent 下发的命令"：动作由 Document Agent 自己判定（resolved_action），
    这里只记录"这一轮在处理哪个文件、最终执行了哪个动作"，用于跨轮复用判断。
    主 Agent 下发的只有 task 文本（见 schemas/supervisor.py）。
    """

    file_id: str = ""
    action: DocumentTaskAction = "none"


class DocumentTaskResult(BaseModel):
    """Document Agent → 主 Agent 的任务结果（这次任务做得怎么样）。

    与 Document Facts（文件里有什么）分开：事实可复用，任务结果是本轮动作的结论。
    主 Agent 只读它来做决策——直接回答 / 发起 HITL / 换个动作 / 转申请书。
    """

    action: str = "none"
    status: DocumentTaskStatus = "none"
    # 地址类任务才有：唯一 / 歧义 / 没抽到
    address_decision: Optional[AddressDecision] = None
    preferred_address: Optional[str] = None
    # 任务产出（地址、摘要、企业信息…）
    data: dict = Field(default_factory=dict)
    reason: Optional[str] = None


class DocumentClassification(BaseModel):
    """文档分类结果。"""

    document_type: DocumentType = Field(description="文档类型")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = Field(default="")


class ExtractedDocumentFields(BaseModel):
    """通用字段抽取结果。

    只保留规范第 19 / 22 节要求的字段，不做无业务意义的扩张。
    """

    company_name: Optional[str] = None
    unified_social_credit_code: Optional[str] = None
    registered_address: Optional[str] = None
    province: Optional[str] = None
    city: Optional[str] = None
    legal_person: Optional[str] = None
    # 营业执照上的企业性质（"类型：有限责任公司"）与注册资本
    enterprise_nature: Optional[str] = None
    registered_capital: Optional[str] = None
    contact_person: Optional[str] = None
    contact_phone: Optional[str] = None
    document_date: Optional[str] = None
    balance: Optional[str] = None


class AddressCandidate(BaseModel):
    """地址候选：地址原文 + 它在原文里的业务标签。"""

    value: str = Field(default="", description="原文中真实出现的完整地址")
    label: str = Field(default="地址", description="邮寄地址 / 回函地址 / 通讯地址 / 注册地址…")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class DocumentUnderstanding(BaseModel):
    """一次文档理解的全部产出（分类 + 通用字段 + 地址候选 + 摘要 + 本轮动作）。

    为什么合成一个结构：这四件事都只需要同一份 OCR 正文，拆成四次调用只是把
    同一段文本喂给模型四遍。合成一次之后，任意 action 的执行都只是"读这里的字段"，
    同一份文件换任务复用时不必再调模型（见 subgraphs/document_understanding.py）。

    `resolved_action` 是重构后新增的：主 Agent 不再指定动作，改由本 Agent 在
    这一次调用里顺便判定"用户这句话对应我哪个动作"——所以调用次数没有增加，
    但动作决策权回到了子 Agent 手里。
    """

    document_type: DocumentType = "unknown"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""
    fields: ExtractedDocumentFields = Field(default_factory=ExtractedDocumentFields)
    addresses: list[AddressCandidate] = Field(default_factory=list)
    summary: str = ""
    # 本轮该执行哪个动作（由本 Agent 判定，取值见 DocumentTaskAction）。
    # 判不出来填 none，由主 Agent 反问用户，不许猜。
    resolved_action: DocumentTaskAction = "none"


class DocumentAnalysis(BaseModel):
    """Document Skill 返回给 Main Agent 的统一结构（规范第 17 节）。"""

    document_id: str = ""
    document_type: DocumentType = "unknown"
    confidence: float = 0.0
    ocr_text: str = ""
    structured_fields: dict = Field(default_factory=dict)
    candidate_addresses: list[str] = Field(default_factory=list)
    available_actions: list[str] = Field(default_factory=list)
    summary: str = ""
    error: Optional[str] = None


# 各类文档默认开放的动作（规范第 19 / 21 / 22 节）
ACTIONS_BY_DOCUMENT_TYPE: dict[str, list[str]] = {
    "inquiry_letter": [
        "extract_mailing_address",
        "summarize_document",
        "extract_company_info",
    ],
    "business_license": [
        "extract_company_info",
        "generate_business_application",
    ],
    "generic_document": ["summarize_document", "extract_company_info"],
    "unknown": ["summarize_document"],
}
