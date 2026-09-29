"""Company MCP 的领域模型。"""
from typing import Optional

from pydantic import BaseModel, Field


class CompanyCandidate(BaseModel):
    """企业候选主体。"""

    company_name: str
    unified_social_credit_code: Optional[str] = None
    registered_address: Optional[str] = None
    province: Optional[str] = None
    city: Optional[str] = None
    legal_person: Optional[str] = None
    enterprise_nature: Optional[str] = None    # 企业类型 / 企业性质
    registered_capital: Optional[str] = None   # 注册资本
    source: str = "company_mcp"

    @property
    def display(self) -> str:
        code = self.unified_social_credit_code or "未知"
        return f"{self.company_name}（统一社会信用代码：{code}）"


class CompanySearchResult(BaseModel):
    """企业检索结果。"""

    query: str
    candidates: list[CompanyCandidate] = Field(default_factory=list)
    source: str = "company_mcp"
    error: Optional[str] = None
