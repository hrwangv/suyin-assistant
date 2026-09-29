"""Company MCP 客户端：企业名称检索 → 统一候选结构。

LLM 不得凭空生成统一社会信用代码（规范第 4.6 节），
所有企业事实只能来自这里返回的数据。

连接信息（地址 / 鉴权 / 工具名）来自代码声明 `app/agent/mcp/builtin.py` 的 company 一条。
"""
import re
from typing import Any, Optional

from app.agent.mcp.client import MCPServerSpec, call_tool
from app.agent.mcp.registry import COMPANY_SERVER, get_server, resolve_tool_name, server_adapter
from app.agent.schemas.company import CompanyCandidate
from app.conf.agent_config import agent_config
from app.core.logger import logger

_NAME_KEYS = (
    "company_name", "companyName", "name", "企业名称", "ent_name", "entName",
    "entname", "corp_name", "corpName", "cust_name", "customer_name", "title",
)
_CREDIT_KEYS = (
    "unified_social_credit_code", "unifiedSocialCreditCode", "credit_code", "creditCode",
    "uscc", "usci", "social_credit_code", "credit_no", "reg_no", "regNo",
    "统一社会信用代码", "纳税人识别号",
)
_ADDRESS_KEYS = (
    "registered_address", "registeredAddress", "reg_address", "regAddress",
    "address", "注册地址", "企业地址", "reg_location", "dom",
)
# 财汇把"所属地区"展开成 belonging_province / belonging_city 这类列名
_PROVINCE_KEYS = (
    "province", "province_name", "provinceName", "reg_province", "省份",
    "belonging_province", "belongingProvince", "belonging_province_name",
)
_CITY_KEYS = (
    "city", "city_name", "cityName", "reg_city", "城市",
    "belonging_city", "belongingCity",
)
_LEGAL_KEYS = (
    "legal_person", "legalPerson", "legal_representative", "legalRepresentative",
    "fr_name", "frName", "oper_name", "operator", "法定代表人",
)
# 企业性质 / 组织形式：注意这两个概念不同——财汇把"组织形式"（有限责任公司）
# 和"企业性质"（国有企业）分成两个字段，申请书模板里那一格要的是组织形式，
# 所以 organization_form 排在最前面优先取。
_NATURE_KEYS = (
    "organization_form", "organizationForm", "组织形式", "企业组织形式",
    "enterprise_nature", "enterpriseNature", "enterprise_type", "enterpriseType",
    "company_type", "companyType", "company_org_type", "ent_type", "entType",
    "org_type", "orgType", "econ_kind", "econKind", "企业性质", "企业类型", "公司类型",
)
# 注册资本：注意单位差异，厂商多数给"5000万元人民币"这样的原文
_CAPITAL_KEYS = (
    "registered_capital", "registeredCapital", "reg_capital", "regCapital",
    "regist_capi", "registCapi", "capital", "注册资本",
)
_CREDIT_RE = re.compile(r"^[0-9A-HJ-NPQRTUWXY]{18}$")
_PROVINCE_RE = re.compile(r".*(省|自治区|北京市|上海市|天津市|重庆市|香港|澳门|台湾).*")

# 财汇"企业基本指标查询"返回的是长表，每行一个指标；这里把中文指标名映射成我们的字段名
_CN_INDICATOR_FIELDS = {
    "企业名称": "company_name",
    "统一社会信用代码": "unified_social_credit_code",
    "法定代表人": "legal_person",
    "注册地址": "registered_address",
    "企业性质": "enterprise_nature",
    "组织形式": "organization_form",
    "企业类型": "organization_form",
    "注册资本": "registered_capital",
    "注册资本(万元)": "registered_capital",
    "注册资本（万元）": "registered_capital",
    "注册资本币种": "capital_currency",
    "省": "province",
    "省份": "province",
    "市": "city",
    "城市": "city",
    "所属省": "province",
    "所属市": "city",
    "所属区县": "district",
    "所属地区": "region",
    "地区": "region",
}


