"""检索阶段改造的离线测试（不依赖大模型、不依赖 Qdrant 服务）。

覆盖改造文档第 21 节要求的用例：
1. 相对时间 → 绝对日期（DateResolver）
2. 模型输出的校验与归一化（RetrievalFilters.from_llm）
3. 结构化条件 → Qdrant Filter（QdrantFilterBuilder）
4. 「没有任何条件时必须不产生 Filter」这条硬要求
5. 提示词能正常渲染（历史上这里因为 JSON 花括号未转义而直接报错）

运行：python -m app.test.test_query_filter_pipeline
"""
from __future__ import annotations

import json
from datetime import date

from qdrant_client import models

from app.rag.query_process.retrieval.schemas import RetrievalFilters, RetrievalQuery
from app.rag.query_process.retrieval.date_resolver import DateResolver
from app.rag.query_process.retrieval.qdrant_filter_builder import (
    QdrantFilterBuilder,
    build_qdrant_filter,
    describe_filters,
)

# 固定"今天"，让相对时间的断言稳定（不随运行日期变化）
TODAY = date(2026, 9, 10)

passed = 0
failed: list[str] = []


def check(name: str, actual, expected):
    global passed
    if actual == expected:
        passed += 1
    else:
        failed.append(f"{name}\n     期望：{expected}\n     实际：{actual}")


def test_date_resolver():
    """时间：文档第 21 节列出的表达都要能解析。"""
    resolver = DateResolver(today=TODAY)
    cases = [
        ("2026年8月", ("2026-08-01", "2026-08-31")),
        ("2026年8月份", ("2026-08-01", "2026-08-31")),
        ("2026年8月1日至2026年8月20日", ("2026-08-01", "2026-08-20")),
        ("2026年上半年", ("2026-01-01", "2026-06-30")),
        ("今年上半年", ("2026-01-01", "2026-06-30")),
        ("去年", ("2025-01-01", "2025-12-31")),
        ("今年", ("2026-01-01", "2026-12-31")),
        ("本月", ("2026-09-01", "2026-09-30")),
        ("上个月", ("2026-08-01", "2026-08-31")),
        ("最近30天", ("2026-08-12", "2026-09-10")),
        ("最近三个月", ("2026-07-01", "2026-09-10")),
        ("今年第一季度", ("2026-01-01", "2026-03-31")),
        ("本月", ("2026-09-01", "2026-09-30")),
    ]
    for expression, expected in cases:
        check(f"DateResolver({expression})", resolver.parse(expression), expected)

    # 解析不出来时不能瞎猜
    check("DateResolver(看不懂的表达)", resolver.parse("某个时候"), None)


def test_normalize_and_validate():
    """模型输出不可信：格式变体要归一化，非法值要丢弃。"""
    filters = RetrievalFilters.from_llm({
        "date_from": "2026/8/1",     # 分隔符变体
        "date_to": "2026年8月20日",   # 中文日期（归一化不了就该丢弃，不能污染检索）
        "category": "重点客户",
        "domain": "新能源领域",
        "item_name": None,
        "date_expression": "null",   # 字符串 "null" 要当成空
        "未知字段": "忽略我",
    })
    check("归一化 date_from", filters.date_from, "2026-08-01")
    check("无法识别的日期被丢弃", filters.date_to, None)
    check("date_expression='null' 视为空", filters.date_expression, None)
    check("category 保留", filters.category, "重点客户")
    check("domain 保留", filters.domain, "新能源领域")

    # 模型瞎编分类（把领域值填进 category）时必须被白名单拦掉
    hallucinated = RetrievalFilters.from_llm({"category": "新能源"})
    check("非法 category 被丢弃", hallucinated.category, None)

    # 区间写反时自动纠正
    reversed_range = RetrievalFilters.from_llm(
        {"date_from": "2026-08-20", "date_to": "2026-08-01"})
    check("区间写反自动纠正", (reversed_range.date_from, reversed_range.date_to),
          ("2026-08-01", "2026-08-20"))


