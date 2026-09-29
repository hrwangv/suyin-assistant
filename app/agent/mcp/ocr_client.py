"""OCR MCP 客户端：图片/PDF → 文本 + 版面 + confidence。

边界（规范第 16 节）：这里只做「OCR + 结果规范化」，
不做文档分类、不判断邮寄地址，那些属于 Document Skill 的语义理解。

连接信息（地址 / 鉴权 / 工具名 / 入参形态）全部来自代码声明
`app/agent/mcp/builtin.py` 的 ocr 一条，本文件不再读 .env。
"""
import base64
import mimetypes
from pathlib import Path
from typing import Any, Optional

from app.agent.mcp.client import MCPServerSpec, call_tool
from app.agent.mcp.registry import OCR_SERVER, get_server, resolve_tool_name, server_adapter
from app.conf.agent_config import agent_config
from app.core.logger import logger

_TEXT_KEYS = (
    "full_text", "fullText", "text", "ocrText", "markdown", "md",
    "ocr_text", "plain_text", "plainText", "content", "result",
)
_PAGE_KEYS = ("pages", "page_list", "pageList", "layouts", "layout")
_CONFIDENCE_KEYS = ("confidence", "avg_confidence", "avgConfidence", "score", "accuracy")
# 业务失败信封：有些厂商 HTTP 200，但 body 里是 {"success": false, "code": "TIMEOUT_ERROR"}
_ERROR_CODE_KEYS = ("code", "errorCode", "error_code", "errCode")
_ERROR_TEXT_KEYS = ("message", "error", "msg", "errmsg", "errorMessage", "error_message")


def ocr_spec() -> MCPServerSpec:
    """OCR MCP 的连接描述（地址等来自 builtin.py 的声明）。"""
    entry = get_server(OCR_SERVER)
    if entry is None:
        raise RuntimeError("OCR MCP 未在 app/agent/mcp/builtin.py 中声明")
    return entry.to_spec()


def ocr_configured() -> bool:
    return ocr_spec().configured


def ocr_tool_name() -> str:
    """OCR 工具名（声明里的 tools.recognize）。"""
    return resolve_tool_name(OCR_SERVER, "recognize", "ocr_document")


def ocr_input_mode() -> str:
    """送文件的形态：base64 / url / path（声明里的 adapter.input_mode）。"""
    return str(server_adapter(OCR_SERVER).get("input_mode") or "base64").strip().lower()


def ocr_args_template() -> dict:
    """厂商入参字段名覆盖（声明里的 adapter.args_template）。"""
    template = server_adapter(OCR_SERVER).get("args_template")
    return template if isinstance(template, dict) else {}


def call_ocr(
    file_path: str,
    mime_type: Optional[str] = None,
    file_id: str = "",
    file_url: Optional[str] = None,
) -> dict:
    """调用 OCR MCP 并返回规范化结果。"""
    spec = ocr_spec()
    arguments = build_ocr_arguments(file_path, mime_type, file_id, file_url)
    tool_name = ocr_tool_name()
    logger.info(
        f"[ocr_mcp] 调用 {tool_name}，文件={Path(file_path).name}，"
        f"参数键={sorted(arguments.keys())}"
    )
    raw = call_tool(spec, tool_name, arguments)
    result = normalize_ocr_payload(raw, file_id=file_id)
    if result.get("error"):
        raise RuntimeError(f"OCR MCP 返回错误：{result['error']}")
    return result


def build_ocr_arguments(
    file_path: str,
    mime_type: Optional[str] = None,
    file_id: str = "",
    file_url: Optional[str] = None,
) -> dict:
    """按配置的入参形态构造 OCR MCP 参数。

    各厂商 MCP 的入参名不一致，这里提供三种标准形态 + 一个 JSON 模板覆盖：
      base64 → file_base64 / file_name / mime_type
      url    → file_url / mime_type
      path   → file_path / mime_type
    """
    path = Path(file_path)
    mime = mime_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"

    input_mode = ocr_input_mode()
    if input_mode == "url":
        arguments: dict[str, Any] = {"file_url": file_url or "", "mime_type": mime}
    elif input_mode == "path":
        arguments = {"file_path": str(path), "mime_type": mime}
    else:
        arguments = {
            "file_base64": base64.b64encode(path.read_bytes()).decode("ascii"),
            "file_name": path.name,
            "mime_type": mime,
        }

    arguments["document_id"] = file_id or path.stem

    # 厂商入参覆盖（builtin.py 的 adapter.args_template）：字符串里的
    # {file_base64}/{file_path}/{file_url}/{mime_type}/{file_name} 会替换成实际值，
    # 方便适配企业自有 MCP 的字段名。
    template = ocr_args_template()
    if template:
        values = {
            "file_base64": arguments.get("file_base64", ""),
            "file_path": arguments.get("file_path", ""),
            "file_url": arguments.get("file_url", ""),
            "mime_type": mime,
            "file_name": path.name,
            "document_id": arguments["document_id"],
        }
        merged: dict[str, Any] = {}
        for key, value in template.items():
            if isinstance(value, str) and value.startswith("{") and value.endswith("}"):
                merged[key] = values.get(value[1:-1], "")
            else:
                merged[key] = value
        arguments.update(merged)
    return arguments