class CompanyQueryError(RuntimeError):
    """企业 MCP 返回业务失败（厂商用 200 包了一个错误信封）。"""


def company_spec() -> MCPServerSpec:
    """Company MCP 的连接描述（地址等来自 builtin.py 的声明）。"""
    entry = get_server(COMPANY_SERVER)
    if entry is None:
        raise RuntimeError("Company MCP 未在 app/agent/mcp/builtin.py 中声明")
    return entry.to_spec()


def company_mcp_configured() -> bool:
    return company_spec().configured


def company_search_tool_name() -> str:
    """企业检索工具名（声明里的 tools.search）。"""
    return resolve_tool_name(COMPANY_SERVER, "search", "search_company")


def company_args_template() -> dict:
    """调用时附带的固定参数（声明里的 adapter.args_template）。"""
    template = server_adapter(COMPANY_SERVER).get("args_template")
    return template if isinstance(template, dict) else {}


def search_company(query: str, limit: Optional[int] = None, tool_name: Optional[str] = None) -> dict:
    """调用 Company MCP 检索企业，返回 {"query", "candidates": [...]}。

    两种调用约定都支持（由 builtin.py 的 adapter.call_style 决定）：
        direct        一次调用：call_tool(检索工具, {query, limit})
        execute_tool  财汇两步式：call_tool("execute_tool",
                          {tool_name: 子工具, arguments: 子工具自己的参数})

    调用失败时抛异常，由 company_service 决定降级策略（提示用户补全名称/上传营业执照）。
    """
    spec = company_spec()
    page_limit = limit or agent_config.company_search_limit
    target_tool = tool_name or company_search_tool_name()
    arguments = build_company_arguments(query, page_limit)
    logger.info(
        f"[company_mcp] 调用 {target_tool}，query={query}，"
        f"call_style={company_call_style()}，参数键={sorted(arguments.keys())}"
    )
    last_error = ""
    try:
        raw = call_tool(spec, target_tool, arguments)
        candidates = normalize_company_candidates(raw)
    except CompanyQueryError as exc:
        # "没查到这家企业"是业务失败，不是异常；先记下来，还要试关键字匹配
        last_error, raw, candidates = str(exc), {}, []

    # 精确匹配没结果时换成"企业名称关键字 + 包含"再试一次。
    # 只对 parameter_list 形态（筛选工具）有意义——那种工具的"企业名称"是精确匹配，
    # 用户报简称时必须换条件；档案类工具（target_company）自己会做名称匹配，没有关键字变体。
    if (
        not candidates
        and company_call_style() == "execute_tool"
        and company_sub_arg_style() == "parameter_list"
    ):
        logger.info(f"[company_mcp] 精确匹配无结果，改用名称关键字包含匹配：query={query}")
        try:
            raw = call_tool(
                spec, target_tool, build_company_arguments(query, page_limit, match="keyword")
            )
            candidates = normalize_company_candidates(raw)
        except CompanyQueryError as exc:
            last_error = str(exc)

    if not candidates and last_error:
        # 两次都没查到：把厂商的原话抛上去，由 company_service 降级成"请补全名称"
        raise CompanyQueryError(last_error)
    return {"query": query, "candidates": candidates, "source": "company_mcp", "raw": raw}


def company_call_style() -> str:
    """调用约定：direct（默认）/ execute_tool（财汇两步式）。"""
    style = str(server_adapter(COMPANY_SERVER).get("call_style") or "direct")
    return style.strip().lower()


def company_sub_tool() -> str:
    """execute_tool 模式下要执行的子工具名（财汇：企业基本信息筛选）。"""
    return str(server_adapter(COMPANY_SERVER).get("sub_tool") or "").strip()


def company_sub_args() -> dict:
    """子工具的固定入参（如 indicator_name 指标清单）。"""
    value = server_adapter(COMPANY_SERVER).get("sub_args")
    return dict(value) if isinstance(value, dict) else {}


def company_sub_arg_style() -> str:
    """子工具入参形态：target_company（按企业名查）/ parameter_list（按条件筛）。"""
    value = str(server_adapter(COMPANY_SERVER).get("sub_arg_style") or "target_company")
    return value.strip().lower()