def test_filter_builder():
    """结构化条件 → Qdrant Filter。"""
    builder = QdrantFilterBuilder()

    # 1. 全空 → 必须返回 None（不能因为 payload 有字段就无条件造过滤）
    check("空条件不产生 Filter", builder.build(RetrievalFilters()), None)
    check("None 入参不产生 Filter", builder.build(None), None)
    check("dict 入参同样支持", builder.build({}), None)

    # 2. 只有日期：单边条件也要能表达
    date_only = builder.build(RetrievalFilters(date_from="2026-08-01", date_to="2026-08-31"))
    condition = date_only.must[0]
    check("日期过滤字段名", condition.key, "news_date")
    # 注意：DatetimeRange 内部把字符串解析成 datetime 对象，
    # 这里断言真正发给 Qdrant 的 JSON（客户端会序列化成 RFC3339）
    wire = json.loads(date_only.model_dump_json())["must"][0]["range"]
    check("日期下界（发出的报文）", wire["gte"], "2026-08-01T00:00:00Z")
    check("日期上界含当天（发出的报文）", wire["lte"], "2026-08-31T23:59:59Z")

    open_range = builder.build(RetrievalFilters(date_from="2026-08-01"))
    open_range_wire = json.loads(open_range.model_dump_json())["must"][0]["range"]
    check("开区间只有下界", open_range_wire["lte"], None)

    # 3. 精确值条件
    full = builder.build(RetrievalFilters(
        category="行业动态", domain="新能源领域", item_name="欣旺达"))
    keys = [c.key for c in full.must]
    check("精确条件字段顺序", keys, ["category", "domain", "item_name"])
    check("精确匹配值", full.must[0].match.value, "行业动态")

    # 4. 第一版全部是 must（AND 语义），没有 should
    check("第一版不使用 should", full.should, None)


def test_end_to_end_analysis():
    """模拟大模型输出 → 校验 → 日期解析 → Filter 的完整链路。

    这里的 JSON 就是改造文档第 9 节给的示例输出。
    """
    builder = QdrantFilterBuilder()

    # 示例1：2026年8月欣旺达有什么动态？→ 日期是硬约束，主体留在语义查询里
    analysis = RetrievalQuery.from_llm({
        "rewritten_query": "欣旺达有什么动态",
        "filters": {"date_from": "2026-08-01", "date_to": "2026-08-31",
                    "date_expression": None, "category": None,
                    "domain": None, "item_name": None},
    }, original_query="2026年8月欣旺达有什么动态？")
    check("示例1 改写", analysis.rewritten_query, "欣旺达有什么动态")
    check("示例1 不产生 item_name 过滤", analysis.filters.item_name, None)
    date_filter = builder.build(analysis.filters)
    check("示例1 只有一个日期条件", len(date_filter.must), 1)

    # 示例2：今年上半年新能源行业 → 相对时间交给 Python 算
    analysis2 = RetrievalQuery.from_llm({
        "rewritten_query": "新能源行业重要动态",
        "filters": {"date_expression": "今年上半年", "domain": "新能源领域"},
    }, original_query="今年上半年新能源行业有哪些重要动态？")
    analyzed2 = DateResolver(today=TODAY).resolve(analysis2.filters)
    check("示例2 相对时间解析", (analyzed2.date_from, analyzed2.date_to),
          ("2026-01-01", "2026-06-30"))
    check("示例2 过滤条件", describe_filters(analyzed2),
          {"date": "2026-01-01 ~ 2026-06-30", "domain": "新能源领域"})

    # 示例3：只看欣旺达 → 才产生 item_name 硬过滤
    analysis3 = RetrievalQuery.from_llm({
        "rewritten_query": "欣旺达相关信息",
        "filters": {"date_from": "2026-08-01", "date_to": "2026-08-31",
                    "item_name": "欣旺达"},
    }, original_query="只看欣旺达2026年8月的相关信息。")
    check("示例3 item_name 过滤", analysis3.filters.item_name, "欣旺达")
    check("示例3 条件数", len(builder.build(analysis3.filters).must), 2)

    # 示例4：无过滤问题 → 必须完全不产生 Filter
    analysis4 = RetrievalQuery.from_llm(
        {"rewritten_query": "什么是 RAG", "filters": {}},
        original_query="什么是 RAG？")
    check("无过滤场景 query_filter is None", builder.build(analysis4.filters), None)

    # 模型完全没按格式返回（或返回非 JSON）时的降级：用原问题、不带过滤
    fallback = RetrievalQuery.from_llm({}, original_query="原始问题")
    check("降级改写=原问题", fallback.rewritten_query, "原始问题")
    check("降级后无过滤", builder.build(fallback.filters), None)

    # 兼容历史 key（旧提示词用的是 rewritten_question）
    legacy = RetrievalQuery.from_llm({"rewritten_question": "旧格式改写"}, original_query="原问题")
    check("兼容 rewritten_question", legacy.rewritten_query, "旧格式改写")


