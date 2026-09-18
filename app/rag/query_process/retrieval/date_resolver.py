"""相对时间表达 → 绝对日期区间。

改造文档的要求：**大模型不做日期算术**。
遇到"今年上半年""最近三个月"这类表达，模型只输出 date_expression，
具体是哪天到哪天由这里结合"今天"算出来，避免模型算错日期。
"""
from __future__ import annotations

import re
from calendar import monthrange
from datetime import date, timedelta

from app.rag.query_process.retrieval.schemas import RetrievalFilters, normalize_date


def _day(year: int, month: int, day: int) -> str:
    return f"{year:04d}-{month:02d}-{day:02d}"


def month_bounds(year: int, month: int) -> tuple[str, str]:
    """某个月的起止日。"""
    return _day(year, month, 1), _day(year, month, monthrange(year, month)[1])


def year_bounds(year: int) -> tuple[str, str]:
    return _day(year, 1, 1), _day(year, 12, 31)


def quarter_bounds(year: int, quarter: int) -> tuple[str, str]:
    """第 N 季度的起止日。"""
    start_month = (quarter - 1) * 3 + 1
    end_month = start_month + 2
    return _day(year, start_month, 1), _day(year, end_month, monthrange(year, end_month)[1])


def half_year_bounds(year: int, first_half: bool) -> tuple[str, str]:
    """上半年 / 下半年的起止日。"""
    return (_day(year, 1, 1), _day(year, 6, 30)) if first_half else (_day(year, 7, 1), _day(year, 12, 31))


_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def cn_to_int(text: str) -> int | None:
    """把「三」「十」「十二」「二十四」这类中文数字转成 int。"""
    if not text:
        return None
    if text.isdigit():
        return int(text)
    text = text.replace("〇", "零")
    if "十" in text:
        left, _, right = text.partition("十")
        tens = _CN_DIGITS.get(left, 1) if left else 1
        ones = _CN_DIGITS.get(right, 0) if right else 0
        if left and left not in _CN_DIGITS:
            return None
        return tens * 10 + ones
    if len(text) == 1 and text in _CN_DIGITS:
        return _CN_DIGITS[text]
    return None


