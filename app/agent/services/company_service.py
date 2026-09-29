"""企业主体检索与匹配（Company MCP 的业务侧）。

规范第 34 条要求的分层匹配策略在这里落地：
    标准化 → 精确匹配 → 包含匹配 → 模糊匹配
LLM 只负责「抽取用户想查的企业名」，企业事实全部来自 MCP。
"""
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Optional

from app.agent.mcp.company_client import company_mcp_configured, search_company
from app.agent.schemas.company import CompanyCandidate
from app.conf.agent_config import agent_config
from app.core.logger import logger

_COMPANY_SUFFIXES = (
    "股份有限公司", "有限责任公司", "有限公司", "集团有限公司",
    "公司", "集团", "厂", "研究院", "中心", "银行",
)


def normalize_company_name(name: str) -> str:
    """标准化：去掉空格、括号、标点，全角转半角，统一小写。"""
    if not name:
        return ""
    text = unicodedata.normalize("NFKC", str(name))
    text = re.sub(r"[\s\u3000()（）\[\]【】{}<>《》,，.。;；:：'\"“”‘’、\-—_/\\|]+", "", text)
    return text.lower()


def name_similarity(query: str, candidate: str) -> float:
    """企业名相似度：精确 > 包含 > 模糊。"""
    left, right = normalize_company_name(query), normalize_company_name(candidate)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0

    # 去掉"有限公司"这类后缀再比一次：用户通常只报简称
    left_core = _strip_suffix(left)
    right_core = _strip_suffix(right)
    if left_core and left_core == right_core:
        return 0.97
    if left_core and right_core and (left_core in right_core or right_core in left_core):
        shorter, longer = sorted((left_core, right_core), key=len)
        return round(0.75 + 0.2 * (len(shorter) / len(longer)), 4)
    return round(SequenceMatcher(None, left, right).ratio() * 0.85, 4)


def _strip_suffix(name: str) -> str:
    for suffix in _COMPANY_SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)]
    return name


# 判定"用户给的就是这个全名"的阈值：标准化后完全相同（name_similarity 给 1.0）
_EXACT_MATCH = 0.999

# 对外（提示词 / SSE）只暴露这些字段，不把内部分数带出去
_PUBLIC_FIELDS = (
    "company_name",
    "unified_social_credit_code",
    "registered_address",
    "province",
    "city",
    "legal_person",
    "enterprise_nature",
    "registered_capital",
)


def rank_by_name(query: str, candidates: list) -> list[dict]:
    """按"用户输入 vs 候选名称"的字面相似度排序，返回 dict 列表（写进 State）。

    为什么是我们自己算而不是厂商给：企业 MCP（财汇）不返回任何分数字段，
    实测返回里只有企业名、信用代码这些事实字段。别名表里那几个分数字段名
    是为"会返分的厂商"留的，对现在的数据源恒为 0，所以不再参与排序。

    排序的作用只有一个：**展示顺序**——多候选时 HITL 让用户按序号挑
    （node_company_select 解析 "1"/"2"），最像的排第一，用户少找一次。
    自动选中**不依赖**排序结果，见 pick_company。

    入参兼容 CompanyCandidate 对象和普通 dict（评测 / 单测会直接塞 dict）。
    """
    ranked = []
    for candidate in candidates:
        payload = (
            candidate.model_dump() if isinstance(candidate, CompanyCandidate) else dict(candidate)
        )
        payload["name_similarity"] = name_similarity(
            query, str(payload.get("company_name") or "")
        )
        ranked.append(payload)
    ranked.sort(key=lambda item: item["name_similarity"], reverse=True)
    return ranked


def public_candidate(payload: Optional[dict]) -> dict:
    """裁出可以给模型 / 前端看的字段（去掉内部分数）。"""
    if not payload:
        return {}
    return {
        key: payload[key]
        for key in _PUBLIC_FIELDS
        if payload.get(key) not in (None, "", [], {})
    }


def resolve_company(query: str, limit: Optional[int] = None) -> dict:
    """查企业：返回 {"query", "candidates", "source", "error"}。"""
    if not query or not query.strip():
        return {"query": query, "candidates": [], "source": "none", "error": "empty_query"}
    if not company_mcp_configured():
        logger.warning("[company] 企业信息 MCP 未配置（财汇：YJT_BASE_URL / YJT_API_KEY），无法查询企业")
        return {
            "query": query,
            "candidates": [],
            "source": "unconfigured",
            "error": "company_mcp_unconfigured",
        }

    try:
        result = search_company(query, limit=limit or agent_config.company_search_limit)
    except Exception as exc:
        logger.exception(f"[company] 企业检索失败：{exc}")
        return {"query": query, "candidates": [], "source": "error", "error": str(exc)}

    candidates = rank_by_name(query, result.get("candidates") or [])
    logger.info(
        f"[company] query={query} 命中 {len(candidates)} 个候选："
        f"{[item['company_name'] for item in candidates[:3]]}"
    )
    return {
        "query": query,
        "candidates": candidates,
        "source": result.get("source", "company_mcp"),
        "error": None,
    }


def pick_company(candidates: list[dict], query: str = "") -> Optional[dict]:
    """能安全自动选中时返回候选，否则返回 None 走 HITL（规范第 35 / 36 节）。

    只有两种情况允许自动选：
    1. 只有一个候选；
    2. 用户给的就是完整企业名称——**在候选里自己找完全一致的那一个**，
       而不是"看排序后的第一个"。这样排序就只是展示优化，不影响判定结果。

    多个候选 + 用户只报了简称（如"南京XX能源"）时必须让用户确认——
    这正是规范里"不能让 LLM 猜"的场景，用分数差做自动选择是不够安全的。
    """
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    if not query:
        return None
    exact = [
        item
        for item in candidates
        if name_similarity(query, str(item.get("company_name") or "")) >= _EXACT_MATCH
    ]
    if len(exact) == 1:
        return exact[0]
    return None
