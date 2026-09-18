"""检索阶段的结构化数据结构（Query Analyzer 的产物定义）。

架构原则（对应改造文档「必须遵守的架构原则」）：
- 大模型只负责：理解自然语言、抽取结构化条件、生成检索查询文本；
- Python 只负责：校验结构化条件、处理日期、构造 Qdrant Filter、执行查询。

所以本模块只做「结构定义 + 取值校验」，不依赖大模型，也不依赖 Qdrant，
这样这部分逻辑可以单独跑单元测试。
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

# 允许 Query Analyzer 输出的过滤字段白名单。
# payload 里字段很多（chunk_id / part / file_title ...），但只有「确定性约束」
# 类的字段才适合开放成过滤条件，后续扩展也只往这个元组里加。
FILTER_FIELDS: tuple[str, ...] = (
    "date_from",
    "date_to",
    "date_expression",
    "category",
    "domain",
    "item_name",
)

# category 的合法取值。
# 注意：需与 node_document_split.assign_category_and_domain 里的 CATEGORY_KEYWORDS
# 保持一致，那边新增分类时记得同步这里。
KNOWN_CATEGORIES: frozenset[str] = frozenset({
    "重点要闻",
    "重点客户",
    "行业动态",
    "同业资讯",
    "头部及域内企业要闻",
    "宏观动态",
})


# 日期正则
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$") # 2026-08-01
_COMPACT_DATE_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})$") # 20260801
_SEP_DATE_RE = re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$") # # 2026/8/1 或 2026.8.1

# 模型偶尔会把整段解释塞进某个字段，这里做个长度兜底，避免脏数据进入过滤条件
_MAX_TEXT_LEN = 64 # 长度兜底、过长的不行，防止大模型输出整段解释塞进某个字段，导致 Qdrant Filter 解析失败
_EMPTY_TOKENS = {"null", "none", "nan", "无", "空", "不限", "全部"}


def clean_text(value) -> str | None:
    """把模型输出里的 None / "" / "null" / "无" 这类空值统一成 None。"""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or text.lower() in _EMPTY_TOKENS:
        return None
    return text[:_MAX_TEXT_LEN]


def normalize_date(value) -> str | None:
    """把日期归一化成 YYYY-MM-DD，识别不了就返回 None。

    模型有时会输出 20260801 或 2026/8/1 这类变体，这里一起兼容；
    但只做「格式归一化」，不做「相对时间推算」——那是 DateResolver 的职责。
    """
    text = clean_text(value)
    if not text:
        return None
    if _DATE_RE.match(text):
        return text
    matched = _COMPACT_DATE_RE.match(text) or _SEP_DATE_RE.match(text)
    if not matched:
        return None
    year, month, day = matched.group(1), int(matched.group(2)), int(matched.group(3))
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return f"{year}-{month:02d}-{day:02d}"


@dataclass
class RetrievalFilters:
    """从用户问题里抽取出来的结构化检索条件（已经过 Python 校验）。"""

    date_from: str | None = None
    date_to: str | None = None
    # 相对时间表达（"今年上半年" / "最近三个月"），交给 DateResolver 计算成绝对日期
    date_expression: str | None = None
    category: str | None = None
    domain: str | None = None
    item_name: str | None = None

    @classmethod
    def from_llm(cls, raw, allowed_categories: frozenset[str] | None = None) -> "RetrievalFilters":
        """从大模型输出里构造 filters，任何不合法的取值一律丢弃。

        设计取向很重要：**宁可不过滤（退化成纯语义检索），也不要带着错误条件去检索**。
        因为错误条件会把正确答案过滤掉，而检索侧看不出这是过滤造成的。
        """

        # 不是字典直接退化成空条件，避免大模型输出了整段解释塞进 filters 里导致 Qdrant Filter 解析失败。
        if not isinstance(raw, dict):
            return cls()

        filters = cls(
            date_from=normalize_date(raw.get("date_from")),
            date_to=normalize_date(raw.get("date_to")),
            date_expression=clean_text(raw.get("date_expression")),
            category=clean_text(raw.get("category")),
            domain=clean_text(raw.get("domain")),
            item_name=clean_text(raw.get("item_name")),
        )

        # 日期区间写反时自动纠正（用户可能说"8月20日到8月1日"）
        if filters.date_from and filters.date_to and filters.date_from > filters.date_to:
            filters.date_from, filters.date_to = filters.date_to, filters.date_from

        # category 是代码里写死的枚举，可以直接做白名单校验；
        # domain 会随着文档增长（从"1.新能源领域"这类标题解析出来），
        # 所以不做白名单校验，靠检索侧的「为空自动放宽」兜底。
        allowed = KNOWN_CATEGORIES if allowed_categories is None else allowed_categories
        if filters.category and filters.category not in allowed:
            filters.category = None

        return filters

    def is_empty(self) -> bool:
        """没有任何可用的确定性条件时返回 True（此时不应该构造 Filter）。

        注意 date_expression 不参与判断：它只是中间表达，
        解析成功会变成 date_from/date_to，解析失败就应该被忽略。
        """
        return not any((
            self.date_from,
            self.date_to,
            self.category,
            self.domain,
            self.item_name,
        ))

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RetrievalQuery:
    """Query Analyzer 的完整产物：语义查询 + 结构化条件。

    对应改造文档中的 RetrievalQuery。语义条件和硬约束在这里被明确分开：
    rewritten_query 用于向量检索，filters 用于 payload 过滤，
    两者不能互相污染（比如把"2026年8月"重新拼回查询文本）。
    """

    rewritten_query: str
    filters: RetrievalFilters

    @classmethod
    def from_llm(cls, data: dict, original_query: str = "") -> "RetrievalQuery":
        """解析大模型的 JSON 输出，缺失/非法字段全部降级为安全默认值。"""
        if not isinstance(data, dict):
            data = {}

        # 历史提示词里用过 rewritten_question 这个名字，这里做兼容，
        # 避免因为 key 不一致导致改写结果被静默丢弃（退回成原问题）。
        rewritten_query = (
            clean_text(data.get("rewritten_query"))
            or clean_text(data.get("rewritten_question"))
            or original_query
        )
        filters = RetrievalFilters.from_llm(data.get("filters"))
        return cls(rewritten_query=rewritten_query, filters=filters)
