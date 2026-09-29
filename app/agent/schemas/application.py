"""业务申请书的字段定义（规范第 31 节）。

字段很少是刻意的：模板里有什么就抽什么，不为「显得复杂」加字段。
"""
from datetime import date
from typing import Optional

from pydantic import BaseModel, Field

# 第一版必填字段（缺任何一个都要走 HITL 补充）
REQUIRED_APPLICATION_FIELDS = [
    "company_name",
    "unified_social_credit_code",
    "province",
]


class ApplicationData(BaseModel):
    """业务申请书渲染所需的字段。"""

    company_name: str
    unified_social_credit_code: str
    province: str
    registered_address: Optional[str] = None
    city: Optional[str] = None
    legal_person: Optional[str] = None
    # 营业执照 / 企业 MCP 能取到的主体信息：填进申请书表单，取不到就留空人工补
    enterprise_nature: Optional[str] = None    # 企业性质，如"有限责任公司"
    registered_capital: Optional[str] = None   # 注册资本，如"5000万元人民币"
    notes: Optional[str] = None
    application_date: str = Field(default_factory=lambda: date.today().isoformat())

    def missing_fields(self) -> list[str]:
        """返回缺失的必填字段名。"""
        return [
            name
            for name in REQUIRED_APPLICATION_FIELDS
            if not str(getattr(self, name, "") or "").strip()
        ]