def normalize_ocr_payload(raw: Any, file_id: str = "") -> dict:
    """把不同 OCR MCP 的返回结构收敛成统一结构。

    统一结构（规范第 15 节）：
        {"document_id", "full_text", "pages": [...], "confidence"}
    """
    full_text = _find_text(raw) or ""
    pages = _find_pages(raw)
    confidence = _find_confidence(raw)

    normalized_pages = []
    for index, page in enumerate(pages or [], start=1):
        normalized_pages.append(_normalize_page(page, index))
    if not normalized_pages and full_text:
        normalized_pages = [{"page_number": 1, "text": full_text, "blocks": []}]

    return {
        "document_id": file_id or str(_find_value(raw, ("document_id", "doc_id", "id")) or ""),
        "full_text": full_text,
        "pages": normalized_pages,
        "confidence": confidence,
        "error": _find_error(raw),
        "raw": raw,
    }


def _walk(node: Any):
    """深度优先遍历 dict / list。"""
    yield node
    if isinstance(node, dict):
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def _find_text(node: Any) -> str:
    best = ""
    for item in _walk(node):
        if not isinstance(item, dict):
            continue
        for key in _TEXT_KEYS:
            value = item.get(key)
            if isinstance(value, str) and len(value) > len(best):
                best = value
    return best


def _find_pages(node: Any) -> list:
    for item in _walk(node):
        if not isinstance(item, dict):
            continue
        for key in _PAGE_KEYS:
            value = item.get(key)
            if isinstance(value, list) and value and isinstance(value[0], (dict, list)):
                return value
    return []


def _find_confidence(node: Any) -> float:
    for item in _walk(node):
        if not isinstance(item, dict):
            continue
        for key in _CONFIDENCE_KEYS:
            value = item.get(key)
            if isinstance(value, (int, float)):
                return _as_ratio(float(value))
    return 0.0


def _find_error(node: Any) -> str:
    """识别"HTTP 200 + 业务失败"的信封，例如 {"success": false, "error": "请求超时"}。

    没有这一层的话，失败会被当成"识别到 0 字"，把厂商的错因盖掉。
    """
    for item in _walk(node):
        if not isinstance(item, dict):
            continue
        code = next(
            (str(item[key]) for key in _ERROR_CODE_KEYS if item.get(key)), ""
        )
        failed = item.get("success") is False or item.get("isError") is True
        if not failed and not code.upper().endswith(("_ERROR", "_FAILED")):
            continue
        text = next(
            (
                item[key]
                for key in _ERROR_TEXT_KEYS
                if isinstance(item.get(key), str) and item[key].strip()
            ),
            "",
        )
        return text or code or "厂商返回失败"
    return ""


def _find_value(node: Any, keys: tuple) -> Any:
    for item in _walk(node):
        if isinstance(item, dict):
            for key in keys:
                if item.get(key) not in (None, ""):
                    return item[key]
    return None


def _as_ratio(value: float) -> float:
    if value > 1.0:
        return round(min(value / 100.0, 1.0), 4)
    return round(value, 4)


def _normalize_page(page: Any, index: int) -> dict:
    if isinstance(page, list):
        return {"page_number": index, "text": " ".join(str(x) for x in page), "blocks": []}
    if not isinstance(page, dict):
        return {"page_number": index, "text": str(page), "blocks": []}

    number = page.get("page_number") or page.get("page_no") or page.get("page") or index
    if isinstance(number, str) and number.isdigit():
        number = int(number)
    text = ""
    for key in _TEXT_KEYS:
        if isinstance(page.get(key), str):
            text = page[key]
            break

    blocks = []
    raw_blocks = page.get("blocks") or page.get("lines") or page.get("items") or []
    for block in raw_blocks if isinstance(raw_blocks, list) else []:
        if isinstance(block, dict):
            blocks.append(
                {
                    "text": block.get("text") or block.get("content") or "",
                    "bbox": block.get("bbox") or block.get("box") or block.get("poly"),
                    "confidence": block.get("confidence") or block.get("score"),
                }
            )
    return {"page_number": number if isinstance(number, int) else index, "text": text, "blocks": blocks}
