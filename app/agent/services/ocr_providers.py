"""OCR 能力：只走外部 OCR MCP 一条通路。

连接信息（地址 / 传输方式 / 工具名 / 入参形态 / 鉴权头）全部声明在
`app/agent/mcp/builtin.py` 的 "ocr" 一条；本文件只负责"拿附件 → 调 OCR → 统一结构"。

失败只有两种，都由 Document 子图转成 `document_error`：
    OCRUnavailable  没有配 OCR MCP（缺 url）→ ocr_unavailable，反问用户 / 稍后再试
    其它异常        配了但调用失败（网络 / 鉴权 / 厂商报错）→ ocr_failed，带原始报错

设计取舍：不再有本地兜底（原「图片走 VL 模型、PDF/Office 走 MinerU」已移除）。
好处是链路只有一条、行为可预期；代价是 OCR MCP 不可用时文档链路直接失败。
"""
from app.agent.mcp.ocr_client import call_ocr, ocr_configured, ocr_input_mode
from app.agent.services.file_store import PublicUrlUnavailable, UploadedFile, public_url
from app.core.logger import logger


class OCRUnavailable(RuntimeError):
    """没有可用的 OCR MCP 配置。"""


def run_ocr(file: UploadedFile) -> dict:
    """调用 OCR MCP，返回统一结构（document_id / full_text / pages / confidence）。

    厂商入参形态由 builtin.py 的 adapter.input_mode 决定：
        base64 / path → 直接用本地文件
        url           → 先把附件传到对象存储换成公网 URL（外部服务自己来拉图）
    """
    if not ocr_configured():
        raise OCRUnavailable(
            "未配置 OCR MCP：填 app/agent/mcp/builtin.py 里 ocr.url，"
            "或设置环境变量 OCR_MCP_BASE_URL（密钥见 OCR_MCP_API_KEY）"
        )

    file_url = None
    if ocr_input_mode() == "url":
        try:
            file_url = public_url(file)
        except PublicUrlUnavailable as exc:
            raise OCRUnavailable(
                f"OCR MCP 的入参是图片链接，但附件传不上公网：{exc}"
            ) from exc

    logger.info(f"[ocr] 调用 OCR MCP 处理 {file.filename}（入参形态={ocr_input_mode()}）")
    return call_ocr(
        file_path=file.path,
        mime_type=file.mime_type,
        file_id=file.file_id,
        file_url=file_url,
    )
