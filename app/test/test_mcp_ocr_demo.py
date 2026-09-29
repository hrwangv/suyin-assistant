"""OCR MCP 最小验证脚本：只留"到底能不能用"这一条链路。

    读配置 → 连服务端列工具 → 调一次 OCR → 规范化输出 → 打印文本

用法
----
    # 1) 不联网：打印配置 + 跑一遍入参/出参的离线自检
    PYTHONPATH=. python3 app/test/test_mcp_ocr_demo.py

    # 2) 真连 MCP 调一次（需要网络；样张图不存在会自动生成一张）
    PYTHONPATH=. python3 app/test/test_mcp_ocr_demo.py --online

    # 3) 换样张 / 显式试别的工具（仅调试用）/ 直接给公网图片地址
    PYTHONPATH=. python3 app/test/test_mcp_ocr_demo.py --online --image /path/to/询证函.png
    PYTHONPATH=. python3 app/test/test_mcp_ocr_demo.py --online --tool GeneralOcrRecognition
    PYTHONPATH=. python3 app/test/test_mcp_ocr_demo.py --online --image-url https://.../a.png

配置来源与生产完全同源：`app/agent/mcp/builtin.py` 的 ocr 一条 + .env 里的密钥。
入参形态由该声明的 `adapter.input_mode` 决定；`url` 形态会先把附件传到对象存储
（腾讯云 COS）换成公网地址，因为这类厂商的工具只收"图片链接"。
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.agent.mcp.client import call_tool, describe_tools  # noqa: E402
from app.agent.mcp.ocr_client import (  # noqa: E402
    build_ocr_arguments,
    normalize_ocr_payload,
    ocr_input_mode,
    ocr_spec,
    ocr_tool_name,
)
from app.agent.services import file_store  # noqa: E402
from app.utils.path_util import PROJECT_ROOT  # noqa: E402

DEFAULT_IMAGE = PROJECT_ROOT / "output" / "agent_files" / "ocr_sample.png"
SAMPLE_LINES = [
    "询证函",
    "致：江苏某某科技有限公司",
    "截至 2026-08-31，贵公司欠本公司货款余额 1,234,567.89 元。",
    "回函地址：江苏省南京市鼓楼区中山路1号苏银大厦12层",
    "联系人：王会计    电话：025-88886666",
]


def print_config() -> bool:
    """打印项目配置里的 OCR MCP 连接信息（不打印密钥明文），返回是否配了地址。"""
    spec = ocr_spec()
    print("=== OCR MCP 配置（声明处：app/agent/mcp/builtin.py）===")
    print(f"  地址        : {spec.url or '（空 → 未配置，run_ocr 会抛 OCRUnavailable）'}")
    print(f"  传输 / 工具 : {spec.transport} / {ocr_tool_name()}")
    print(f"  入参形态    : {ocr_input_mode()}")
    print(
        f"  鉴权        : {spec.auth_header} "
        f"{'（已读到密钥）' if spec.api_key else '（未读到密钥：OCR_MCP_API_KEY 为空）'}"
    )
    return bool(spec.url)


def ensure_sample_image(path: Path) -> bool:
    """样张不存在时用 PIL 画一张假询证函，让 --online 开箱可跑。"""
    if path.exists():
        return True
    try:
        from PIL import Image, ImageDraw
    except Exception as exc:
        print(f"[SKIP] 没有样张图 {path}，PIL 也不可用（{exc}），请用 --image 指定")
        return False

    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (1000, 520), "white")
    draw = ImageDraw.Draw(image)
    font = _load_font(30)
    for index, line in enumerate(SAMPLE_LINES):
        draw.text((40, 40 + index * 80), line, fill="black", font=font)
    image.save(path)
    print(f"[INFO] 已生成测试样张：{path}")
    return True


def _load_font(size: int):
    """找一款带中文字形的字体：用 Pillow 默认字体的话汉字会画成方块，识别出来是乱码。"""
    from PIL import ImageFont

    candidates = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/Supplemental/Songti.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",  # Linux 常见中文字体
    ]
    for font_path in candidates:
        if Path(font_path).exists():
            try:
                return ImageFont.truetype(font_path, size)
            except Exception:
                continue
    try:
        return ImageFont.load_default(size=size)  # 兜底：中文会变方块
    except TypeError:  # 老版本 Pillow 的 load_default 不接受 size
        return ImageFont.load_default()


def list_remote_tools(spec) -> list:
    """连一次服务端，列工具 + 入参 schema（调不通时最有用的排查信息）。"""
    print(f"\n[1/4] 连接 {spec.url}")
    tools = describe_tools(spec)
    if not tools:
        print("      连不上 / 没返回工具，原因见上面的日志")
        return []
    for tool in tools:
        schema = json.dumps(tool["inputSchema"], ensure_ascii=False)
        print(f"      - {tool['name']}：{tool['description'][:60]}")
        print(f"        入参：{schema[:300]}")
    return tools


def pick_tool(tools: list, preferred: str) -> str:
    """定工具名：声明里的 tools.recognize（可用 --tool 显式覆盖）。

    业务链路只用这一个工具（方案 A），所以名字对不上时**直接报错**，
    不自动挑一个别的——否则配置写错了会被悄悄掩盖成"能跑但结果为空"。
    """
    names = [tool["name"] for tool in tools]
    if preferred in names:
        return preferred
    print(f"[FAIL] 配置的工具名 {preferred} 不在服务端暴露的工具里")
    print(f"       服务端实际暴露：{names}")
    print("       确认 builtin.py 的 tools.recognize；只想临时试别的工具就加 --tool 名字")
    return ""


def prepare_file_url(image: str, image_url: str) -> str:
    """url 形态需要公网地址：直接用 --image-url，或把本地图片传到 COS 换一个。"""
    if image_url:
        print(f"[2/4] 直接用给定的图片地址：{image_url}")
        return image_url
    path = Path(image)
    if str(path) == str(DEFAULT_IMAGE):
        ensure_sample_image(path)
    if not path.exists():
        print(f"[SKIP] 找不到图片：{path}（用 --image 指定一张）")
        return ""
    uploaded = file_store.save_upload(path.read_bytes(), path.name)
    try:
        url = file_store.public_url(uploaded)
    except file_store.PublicUrlUnavailable as exc:
        print(f"[FAIL] 入参形态是 url，需要公网图片地址，但附件上传失败：{exc}")
        return ""
    print(f"[2/4] 附件已换成公网地址：{url}")
    return url


def run_online(args) -> bool:
    """真连一次：列工具 → 构造入参 → 调用 → 规范化 → 打印文本。"""
    spec = ocr_spec()
    if not spec.url:
        print("[SKIP] 没有 MCP 地址：填 builtin.py 的 ocr.url，或设 OCR_MCP_BASE_URL")
        return False
    if not spec.api_key:
        print("[WARN] 没有读到密钥：服务端若要求鉴权会返回 401/403，报错里能看到")

    tools = list_remote_tools(spec)
    if not tools:
        return False
    tool = pick_tool(tools, args.tool or ocr_tool_name())
    if not tool:
        return False

    mode = ocr_input_mode()
    file_url = ""
    image = args.image
    if mode == "url":
        file_url = prepare_file_url(image, args.image_url)
        if not file_url:
            return False
    elif not Path(image).exists():
        ensure_sample_image(Path(image))

    arguments = build_ocr_arguments(
        file_path=image,
        file_id=Path(image).stem,
        file_url=file_url or args.image_url,
    )
    printable = {
        # 签名 URL 的 query 里有账号与签名，打印时只留路径部分
        key: _mask(key, value)
        for key, value in arguments.items()
    }
    print(f"[3/4] 调用 {tool}，参数：{printable}")

    raw = call_tool(spec, tool, arguments, required=False)
    if not raw:
        print("[FAIL] 没有拿到结果（连接失败 / 服务端报错，看上面的日志）")
        return False

    result = normalize_ocr_payload(raw, file_id=Path(image).stem)
    text = result["full_text"] or ""
    if result.get("error"):
        print(f"[FAIL] 厂商返回业务失败：{result['error']}")
        print("       原始返回：" + json.dumps(raw, ensure_ascii=False)[:500])
        return False
    print(f"[4/4] 识别到 {len(text)} 字，置信度 {result['confidence']}")
    print("      " + (text[:500].replace("\n", "\n      ") or "（空）"))
    if not text:
        print("[诊断] 返回体里没有任何文本字段。原始返回：")
        print("      " + json.dumps(raw, ensure_ascii=False)[:800])
        print("      常见原因：入参字段名不对（对方要 image_url、pictureUrl 之类）；")
        print("               或图片地址外部拉不到（桶私有 → 必须用签名 URL）")
    return bool(text)


def _mask(key: str, value) -> str:
    """日志友好：base64 只报长度，签名 URL 去掉 query。"""
    text = str(value)
    if key.endswith("base64"):
        return f"<{len(text)} chars>"
    return text.split("?")[0] if "?" in text else text


def offline_self_check() -> None:
    """不联网的自检：入参构造（base64 / url 两种形态）+ 返回结构规范化。"""
    sample = "app/test/test_mcp_ocr_demo.py"
    args = build_ocr_arguments(
        sample, file_id="file_demo", file_url="https://example.com/a.png"
    )
    mode = ocr_input_mode()
    if mode == "url":
        assert args["file_url"] == "https://example.com/a.png", args
        # 厂商模板字段要能取到实际地址（例如 {"pictureUrl": "{file_url}"}）
        assert any("example.com" in str(value) for value in args.values()), args
    elif mode == "path":
        assert args["file_path"].endswith("test_mcp_ocr_demo.py"), args
    else:
        assert args["file_base64"], args
    print(f"[PASS] 离线自检：input_mode={mode} 的入参构造 OK")

    normalized = normalize_ocr_payload(
        {"data": {"ocrText": "询证函…", "confidence": 96}}, file_id="file_demo"
    )
    assert normalized["document_id"] == "file_demo"
    assert normalized["pages"][0]["page_number"] == 1
    assert 0.0 <= normalized["confidence"] <= 1.0
    print("[PASS] 离线自检：返回结构规范化 OK")


def main() -> None:
    parser = argparse.ArgumentParser(description="OCR MCP 最小验证")
    parser.add_argument("--online", action="store_true", help="真连 MCP 调一次（需要网络）")
    parser.add_argument("--tool", default="", help="工具名；不填用 builtin.py 的声明")
    parser.add_argument("--image", default=str(DEFAULT_IMAGE), help="本地图片路径")
    parser.add_argument("--image-url", default="", help="直接给公网图片地址（跳过本地上传）")
    args = parser.parse_args()

    print_config()
    print("=== 离线自检 ===")
    offline_self_check()

    if not args.online:
        print("\n（加 --online 可以连真实 MCP 跑一遍）")
        return
    print("\n=== 真实联调 ===")
    ok = run_online(args)
    print("\n在线调用结果：", "成功 ✅" if ok else "未成功（原因见上面的报错）")


if __name__ == "__main__":
    main()