def build_company_arguments(query: str, limit: int, match: str = "exact") -> dict:
    """按声明里的调用约定组装入参。

    :param match: exact（企业名称，精确）/ keyword（企业名称关键字，包含）
                 仅在 parameter_list 形态（筛选工具）下生效
    """
    if company_call_style() != "execute_tool":
        arguments: dict[str, Any] = {"query": query, "limit": limit}
        arguments.update(company_args_template())
        return arguments

    sub_tool = company_sub_tool()
    if not sub_tool:
        raise RuntimeError(
            "company 声明里缺少 adapter.sub_tool：execute_tool 模式必须指定要执行的子工具"
        )
    arg_style = company_sub_arg_style()
    if arg_style == "parameter_list":
        # 筛选器形态：parameter_list（条件数组）+ express（条件间逻辑关系，单条件就是 "1"）
        indicator = "企业名称" if match == "exact" else "企业名称关键字"
        sub_arguments: dict[str, Any] = {
            "parameter_list": [
                {
                    "indicator_name": indicator,
                    "operator": "等于" if match == "exact" else "包含",
                    "indicator_value": [query],
                }
            ],
            "express": "1",
            "limit": limit,
        }
    else:
        # 档案形态：直接给企业名（财汇自己会做匹配，简称也能命中）
        sub_arguments = {"target_company": [query]}
    sub_arguments.update(company_sub_args())
    sub_arguments.update(company_args_template())
    return {"tool_name": sub_tool, "arguments": sub_arguments}


def normalize_company_candidates(raw: Any) -> list[CompanyCandidate]:
    """把不同企业 MCP 的返回结构收敛成 CompanyCandidate 列表。

    支持三种形态（按可靠性排序）：
      1. dict 列表：每个 dict 自带 company_name 等字段（多数厂商）
      2. 财汇风格：`records.headInfo`（列定义）+ `records.data`（二维数组）
      3. 纯二维数组兜底：按内容特征猜列（老的企业预警通接口）

    业务失败（厂商用 HTTP 200 包了错误信封）直接抛 CompanyQueryError——
    否则错误信息里的**请求回显**（我们刚传过去的企业名）会被当成一个候选，
    而"单候选"在 company_service 里是会自动选中的，等于拿空壳企业继续走流程。
    """
    error = _find_business_error(raw)
    if error:
        raise CompanyQueryError(error)

    seen: set[str] = set()
    # 先按表头还原（财汇这类"列定义 + 二维数组"最准确），再退回通用的 dict 扫描
    candidates = _candidates_from_headinfo(raw, seen)
    if not candidates:
        candidates = _candidates_from_long_records(raw, seen)
    if not candidates:
        for node in _collect_dicts(raw, skip_ids=_column_definition_ids(raw)):
            candidate = _to_candidate(node, seen)
            if candidate is not None:
                candidates.append(candidate)
    if not candidates:
        candidates = _candidates_from_rows(raw, seen)
    return candidates


def _to_candidate(node: dict, seen: set) -> Optional[CompanyCandidate]:
    """把一个 dict 记录转成候选；企业名重复或缺失时返回 None。"""
    name = _pick(node, _NAME_KEYS)
    if not isinstance(name, str) or not name.strip():
        return None
    name = name.strip()
    if name in seen:
        return None
    seen.add(name)
    return CompanyCandidate(
        company_name=name,
        unified_social_credit_code=_pick(node, _CREDIT_KEYS),
        registered_address=_pick(node, _ADDRESS_KEYS),
        province=_pick(node, _PROVINCE_KEYS),
        city=_pick(node, _CITY_KEYS),
        legal_person=_pick(node, _LEGAL_KEYS),
        enterprise_nature=_pick(node, _NATURE_KEYS),
        registered_capital=_format_capital(_pick(node, _CAPITAL_KEYS)),
    )


