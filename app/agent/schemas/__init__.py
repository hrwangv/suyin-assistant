"""Agent 的结构化输出定义（Pydantic）。

大模型的输出一律先落到这里的模型上，业务代码只消费模型字段，
不做「对模型文本做字符串匹配」这种判断（规范第 10 节）。
"""
from app.agent.schemas.application import ApplicationData
from app.agent.schemas.agent_result import AgentResult, make_agent_result
from app.agent.schemas.company import CompanyCandidate, CompanySearchResult
from app.agent.schemas.document import (
    DocumentAnalysis,
    DocumentClassification,
    ExtractedDocumentFields,
)
from app.agent.schemas.supervisor import SupervisorDecision

__all__ = [
    "AgentResult",
    "ApplicationData",
    "CompanyCandidate",
    "CompanySearchResult",
    "DocumentAnalysis",
    "DocumentClassification",
    "ExtractedDocumentFields",
    "SupervisorDecision",
    "make_agent_result",
]
