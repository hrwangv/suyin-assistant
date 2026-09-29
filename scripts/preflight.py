"""联调前置检查：一条命令看清"现在能不能开始联调"。

检查三类东西：
    1. 配置就位     .env 里该有值的键有没有值（只看有没有，不打印密钥）
    2. 依赖连通     MySQL / Redis / Qdrant / LLM 网关 / 财汇 MCP / OCR MCP
    3. 资产就位     业务申请书模板、生成物目录、检查点模式

用法
----
    PYTHONPATH=. python scripts/preflight.py              # 全查（含联网）
    PYTHONPATH=. python scripts/preflight.py --offline    # 只查配置与资产，不联网

退出码：0 = 关键项都通过；1 = 有关键项失败（看输出里的 ❌）。
"""
import argparse
import os
import socket
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import dotenv_values  # noqa: E402

from app.conf.agent_config import agent_config  # noqa: E402

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


class Report:
    """收集检查结果，最后统一打印（关键项失败 → 退出码 1）。"""

    def __init__(self) -> None:
        self.rows: list[tuple[bool, str, str]] = []

    def section(self, title: str) -> None:
        """插一条分组标题（打印时按顺序还原）。"""
        self.rows.append((True, f"\n【{title}】", ""))

    def add(self, ok: bool, title: str, detail: str = "", critical: bool = True) -> bool:
        mark = "✅" if ok else ("❌" if critical else "⚠️")
        self.rows.append((ok, f"{mark} {title}", detail))
        return ok

    def print(self) -> int:
        for ok, title, detail in self.rows:
            print(f"{title}{('  —  ' + detail) if detail else ''}")
        failed = [title for ok, title, _ in self.rows if not ok and title.startswith("❌")]
        print()
        if failed:
            print(f"结论：{len(failed)} 项关键检查未通过，先解决再联调：")
            for item in failed:
                print("   ", item)
            return 1
        print("结论：关键项全部通过，可以开始联调 🚀")
        return 0


def _env(key: str) -> str:
    """环境变量优先，其次 .env（本地用 dotenv_values，不偷偷改 os.environ）。"""
    return (os.getenv(key) or "").strip() or (dotenv_values(ENV_PATH).get(key) or "").strip()


def _tcp_ok(url: str, default_port: int = 80, timeout: float = 2.0) -> tuple[bool, str]:
    """对 URL 里的 host:port 做一次 TCP 连接（不校验鉴权，只测通不通）。"""
    if not url:
        return False, "未配置"
    parsed = urlparse(url if "//" in url else f"//{url}")
    host = parsed.hostname or ""
    port = parsed.port or default_port
    if not host:
        return False, f"解析不出主机：{url[:40]}"
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, f"{host}:{port} 可连"
    except OSError as exc:
        return False, f"{host}:{port} 连不上（{type(exc).__name__}）"


def check_config(report: Report) -> None:
    report.section("配置")
    for key, note in (
        ("LLM_API_KEY", "主 Agent / 子图判定都靠它"),
        ("LLM_BASE_URL", "OpenAI 兼容网关"),
        ("LLM_MODEL_ID", ""),
        ("YJT_API_KEY", "财汇 MCP（企业信息 + 资讯共用）"),
        ("COS_SECRET_ID", "文档子图：附件换公网地址"),
        ("COS_SECRET_KEY", ""),
    ):
        report.add(bool(_env(key)), f"配置 {key}", note or "已配置")

    report.add(
        bool(_env("MYSQL_URL")), "配置 MYSQL_URL", "短期记忆 / 历史（连不上会降级）", critical=False
    )
    report.add(bool(_env("REDIS_URL")), "配置 REDIS_URL", "热缓存（连不上会降级）", critical=False)


def check_assets(report: Report) -> None:
    report.section("资产")
    templates = sorted(agent_config.templates_dir.glob("*.docx"))
    report.add(bool(templates), "申请书模板", f"{agent_config.templates_dir}：{[t.name for t in templates]}")
    report.add(True, "生成物目录", str(agent_config.outputs_dir), critical=False)
    check_checkpoint(report)


def check_checkpoint(report: Report) -> None:
    """检查点后端：配的是哪个、依赖装没装、PG 连不连得上。"""
    import importlib.util

    spec = (_env("AGENT_CHECKPOINT") or "memory").strip()
    kind = spec.split(":", 1)[0].lower() or "memory"

    if kind in {"memory", "inmemory"}:
        report.add(
            True,
            "检查点后端 memory",
            "进程内，重启即丢；多进程 / 重启后 HITL 会丢状态",
            critical=False,
        )
        return

    if kind in {"sqlite", "sqlite3"}:
        installed = importlib.util.find_spec("langgraph.checkpoint.sqlite") is not None
        report.add(
            installed,
            "检查点后端 sqlite",
            "依赖已装" if installed else "缺依赖：pip install langgraph-checkpoint-sqlite",
        )
        return

    report.add(
        False, "检查点后端", f"不认识的 AGENT_CHECKPOINT={spec!r}；可选 memory / sqlite:<路径>"
    )


def check_network(report: Report) -> None:
    report.section("连通性")
    ok, detail = _tcp_ok(_env("LLM_BASE_URL"), 443)
    report.add(ok, "LLM 网关", detail)

    ok, detail = _tcp_ok(_env("YJT_BASE_URL") or "https://mcp.finchina.com", 443)
    report.add(ok, "财汇 MCP 域名", detail)

    ok, detail = _tcp_ok(_env("QDRANT_URL"), 6333)
    report.add(ok, "Qdrant", detail + "（只有知识问答用）", critical=False)

    ok, detail = _tcp_ok(_env("MYSQL_URL"), 3306)
    report.add(ok, "MySQL", detail + "（连不上会降级，不影响文档/申请书）", critical=False)

    ok, detail = _tcp_ok(_env("REDIS_URL"), 6379)
    report.add(ok, "Redis", detail + "（连不上会降级）", critical=False)

    # 两个 MCP 真的能列工具，才算"接上了"
    from app.agent.mcp.client import describe_tools
    from app.agent.mcp.company_client import company_spec
    from app.agent.mcp.ocr_client import ocr_spec

    try:
        tools = describe_tools(ocr_spec())
        report.add(bool(tools), "OCR MCP（文档子图）", f"探测到 {len(tools)} 个工具")
    except Exception as exc:
        report.add(False, "OCR MCP（文档子图）", f"{type(exc).__name__}: {exc}")

    try:
        tools = describe_tools(company_spec())
        report.add(bool(tools), "财汇 MCP（申请书子图）", f"探测到 {len(tools)} 个工具")
    except Exception as exc:
        report.add(False, "财汇 MCP（申请书子图）", f"{type(exc).__name__}: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(description="联调前置检查")
    parser.add_argument("--offline", action="store_true", help="不联网，只查配置与资产")
    args = parser.parse_args()

    report = Report()
    check_config(report)
    check_assets(report)
    if not args.offline:
        check_network(report)
    else:
        report.section("连通性（--offline，已跳过）")

    raise SystemExit(report.print())


if __name__ == "__main__":
    main()