class DateResolver:
    """把 RetrievalFilters.date_expression 解析成 date_from / date_to。

    用法：
        filters = DateResolver().resolve(filters)

    解析不出来时保持原样（date_from/date_to 仍为 None），
    这样上层不会因为一个看不懂的时间表达就构造出错误的时间过滤。
    """

    def __init__(self, today: date | None = None):
        # 允许注入 today，方便单元测试固定"今天"
        self.today = today or date.today()

    # ------------------------------------------------------------------
    # 对外入口
    # ------------------------------------------------------------------
    def resolve(self, filters: RetrievalFilters) -> RetrievalFilters:
        if not isinstance(filters, RetrievalFilters):
            return filters

        # 1. 大模型已经给出绝对日期：直接用，不再看 date_expression
        if filters.date_from or filters.date_to:
            return filters

        # 2. 有相对时间表达：尝试换算
        if filters.date_expression:
            bounds = self.parse(filters.date_expression)
            if bounds:
                filters.date_from, filters.date_to = bounds

        return filters

    # ------------------------------------------------------------------
    # 解析规则（按顺序匹配，命中即返回）
    # ------------------------------------------------------------------
    def parse(self, expression: str) -> tuple[str, str] | None:
        text = (expression or "").strip()
        if not text:
            return None

        # 2.1 表达式里直接写了绝对日期（模型没按规范输出时的兜底）
        explicit = self._explicit_dates(text)
        if explicit:
            return explicit

        today = self.today
        year = today.year

        # 2.2 单点日期
        if re.search(r"今天|今日|当天", text):
            d = today.isoformat()
            return d, d
        if re.search(r"昨天|昨日", text):
            d = (today - timedelta(days=1)).isoformat()
            return d, d
        if re.search(r"前天", text):
            d = (today - timedelta(days=2)).isoformat()
            return d, d

        # 2.3 周
        if re.search(r"本周|这周|这个星期|本星期", text):
            monday = today - timedelta(days=today.weekday())
            return monday.isoformat(), (monday + timedelta(days=6)).isoformat()
        if re.search(r"上周|上个星期|上星期", text):
            monday = today - timedelta(days=today.weekday() + 7)
            return monday.isoformat(), (monday + timedelta(days=6)).isoformat()

        # 2.4 「最近N天/周/个月/年」
        recent = self._recent(text)
        if recent:
            return recent

        # 2.5 月 / 年 / 半年 / 季度（带年份修饰）
        target_year = year
        if re.search(r"去年|上年", text):
            target_year = year - 1
        elif re.search(r"前年", text):
            target_year = year - 2
        elif re.search(r"明年", text):
            target_year = year + 1

        # 2.6 半年（"今年上半年" / "下半年" / "2026年上半年"）
        half = re.search(r"(上|下)\s*半年", text)
        if half:
            explicit_year = self._year_in(text)
            return half_year_bounds(explicit_year or target_year, half.group(1) == "上")

        # 2.7 季度（"今年第一季度" / "2026年Q1" / "一季度"）
        quarter = self._quarter(text)
        if quarter:
            explicit_year = self._year_in(text)
            return quarter_bounds(explicit_year or target_year, quarter)

        # 2.8 具体月份（"2026年8月" / "今年8月" / "8月份"）
        month_match = re.search(r"(\d{1,2}|[一二三四五六七八九十]{1,3})\s*月", text)
        if month_match:
            month = cn_to_int(month_match.group(1))
            if month and 1 <= month <= 12:
                explicit_year = self._year_in(text)
                return month_bounds(explicit_year or target_year, month)

        # 2.9 本月 / 上个月
        if re.search(r"本月|这个月|当月|本月份", text):
            return month_bounds(year, today.month)
        if re.search(r"上个月|上月", text):
            prev = today.replace(day=1) - timedelta(days=1)
            return month_bounds(prev.year, prev.month)

        # 2.10 年份（"2026年" / "去年"）
        explicit_year = self._year_in(text)
        if explicit_year:
            return year_bounds(explicit_year)
        if re.search(r"去年|上年|今年|本年|前年|明年", text):
            return year_bounds(target_year)

        return None

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    @staticmethod
    def _year_in(text: str) -> int | None:
        matched = re.search(r"(19|20)\d{2}", text)
        return int(matched.group(0)) if matched else None

    @staticmethod
    def _quarter(text: str) -> int | None:
        """识别第一季度 / 第1季度 / 一季度 / Q1 这几种写法。"""
        matched = re.search(r"第?\s*([1-4一二三四])\s*季度|[Qq]([1-4])", text)
        if not matched:
            return None
        raw = matched.group(1) or matched.group(2)
        value = cn_to_int(raw)
        return value if value and 1 <= value <= 4 else None

    def _recent(self, text: str) -> tuple[str, str] | None:
        """「最近三个月」「近7天」「过去两年」这类滚动区间。"""
        matched = re.search(
            r"(?:最近|近|过去|前)\s*([0-9一二三四五六七八九十]{1,3})\s*(天|日|周|个?月|年)",
            text,
        )
        if not matched:
            return None
        amount = cn_to_int(matched.group(1))
        if not amount or amount <= 0:
            return None
        unit = matched.group(2)
        today = self.today

        if unit in ("天", "日"):
            start = today - timedelta(days=amount - 1)
        elif unit == "周":
            start = today - timedelta(days=amount * 7 - 1)
        elif unit.endswith("月"):
            # 向前推 N 个月，从那个月的 1 号算起
            total = today.year * 12 + (today.month - 1) - (amount - 1)
            start = date(total // 12, total % 12 + 1, 1)
        else:  # 年
            start = date(today.year - (amount - 1), 1, 1)

        return start.isoformat(), today.isoformat()

    @staticmethod
    def _explicit_dates(text: str) -> tuple[str, str] | None:
        """从表达式里捞出写死的日期，用于模型没按规范输出的兜底。"""
        found: list[str] = []

        # 2026年8月20日 / 2026年8月
        for year, month, day in re.findall(r"((?:19|20)\d{2})\s*年\s*(\d{1,2})\s*月(?:\s*(\d{1,2})\s*日)?", text):
            if day:
                found.append(_day(int(year), int(month), int(day)))
            else:
                found.extend(month_bounds(int(year), int(month)))

        # 2026-08-20 / 2026/08/20
        for value in re.findall(r"(?:19|20)\d{2}[-/.]\d{1,2}[-/.]\d{1,2}", text):
            normalized = normalize_date(value)
            if normalized:
                found.append(normalized)

        if not found:
            return None
        return min(found), max(found)