def _candidates_from_headinfo(raw: Any, seen: set) -> list[CompanyCandidate]:
    """财汇：`records.headInfo` 是列定义（field/name），`records.data` 是二维数组。

    有表头就用表头把每行还原成 dict，再走同一套字段别名，别去猜列。
    """
    candidates: list[CompanyCandidate] = []
    for node in _walk_dicts(raw):
        header = node.get("headInfo")
        rows = node.get("data")
        if not isinstance(header, list) or not isinstance(rows, list):
            continue
        fields = [
            str((column or {}).get("field") or (column or {}).get("name") or "")
            for column in header
            if isinstance(column, dict)
        ]
        if not fields or not any(field in _NAME_KEYS for field in fields):
            continue
        # 长表（每行一个指标）交给 _candidates_from_long_records，别在这里按行拼
        if {"index_name", "index_value"} <= set(fields):
            continue
        for row in rows:
            if not isinstance(row, list):
                continue
            record = {
                fields[index]: row[index]
                for index in range(min(len(fields), len(row)))
            }
            candidate = _to_candidate(record, seen)
            if candidate is not None:
                candidates.append(candidate)
    return candidates


def _candidates_from_long_records(raw: Any, seen: set) -> list[CompanyCandidate]:
    """财汇"企业基本指标查询"：长表（每行一个指标）→ 按企业名透视成候选。

    真实返回形状：
        headInfo = [{field: company_name}, {field: index_name}, {field: index_value}]
        data     = [["贵州茅台酒股份有限公司", "注册资本(万元)", "125008.16"], ...]
    """
    candidates: list[CompanyCandidate] = []
    for node in _walk_dicts(raw):
        header = node.get("headInfo")
        rows = node.get("data")
        if not isinstance(header, list) or not isinstance(rows, list):
            continue
        fields = [
            str((column or {}).get("field") or "") for column in header if isinstance(column, dict)
        ]
        if fields != ["company_name", "index_name", "index_value"]:
            continue

        grouped: dict[str, dict] = {}
        for row in rows:
            if not isinstance(row, list) or len(row) < 3:
                continue
            name = str(row[0]).strip()
            key = _CN_INDICATOR_FIELDS.get(str(row[1]).strip())
            if not name or not key:
                continue
            grouped.setdefault(name, {"company_name": name})[key] = row[2]

        for record in grouped.values():
            # 所属地区形如"贵州省遵义市"：补一个省份，城市留给专门指标
            region = record.pop("region", None)
            if region and not record.get("province"):
                matched = re.match(r"(.{2,8}?(?:省|自治区|北京市|上海市|天津市|重庆市))", str(region))
                if matched:
                    record["province"] = matched.group(1)
            # 注册资本 + 币种拼成模板要的写法（663719.99万元人民币）
            currency = record.pop("capital_currency", None)
            if currency and record.get("registered_capital") is not None:
                formatted = _format_capital(record["registered_capital"])
                if formatted:
                    record["registered_capital"] = f"{formatted}{str(currency).strip()}"
            candidate = _to_candidate(record, seen)
            if candidate is not None:
                candidates.append(candidate)
    return candidates


def _walk_dicts(node: Any, depth: int = 0):
    """深度优先遍历所有 dict。"""
    if depth > 8:
        return
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk_dicts(value, depth + 1)
    elif isinstance(node, list):
        for value in node:
            yield from _walk_dicts(value, depth + 1)


def _collect_dicts(node: Any, depth: int = 0, skip_ids: Optional[set] = None):
    """深度优先收集「像企业记录」的 dict（自带你认识的字段名）。

    skip_ids 用来排除表头列定义那种"长得像记录"的 dict：
    `{"field": "company_name", "name": "企业名称", "type": "string"}` 里有 name 键，
    不排除的话会被当成一家叫"企业名称"的公司。
    """
    skip_ids = skip_ids or set()
    for item in _walk_dicts(node, depth):
        if id(item) in skip_ids:
            continue
        if any(key in item for key in _NAME_KEYS):
            yield item


