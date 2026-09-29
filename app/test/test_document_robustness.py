"""Document 子图健壮性自检（离线，不联网、不依赖 MySQL/Qdrant/PostgreSQL）。

用法：
    PYTHONPATH=. python app/test/test_document_robustness.py

覆盖三类"线上真出过问题"的路径：
1. OCR 失败三态：ocr_unavailable（没配）/ ocr_failed（配了但调用失败）/ ocr_empty_text（空文本）
2. COS 外链生命周期：首次上传 → 缓存复用 → 签名过期续签 → 对象被删自愈 → 上传失败报错
3. 复用路径的动作判定：模型判定优先，模型不可用退回关键词规则
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402

from app.agent.services import document_service, file_store  # noqa: E402
from app.agent.services.ocr_providers import OCRUnavailable  # noqa: E402
from app.agent.subgraphs import document_understanding as du  # noqa: E402
from app.agent.subgraphs.document_understanding import build_document_app  # noqa: E402

INQUIRY_TEXT = """询证函
致：江苏某某科技有限公司
截至 2026-08-31，贵公司欠本公司货款余额 1,234,567.89 元。
回函地址：江苏省南京市鼓楼区中山路1号苏银大厦12层
联系人：王会计    电话：025-88886666
"""


def _state(thread: str, attachments=None, task: str = "", action: str = "") -> dict:
    return {
        "session_id": thread,
        "thread_id": thread,
        "is_stream": False,
        "attachments": attachments or [],
        "document_task_text": task,
        "document_action": action,
    }


def _upload(name: str = "询证函.png", content: str = INQUIRY_TEXT):
    return file_store.save_upload(content.encode("utf-8"), name, "image/png")


class _OCRStub:
    """把 OCR 换成指定的行为（抛错 / 返回指定结果），退出时自动复原。"""

    def __init__(self, behavior):
        self.behavior = behavior

    def __enter__(self):
        self.original = du.run_ocr
        du.run_ocr = lambda file: self.behavior(file)
        return self

    def __exit__(self, *exc):
        du.run_ocr = self.original
        return False


def _ok_ocr(file):
    return {
        "document_id": file.file_id,
        "full_text": INQUIRY_TEXT,
        "pages": [{"page_number": 1, "text": INQUIRY_TEXT, "blocks": []}],
        "confidence": 0.0,
        "provider": "stub",
    }


# --------------------------------------------------------------------------
# 1. OCR 失败三态
# --------------------------------------------------------------------------
def test_ocr_unavailable():
    """没配 OCR MCP：document_error=ocr_unavailable，任务结果为 failed。"""
    app = build_document_app(InMemorySaver())
    upload = _upload()

    def boom(_file):
        raise OCRUnavailable("未配置 OCR MCP：填 builtin.py 里 ocr.url")

    with _OCRStub(boom):
        result = app.invoke(
            _state("t-ocr-unavailable", [upload.to_dict()], "提取邮寄地址"),
            {"configurable": {"thread_id": "t-ocr-unavailable"}},
        )

    assert result["document_error"].startswith("ocr_unavailable"), result["document_error"]
    assert result["document_task_result"]["status"] == "failed", result["document_task_result"]
    assert result["agent_result"]["status"] == "failed", result["agent_result"]
    print("[PASS] OCR 未配置 → ocr_unavailable → failed OK")


def test_ocr_failed():
    """配了但调用失败（超时 / 鉴权 / 厂商报错）：document_error=ocr_failed，保留原始报错。"""
    app = build_document_app(InMemorySaver())
    upload = _upload()

    def boom(_file):
        raise RuntimeError("OCR MCP 返回错误：请求超时。服务响应较慢")

    with _OCRStub(boom):
        result = app.invoke(
            _state("t-ocr-failed", [upload.to_dict()], "提取邮寄地址"),
            {"configurable": {"thread_id": "t-ocr-failed"}},
        )

    assert result["document_error"].startswith("ocr_failed"), result["document_error"]
    assert "请求超时" in result["document_error"], result["document_error"]
    assert result["document_task_result"]["status"] == "failed", result["document_task_result"]
    print("[PASS] OCR 调用失败 → ocr_failed（带原始报错）OK")


def test_ocr_empty_text():
    """OCR 返回空文本（图片糊了 / 厂商返回空）：document_error=ocr_empty_text。"""
    app = build_document_app(InMemorySaver())
    upload = _upload()

    def empty(file):
        return {
            "document_id": file.file_id,
            "full_text": "   \n  ",
            "pages": [],
            "confidence": 0.0,
            "provider": "stub",
        }

    with _OCRStub(empty):
        result = app.invoke(
            _state("t-ocr-empty", [upload.to_dict()], "提取邮寄地址"),
            {"configurable": {"thread_id": "t-ocr-empty"}},
        )

    assert result["document_error"] == "ocr_empty_text", result["document_error"]
    assert result["document_task_result"]["status"] == "failed", result["document_task_result"]
    print("[PASS] OCR 空文本 → ocr_empty_text OK")


# --------------------------------------------------------------------------
# 2. COS 外链生命周期
# --------------------------------------------------------------------------
class _FakeCos:
    """假 COS 客户端：只记账，不发网络请求。"""

    def __init__(self):
        self.uploads: list[str] = []
        self.signs: list[str] = []

    def upload_file(self, Bucket, Key, LocalFilePath, EnableMD5=False):
        self.uploads.append(Key)

    def get_presigned_url(self, Bucket, Key, Method, Expired):
        self.signs.append(Key)
        return f"https://cos.example.com/{Key}?sign={len(self.signs)}"


class _CosEnv:
    """替换 COS 客户端与可达性检查，退出时复原。"""

    def __init__(self, cos_client, reachable=True):
        self.cos_client = cos_client
        self.reachable = reachable
        self.checked: list[str] = []

    def __enter__(self):
        self.original_client = file_store._cos_client
        self.original_reachable = file_store._url_reachable
        file_store._cos_client = lambda: self.cos_client

        def fake_reachable(url):
            self.checked.append(url)
            ok = self.reachable if isinstance(self.reachable, bool) else self.reachable(len(self.checked))
            return ok, "200" if ok else "HTTP 403"

        file_store._url_reachable = fake_reachable
        return self

    def __exit__(self, *exc):
        file_store._cos_client = self.original_client
        file_store._url_reachable = self.original_reachable
        return False


def _meta_of(file) -> dict:
    return json.loads((Path(file.path).parent / "meta.json").read_text(encoding="utf-8"))


def test_cos_upload_and_cache():
    """首次调用：上传一次 + 签名一次 + 写进 meta；第二次调用直接命中缓存。"""
    upload = _upload()
    cos = _FakeCos()
    with _CosEnv(cos):
        first = file_store.public_url(upload)
        second = file_store.public_url(upload)

    assert len(cos.uploads) == 1, cos.uploads
    assert first == second, (first, second)
    assert _meta_of(upload)["public_url"] == first
    print("[PASS] COS 外链：首次上传 + 缓存复用（不重复上传）OK")


def test_cos_expired_signature_resigned():
    """签名过期：只重新签名，不重新上传。"""
    upload = _upload()
    cos = _FakeCos()
    with _CosEnv(cos):
        first = file_store.public_url(upload)
        meta = _meta_of(upload)
        meta["public_url_expires_at"] = 0  # 手动把它改成过期
        (Path(upload.path).parent / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False), encoding="utf-8"
        )
        second = file_store.public_url(upload)

    assert len(cos.uploads) == 1, f"过期不该重传，实际上传 {len(cos.uploads)} 次"
    assert len(cos.signs) == 2, cos.signs
    assert first != second, "续签应当拿到新的签名"
    print("[PASS] COS 外链：签名过期只续签、不重传 OK")


def test_cos_object_deleted_self_heals():
    """对象被删：缓存的外链拉不通 → 重传一次再签。"""
    upload = _upload()
    cos = _FakeCos()
    # 第一次（首轮上传）可达；第二次（读缓存时）不可达 → 触发自愈
    with _CosEnv(cos, reachable=lambda n: n != 2):
        file_store.public_url(upload)
        url = file_store.public_url(upload)

    assert len(cos.uploads) == 2, f"应当重传一次，实际上传 {len(cos.uploads)} 次"
    assert url.startswith("https://cos.example.com/"), url
    print("[PASS] COS 外链：对象被删自动重传自愈 OK")


def test_cos_upload_failure_raises():
    """上传失败 / COS 不可用：抛 PublicUrlUnavailable，由上层转成 ocr_unavailable。"""
    upload = _upload()

    class _Broken:
        def upload_file(self, **kwargs):
            raise RuntimeError("COS 网络抖动")

        def get_presigned_url(self, **kwargs):
            raise AssertionError("上传就失败了，不该走到签名")

    with _CosEnv(_Broken()):
        try:
            file_store.public_url(upload)
            raise AssertionError("上传失败时应当抛 PublicUrlUnavailable")
        except file_store.PublicUrlUnavailable as exc:
            assert "COS" in str(exc), exc

    # 客户端都建不起来（没装 SDK / 没配密钥）
    original = file_store._cos_client
    file_store._cos_client = lambda: (_ for _ in ()).throw(
        file_store.PublicUrlUnavailable("COS 客户端不可用")
    )
    try:
        try:
            file_store.public_url(_upload())
            raise AssertionError("COS 不可用时应当抛 PublicUrlUnavailable")
        except file_store.PublicUrlUnavailable:
            pass
    finally:
        file_store._cos_client = original
    print("[PASS] COS 外链：上传失败 / 未配置 → PublicUrlUnavailable OK")


def test_ocr_url_mode_needs_cos():
    """url 形态下 COS 挂了：run_ocr 必须转成 OCRUnavailable，而不是发个空链接出去。"""
    from app.agent.services import ocr_providers

    upload = _upload()
    original_mode = ocr_providers.ocr_input_mode
    original_public = ocr_providers.public_url
    original_configured = ocr_providers.ocr_configured
    ocr_providers.ocr_configured = lambda: True
    ocr_providers.ocr_input_mode = lambda: "url"
    ocr_providers.public_url = lambda _file: (_ for _ in ()).throw(
        file_store.PublicUrlUnavailable("COS 客户端不可用")
    )
    try:
        try:
            ocr_providers.run_ocr(upload)
            raise AssertionError("应当抛 OCRUnavailable")
        except OCRUnavailable as exc:
            assert "传不上公网" in str(exc), exc
    finally:
        ocr_providers.ocr_configured = original_configured
        ocr_providers.ocr_input_mode = original_mode
        ocr_providers.public_url = original_public
    print("[PASS] url 形态 + COS 不可用 → ocr_unavailable OK")


# --------------------------------------------------------------------------
# 3. 复用路径的动作判定（模型优先，规则兜底）
# --------------------------------------------------------------------------
def test_reuse_action_uses_model_first():
    """同一份文件换问题：模型判定优先（"法人是谁" 这类口语说法规则判不出来）。"""
    original = document_service.json_completion

    def fake_model(schema, prompt, **kwargs):
        """模拟真模型：认得出"法人/注册资本"这类口语说法（关键词表认不出）。"""
        task = prompt.split("用户目标：")[-1]  # 只看用户目标，不看模板正文
        if any(word in task for word in ("法人", "注册资本")):
            return schema(action="extract_company_info", reason="用户问企业主体信息")
        return schema(action="none", reason="看不出要做什么")

    document_service.json_completion = fake_model
    try:
        for task, expect in [
            ("这份文件的法人是谁", "extract_company_info"),
            ("看一下这家公司的注册资本", "extract_company_info"),
            ("把金额摘出来", "none"),
        ]:
            got = document_service.decide_action_from_task(task, "inquiry_letter")
            assert got == expect, f"{task!r} → {got}，期望 {expect}"
    finally:
        document_service.json_completion = original
    print("[PASS] 动作判定：模型优先（口语说法）OK")


def test_reuse_action_falls_back_to_rules():
    """模型不可用 / 判成 none：退回关键词规则，规则也判不出才 none。"""
    original = document_service.json_completion
    try:
        # 模型不可用（json_completion 返回 None）
        document_service.json_completion = lambda *a, **k: None
        assert document_service.decide_action_from_task("提取邮寄地址", "inquiry_letter") == "extract_mailing_address"
        assert document_service.decide_action_from_task("这份文件的法人是谁", "inquiry_letter") == "none"

        # 模型给了 none，但 task 明显指向地址 → 规则补上
        document_service.json_completion = lambda schema, prompt, **k: schema(action="none")
        assert document_service.decide_action_from_task("帮我提取回函地址", "inquiry_letter") == "extract_mailing_address"
    finally:
        document_service.json_completion = original
    print("[PASS] 动作判定：模型失败 / none → 规则兜底 OK")


def test_reuse_subgraph_does_not_reunderstand():
    """复用轮：不重跑理解，但动作按本轮 task 重新判定。"""
    app = build_document_app(InMemorySaver())
    thread = "t-reuse-action"
    upload = _upload()

    with _OCRStub(_ok_ocr):
        first = app.invoke(
            _state(thread, [upload.to_dict()], "提取邮寄地址"),
            {"configurable": {"thread_id": thread}},
        )
    assert first["resolved_action"] == "extract_mailing_address", first["resolved_action"]

    original = document_service.json_completion
    document_service.json_completion = lambda schema, prompt, **kwargs: schema(
        action="extract_company_info", reason="用户问法人"
    )
    try:
        second = app.invoke(
            _state(thread, [], "这份文件的法人是谁"),
            {"configurable": {"thread_id": thread}},
        )
    finally:
        document_service.json_completion = original

    assert second["document_reuse"] is True, second.get("document_reuse")
    assert second["resolved_action"] == "extract_company_info", second["resolved_action"]
    assert second["document_task_result"]["action"] == "extract_company_info"
    print("[PASS] 复用轮：动作按本轮 task 重新判定 OK")


def main() -> None:
    test_ocr_unavailable()
    test_ocr_failed()
    test_ocr_empty_text()
    test_cos_upload_and_cache()
    test_cos_expired_signature_resigned()
    test_cos_object_deleted_self_heals()
    test_cos_upload_failure_raises()
    test_ocr_url_mode_needs_cos()
    test_reuse_action_uses_model_first()
    test_reuse_action_falls_back_to_rules()
    test_reuse_subgraph_does_not_reunderstand()
    print("\nDocument 健壮性自检全部通过 ✅")


if __name__ == "__main__":
    main()