def test_payload_date_and_indexes():
    """入库侧：payload 里的 news_date 必须是 RFC3339，索引类型要对得上。"""
    from app.utils.date_utils import to_rfc3339_date
    from app.utils.qdrant_utils import CHUNKS_PAYLOAD_INDEXES

    check("紧凑日期转 RFC3339", to_rfc3339_date("20260830"), "2026-08-30T00:00:00Z")
    check("已是 ISO 日期也能转", to_rfc3339_date("2026-08-30"), "2026-08-30T00:00:00Z")
    check("已经是 RFC3339 时保持不变（幂等）",
          to_rfc3339_date("2026-08-30T00:00:00Z"), "2026-08-30T00:00:00Z")
    check("斜杠格式也能转", to_rfc3339_date("2026/8/30"), "2026-08-30T00:00:00Z")
    check("非法日期返回 None", to_rfc3339_date("不是日期"), None)
    check("非法月日被拦截", to_rfc3339_date("20269999"), None)
    check("空值返回 None", to_rfc3339_date(None), None)
    check("news_date 索引类型", CHUNKS_PAYLOAD_INDEXES["news_date"], "datetime")
    check("category 索引类型", CHUNKS_PAYLOAD_INDEXES["category"], "keyword")
    # 明确约定：item_name 不建索引（它只是文件名兜底值，不是高频过滤条件）
    check("item_name 不在索引配置里", "item_name" in CHUNKS_PAYLOAD_INDEXES, False)

    # 只存一个日期字段：过滤用的 key 必须和 payload 里的字段名一致
    from app.rag.query_process.retrieval.qdrant_filter_builder import PAYLOAD_DATE_KEY
    check("builder 与 payload 的日期字段名一致", PAYLOAD_DATE_KEY, "news_date")


def test_prompt_renders():
    """提示词必须能被 load_prompt 渲染。

    历史问题：prompt 里的 JSON 示例花括号没转义（{{ }}），
    str.format 会把 {"rewritten_query": ...} 当成字段名并抛 KeyError，
    导致前置节点整条链路不可用。
    """
    from app.core.load_prompt import load_prompt

    prompt = load_prompt(
        "query_analyzer",
        history_text="（无历史）",
        query="2026年8月欣旺达有什么动态？",
        today=TODAY.isoformat(),
        categories="重点要闻、重点客户、行业动态、同业资讯",
    )
    check("提示词渲染含问题", "2026年8月欣旺达有什么动态？" in prompt, True)
    check("提示词渲染含今天", TODAY.isoformat() in prompt, True)
    check("提示词渲染含分类枚举", "重点客户" in prompt, True)
    check("JSON 示例花括号已还原", '{"rewritten_query"' in prompt, True)


def main():
    for test in (
        test_date_resolver,
        test_normalize_and_validate,
        test_filter_builder,
        test_end_to_end_analysis,
        test_payload_date_and_indexes,
        test_prompt_renders,
    ):
        test()

    print(f"\n通过 {passed} 项，失败 {len(failed)} 项")
    for item in failed:
        print("  [FAIL]", item)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
