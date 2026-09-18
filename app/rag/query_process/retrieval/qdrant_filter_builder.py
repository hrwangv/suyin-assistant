"""RetrievalFilters → Qdrant Filter。

改造文档第 10 节：**这一层必须由 Python 完成**，
禁止让大模型直接产出 Qdrant Filter / 查询代码。

集中放在这里的三个好处：
1. payload 字段名只在这一个文件里出现，改名不用全项目搜；
2. 过滤逻辑可以脱离大模型、脱离数据库单独跑测试；
3. 以后换向量库（Milvus 等）只需要替换这一层。
"""
from __future__ import annotations

from qdrant_client import models

from app.rag.query_process.retrieval.schemas import RetrievalFilters

# payload 字段名，需与入库阶段写入的字段保持一致（见 utils/qdrant_utils.upsert_chunks）。
# 日期字段用 RFC3339 存储，才能用 DatetimeRange 做范围过滤。
# 日期字段沿用切分阶段写入的 news_date（入库时已统一成 RFC3339，
# 见 utils/date_utils.to_rfc3339_date），没有再额外存第二份日期字段
PAYLOAD_DATE_KEY = "news_date"
PAYLOAD_CATEGORY_KEY = "category"
PAYLOAD_DOMAIN_KEY = "domain"
PAYLOAD_ITEM_NAME_KEY = "item_name"


def _day_start(value: str) -> str:
    """把 YYYY-MM-DD 转成当天的起始时刻（RFC3339）。"""
    return f"{value}T00:00:00Z"


def _day_end(value: str) -> str:
    """把 YYYY-MM-DD 转成当天的结束时刻（RFC3339，含当天）。"""
    return f"{value}T23:59:59Z"


class QdrantFilterBuilder:
    """结构化条件 → Qdrant Filter；没有任何条件时返回 None。

    返回 None 很重要：不能因为 payload 里存在 date/category 字段，
    就无条件产生一个"看起来有过滤"的空条件。
    """

    def build(self, filters: RetrievalFilters | dict | None) -> models.Filter | None:
        filters = self._coerce(filters)
        if filters is None or filters.is_empty():
            return None

        must: list[models.FieldCondition] = []

        # 1. 时间条件：范围过滤。
        # 只给"有值"的一端加约束，所以"2026年8月以后"这种单边条件也能表达。
        if filters.date_from or filters.date_to:
            date_range = {}
            if filters.date_from:
                date_range["gte"] = _day_start(filters.date_from)
            if filters.date_to:
                date_range["lte"] = _day_end(filters.date_to)
            must.append(models.FieldCondition(
                key=PAYLOAD_DATE_KEY,
                range=models.DatetimeRange(**date_range),
            ))

        # 2. 精确值条件：category / domain / item_name 都是等值匹配。
        # 第一版全部用 must（即 AND 语义），需要 OR 时再引入 should。
        for key, value in (
            (PAYLOAD_CATEGORY_KEY, filters.category),
            (PAYLOAD_DOMAIN_KEY, filters.domain),
            (PAYLOAD_ITEM_NAME_KEY, filters.item_name),
        ):
            if value:
                must.append(models.FieldCondition(
                    key=key,
                    match=models.MatchValue(value=value),
                ))

        if not must:
            return None
        return models.Filter(must=must)

    @staticmethod
    def _coerce(filters) -> RetrievalFilters | None:
        """兼容两种入参： dataclass 对象，或 state 里存的普通 dict。"""
        if filters is None:
            return None
        if isinstance(filters, RetrievalFilters):
            return filters
        if isinstance(filters, dict):
            return RetrievalFilters(**{
                key: filters.get(key) for key in RetrievalFilters.__dataclass_fields__
            })
        return None


def describe_filters(filters: RetrievalFilters | dict | None) -> dict:
    """生成给人看的过滤条件摘要，用于日志定位问题（改造文档第 20 节）。

    排查时的价值：这一条日志能直接回答"是模型没抽出来条件，还是条件构造错了"。
    """
    resolved = QdrantFilterBuilder._coerce(filters)
    if resolved is None or resolved.is_empty():
        return {}

    summary: dict = {}
    if resolved.date_from or resolved.date_to:
        summary["date"] = f"{resolved.date_from or '*'} ~ {resolved.date_to or '*'}"
    if resolved.category:
        summary["category"] = resolved.category
    if resolved.domain:
        summary["domain"] = resolved.domain
    if resolved.item_name:
        summary["item_name"] = resolved.item_name
    return summary


def build_qdrant_filter(filters: RetrievalFilters | dict | None) -> models.Filter | None:
    """便捷函数：直接构造 Filter，省去实例化 Builder。"""
    return QdrantFilterBuilder().build(filters)
