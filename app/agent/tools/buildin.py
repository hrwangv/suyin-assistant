"""内置小工具：几个纯功能性的通用工具，用来把工具箱链路跑通。

刻意不放业务工具：知识库问答 / 文档理解 / 企业检索 / 申请书生成都属于
各自的子图（Node / Subgraph）职责，工具箱只登记"小而通用、无副作用"的能力：

    calculator     四则运算（AST 安全求值，不用 eval）
    current_time   当前日期时间（可指定时区）
    text_stats     文本统计（字符 / 行 / 词 / 高频词）
    random_pick    从候选里随机挑选

四个工具都不联网、不写文件、不碰数据库，方便 review 时只盯"注册与调用"这一层。
"""
import ast
import operator
import random
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from app.agent.tools.registry import tool


class CalculatorArgs(BaseModel):
    """四则运算入参。"""

    expression: str = Field(description="数学表达式，例如 (1+2)*3、7/2、2**10")


class CurrentTimeArgs(BaseModel):
    """取当前时间入参。"""

    timezone: str = Field(default="Asia/Shanghai", description="IANA 时区名，例如 Asia/Shanghai、UTC")


class TextStatsArgs(BaseModel):
    """文本统计入参。"""

    text: str = Field(description="要统计的文本")
    top_n: int = Field(default=5, ge=1, le=20, description="返回出现次数最多的前 N 个词")


class RandomPickArgs(BaseModel):
    """随机挑选入参。"""

    options: list[str] = Field(description="候选列表，至少一个")
    count: int = Field(default=1, ge=1, description="挑选几个（不超过候选数量）")


# --------------------------------------------------------------------------
# calculator：只允许数字、四则运算与少量数学函数，绝不 eval 用户字符串
# --------------------------------------------------------------------------
_BINARY_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCTIONS = {"abs": abs, "round": round, "min": min, "max": max, "sum": sum, "int": int, "float": float}
# 指数上限：防止 2**99999999 这类表达式把进程算死
_MAX_EXPONENT = 1000
_MAX_EXPRESSION_CHARS = 200
_MAX_NODES = 50


def _eval_node(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ValueError("只支持数字常量")
        return node.value
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_eval_node(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPS:
        left, right = _eval_node(node.left), _eval_node(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > _MAX_EXPONENT:
            raise ValueError(f"指数不能超过 {_MAX_EXPONENT}")
        return _BINARY_OPS[type(node.op)](left, right)
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCTIONS:
            raise ValueError("只支持 abs / round / min / max / sum / int / float")
        if node.keywords:
            raise ValueError("函数不支持关键字参数")
        return _FUNCTIONS[node.func.id](*[_eval_node(arg) for arg in node.args])
    raise ValueError(f"表达式里有不支持的写法：{type(node).__name__}")


@tool(
    category="buildin",
    tags=["通用", "计算"],
    display_name="计算器",
    description="计算一个数学表达式（支持 + - * / // % ** 与 abs/round/min/max/sum），用于算术问题。",
    args_schema=CalculatorArgs,
)
def calculator(expression: str) -> dict:
    """安全求值四则运算表达式，返回 {expression, value}。"""
    text = (expression or "").strip()
    if not text:
        raise ValueError("表达式不能为空")
    if len(text) > _MAX_EXPRESSION_CHARS:
        raise ValueError(f"表达式过长（上限 {_MAX_EXPRESSION_CHARS} 字符）")

    tree = ast.parse(text, mode="eval")
    if sum(1 for _ in ast.walk(tree)) > _MAX_NODES:
        raise ValueError("表达式过于复杂")
    return {"expression": text, "value": _eval_node(tree)}


# --------------------------------------------------------------------------
# current_time：只读系统时钟，时区不合法时明确报错（不静默用本地时区）
# --------------------------------------------------------------------------
@tool(
    category="buildin",
    tags=["通用", "时间"],
    display_name="当前时间",
    description="获取当前日期时间，可指定时区（如 Asia/Shanghai、UTC），用于和时间相关的问题。",
    args_schema=CurrentTimeArgs,
)
def current_time(timezone: str = "Asia/Shanghai") -> dict:
    """返回指定时区的当前时间 {timezone, datetime, date, weekday, timestamp}。"""
    try:
        zone = ZoneInfo(timezone)
    except Exception as exc:
        raise ValueError(f"时区不可用：{timezone}") from exc

    now = datetime.now(zone)
    return {
        "timezone": timezone,
        "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
        "date": now.date().isoformat(),
        "weekday": ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][now.weekday()],
        "timestamp": int(now.timestamp()),
    }


# --------------------------------------------------------------------------
# text_stats：纯字符串处理
# --------------------------------------------------------------------------
_WORD_RE = re.compile(r"[A-Za-z0-9_]+")
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")


@tool(
    category="buildin",
    tags=["通用", "文本"],
    display_name="文本统计",
    description="统计一段文本的字符数、行数、词数与高频词，用于长度估算和关键词粗看。",
    args_schema=TextStatsArgs,
)
def text_stats(text: str, top_n: int = 5) -> dict:
    """返回 {chars, chars_no_space, lines, words, cjk_chars, top_terms}。"""
    content = text or ""
    words = _WORD_RE.findall(content)
    cjk_runs = _CJK_RUN_RE.findall(content)
    cjk_chars = sum(len(run) for run in cjk_runs)
    # 中文按「双字滑窗」取词：单个汉字做高频词没有意义，双字能看出"苏银""助手"这类词
    cjk_terms = [run[i : i + 2] for run in cjk_runs for i in range(len(run) - 1)]
    counter: dict[str, int] = {}
    for term in words + cjk_terms:
        counter[term] = counter.get(term, 0) + 1
    top_terms = sorted(counter.items(), key=lambda item: (-item[1], item[0]))[:top_n]
    return {
        "chars": len(content),
        "chars_no_space": len(re.sub(r"\s", "", content)),
        "lines": len(content.splitlines()) if content else 0,
        "words": len(words),
        "cjk_chars": cjk_chars,
        "top_terms": [{"term": term, "count": count} for term, count in top_terms],
    }


# --------------------------------------------------------------------------
# random_pick：给"从几个选项里挑一个"提供确定性之外的随机性
# --------------------------------------------------------------------------
@tool(
    category="buildin",
    tags=["通用", "随机"],
    display_name="随机挑选",
    description="从候选列表里随机挑选若干个，用于抽签、随机选一个方案等场景（有随机性，不适合需要确定答案的问题）。",
    args_schema=RandomPickArgs,
)
def random_pick(options: list[str], count: int = 1) -> dict:
    """从候选里随机挑 count 个（不重复），返回 {picked, options_count}。"""
    items = [str(item) for item in (options or []) if str(item).strip()]
    if not items:
        raise ValueError("候选列表不能为空")
    if count > len(items):
        raise ValueError(f"要挑 {count} 个，但候选只有 {len(items)} 个")
    return {"picked": random.sample(items, count), "options_count": len(items)}