def _column_definition_ids(raw: Any) -> set:
    """收集所有 headInfo 列定义 dict 的 id，供 _collect_dicts 排除。"""
    ids: set = set()
    for node in _walk_dicts(raw):
        header = node.get("headInfo")
        if isinstance(header, list):
            ids.update(id(column) for column in header if isinstance(column, dict))
    return ids


def _find_business_error(raw: Any) -> str:
    """识别业务失败信封，返回可读原因（没失败返回空串）。

    财汇的形状：{"status": {"code": ..., "message": "SUCCESS"/"PARAM_ERROR", "error": {...}}}。
    **不能只看 code**：code=1 是"成功，存在参数兼容处理，已按默认值继续查询"，
    数据是完整的；只有 message 不是 SUCCESS、或带 error 对象才是真失败。
    另外兼容 {"success": false} / {"isError": true} 这类通用写法。
    """
    for node in _walk_dicts(raw):
        status = node.get("status")
        # 只认"工具信封"那种 status：旁边还有 data / paramDetails / querySummary
        is_envelope = isinstance(status, dict) and any(
            key in node for key in ("data", "paramDetails", "querySummary")
        )
        if is_envelope:
            error = status.get("error")
            message = str(status.get("message") or "").strip()
            if isinstance(error, dict) and error:
                return str(error.get("message") or error.get("type") or "").strip()[:300]
            if message and message.upper() not in {"SUCCESS", "OK"}:
                return message[:300]
            continue
        if node.get("success") is False or node.get("isError") is True:
            return str(node.get("message") or node.get("error") or "厂商返回失败")[:300]
    return ""


def _candidates_from_rows(raw: Any, seen: set) -> list[CompanyCandidate]:
    """兜底：部分厂商（如企业预警通）返回的是「二维数组」，按特征猜列。

    只在结构化字段完全没命中时启用，且只认能明确识别的列：
    - 18 位统一社会信用代码 → 信用代码列
    - 含"省/自治区/直辖市" → 省份列
    - 最长的含地址特征的串 → 注册地址列
    """
    rows: list[list] = []

    def walk(node: Any, depth: int = 0):
        if depth > 8:
            return
        if isinstance(node, list):
            if node and all(isinstance(item, (str, int, float)) for item in node):
                rows.append(node)
            else:
                for item in node:
                    walk(item, depth + 1)
        elif isinstance(node, dict):
            for value in node.values():
                walk(value, depth + 1)

    walk(raw)
    candidates: list[CompanyCandidate] = []
    for row in rows:
        texts = [str(item) for item in row if isinstance(item, str)]
        name = next((t for t in texts if _looks_like_company(t)), None)
        if not name or name in seen:
            continue
        seen.add(name)
        candidates.append(
            CompanyCandidate(
                company_name=name,
                unified_social_credit_code=next(
                    (t.strip() for t in texts if _CREDIT_RE.match(t.strip())), None
                ),
                province=next((t for t in texts if _PROVINCE_RE.match(t)), None),
                registered_address=max(
                    (t for t in texts if any(ch in t for ch in "路号区街道")),
                    key=len,
                    default=None,
                ),
            )
        )
    return candidates


def _looks_like_company(text: str) -> bool:
    text = text.strip()
    if not (4 <= len(text) <= 60):
        return False
    return any(suffix in text for suffix in ("公司", "企业", "集团", "厂", "研究院", "中心", "银行"))


def _pick(node: dict, keys: tuple) -> Optional[str]:
    for key in keys:
        value = node.get(key)
        if value not in (None, "", [], {}):
            return str(value).strip()
    return None


def _format_capital(value: Optional[str]) -> Optional[str]:
    """注册资本补单位：财汇给的是数字（单位万元，如 5000），补成"5000万元"；
    其它厂商给的是原文（"5000万元人民币"）时原样返回。"""
    if value is None:
        return None
    text = str(value).strip()
    if re.fullmatch(r"\d+(\.\d+)?", text):
        number = float(text)
        shown = f"{number:.2f}".rstrip("0").rstrip(".")
        return f"{shown}万元"
    return text
