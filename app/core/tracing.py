"""Langfuse 链路追踪适配层（可选依赖，未启用时对业务完全透明）。

设计约束（改之前先读）：

1. **可选依赖**：没装 langfuse、没配密钥、或显式关闭时，本模块所有接口退化成直通——
   装饰器原样调用原函数、上下文管理器不建 span、`langchain_callbacks()` 返回空列表。
   业务的返回值和异常行为与接入前完全一致。
2. **密钥只在 .env 里**：本模块不硬编码密钥，任何日志都不打印密钥。
3. **不阻塞请求**：追踪走 OpenTelemetry 批量异步导出，请求路径上没有网络调用，
   只有进程退出或显式 `flush()` 时才把 buffer 落盘。
4. **默认脱敏**：默认对手机号 / 身份证号 / 16~19 位长数字做掩码，
   避免把客户信息原样送到境外 SaaS（苏银这类金融场景尤其重要）。

.env 开关（都有默认值，不配也能跑）：

    LANGFUSE_ENABLED          auto（默认）| true | false
                             auto = 只要配了 PUBLIC/SECRET KEY 就开启
    LANGFUSE_CAPTURE_CONTENT  true（默认）| false
                             false = 只上报 span 结构、耗时、token 与指标，
                             不上报 prompt / 答案原文（LangChain 的 generation span 也会关掉）
    LANGFUSE_MASK_ENABLED     true（默认）| false  是否启用内置脱敏
    LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY / LANGFUSE_BASE_URL
                             由 langfuse SDK 自己读取（v4 支持 BASE_URL，也认 HOST）

自检（会真的联网调用一次 Langfuse，用来确认密钥和网络都通）：

    PYTHONPATH=. python -m app.core.tracing --check
"""
from __future__ import annotations

import atexit
import functools
import os
import re
import threading
from contextlib import contextmanager, nullcontext
from typing import Any, Callable, Iterator

from dotenv import load_dotenv

# 必须在导入 langfuse 之前加载 .env：SDK 在构造客户端时才读 LANGFUSE_*，
# 而 langfuse 包自身不会去读 .env（包内没有 dotenv 调用）。
load_dotenv()

from app.core.logger import logger

_TRUE = {"1", "true", "yes", "on", "y"}
_FALSE = {"0", "false", "no", "off", "n"}


def _flag(name: str, default: bool) -> bool:
    """布尔开关：无法识别的值一律回退到默认值，绝不因为写错而意外开启/关闭。"""
    raw = (os.getenv(name) or "").strip().lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    return default


# 内容上报与脱敏在导入期就要确定：装饰器需要用它决定 capture_input / capture_output。
_CAPTURE_CONTENT: bool = _flag("LANGFUSE_CAPTURE_CONTENT", True)
_MASK_ENABLED: bool = _flag("LANGFUSE_MASK_ENABLED", True)

# ---------------------------------------------------------------- #
# 单例状态
# ---------------------------------------------------------------- #

_lock = threading.Lock()
_initialized = False
_client: Any = None
_observe_impl: Callable[..., Any] | None = None
_callback_handler: Any = None
_propagate_impl: Callable[..., Any] | None = None
_status_reason: str = ""


# ---------------------------------------------------------------- #
# 内置脱敏
# ---------------------------------------------------------------- #

_PHONE_RE = re.compile(r"(?<!\d)(1[3-9]\d{9})(?!\d)")
_ID_CARD_RE = re.compile(
    r"(?<!\d)(\d{6}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx])(?!\d)"
)
_LONG_DIGITS_RE = re.compile(r"(?<!\d)(\d{16,19})(?!\d)")


def _mask_text(text: str) -> str:
    text = _PHONE_RE.sub(lambda m: f"{m.group(1)[:3]}****{m.group(1)[-4:]}", text)
    text = _ID_CARD_RE.sub(lambda m: f"{m.group(1)[:6]}********{m.group(1)[-4:]}", text)
    # 银行卡号 / 账号一类：保留首尾四位，中间打码
    text = _LONG_DIGITS_RE.sub(
        lambda m: f"{m.group(1)[:4]}{'*' * (len(m.group(1)) - 8)}{m.group(1)[-4:]}",
        text,
    )
    return text


def _mask_value(value: Any) -> Any:
    if isinstance(value, str):
        return _mask_text(value)
    if isinstance(value, dict):
        return {key: _mask_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_mask_value(item) for item in value]
    return value


def _mask_data(*, data: Any) -> Any:
    """langfuse 的 mask 钩子（关键字参数 data）。脱敏失败时原样返回，绝不影响上报。"""
    try:
        return _mask_value(data)
    except Exception:  # pragma: no cover - 兜底分支
        return data


# ---------------------------------------------------------------- #
# 初始化
# ---------------------------------------------------------------- #


def _enabled_requested() -> tuple[bool, str]:
    mode = (os.getenv("LANGFUSE_ENABLED") or "auto").strip().lower()
    public_key = (os.getenv("LANGFUSE_PUBLIC_KEY") or "").strip()
    secret_key = (os.getenv("LANGFUSE_SECRET_KEY") or "").strip()
    if mode in _FALSE:
        return False, "LANGFUSE_ENABLED=false"
    if mode in _TRUE:
        return True, "LANGFUSE_ENABLED=true"
    if public_key and secret_key:
        return True, "auto（已配置密钥）"
    return False, "auto（未配置 LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY）"


def _ensure_ca_bundle() -> None:
    """给 OTLP span 导出补上 CA bundle。

    为什么需要：span 走 OpenTelemetry 的 OTLP HTTP 导出器（底层 urllib3/requests），
    它用的是系统默认 CA；而 macOS 上 python.org 装的 Python 默认没有可用 CA
    （需要跑 Install Certificates.command），表现为
    `CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate`，
    结果是 score 上报成功、trace 却一条都没有。

    这里按 OTel 官方配置项指到 certifi 的 CA bundle；用户若已显式配置则不覆盖。
    """
    if os.getenv("OTEL_EXPORTER_OTLP_TRACES_CERTIFICATE") or os.getenv(
        "OTEL_EXPORTER_OTLP_CERTIFICATE"
    ):
        return
    try:
        import certifi

        bundle = certifi.where()
    except Exception:  # pragma: no cover - 兜底分支
        return
    if bundle and os.path.exists(bundle):
        os.environ.setdefault("OTEL_EXPORTER_OTLP_CERTIFICATE", bundle)


def _do_init() -> None:
    global _client, _observe_impl, _callback_handler, _propagate_impl, _status_reason

    requested, reason = _enabled_requested()
    if not requested:
        _status_reason = reason
        return

    try:
        from langfuse import Langfuse, get_client, observe as langfuse_observe, propagate_attributes
        from langfuse.langchain import CallbackHandler
    except Exception as exc:
        _status_reason = f"langfuse 不可用：{exc}"
        logger.warning(f"[tracing] 未安装或无法导入 langfuse，链路追踪已关闭：{exc}")
        return

    try:
        # 只在这里构造一次客户端（带上脱敏钩子）。之后统一走 get_client() 复用同一个
        # 单例，保证 LangChain CallbackHandler 拿到的也是同一份配置（含 mask）。
        _ensure_ca_bundle()
        Langfuse(mask=_mask_data if _MASK_ENABLED else None)
        _client = get_client()
    except Exception as exc:
        _status_reason = f"客户端初始化失败：{exc}"
        logger.warning(f"[tracing] Langfuse 客户端初始化失败，链路追踪已关闭：{exc}")
        return

    _observe_impl = langfuse_observe
    _callback_handler = CallbackHandler
    _propagate_impl = propagate_attributes
    _status_reason = reason
    atexit.register(flush)
    logger.info(
        f"[tracing] Langfuse 链路追踪已启用：base_url="
        f"{os.getenv('LANGFUSE_BASE_URL') or os.getenv('LANGFUSE_HOST') or '默认'}，"
        f"内容上报={_CAPTURE_CONTENT}，脱敏={_MASK_ENABLED}"
    )


def _ensure_init() -> None:
    global _initialized
    if _initialized:
        return
    with _lock:
        if _initialized:
            return
        try:
            _do_init()
        finally:
            # 初始化失败也只做一次，避免每个 span 都重试一遍
            _initialized = True


def is_enabled() -> bool:
    """追踪当前是否真正生效（想按开关跳过高成本埋点时用它）。"""
    _ensure_init()
    return _client is not None


def status() -> dict:
    """当前状态，排查「为什么没有 trace」时先看这个。"""
    _ensure_init()
    return {
        "enabled": _client is not None,
        "reason": _status_reason,
        "base_url": os.getenv("LANGFUSE_BASE_URL") or os.getenv("LANGFUSE_HOST") or "",
        "capture_content": _CAPTURE_CONTENT,
        "mask_enabled": _MASK_ENABLED,
    }


def get_client():
    """返回 langfuse 客户端；未启用时返回 None。"""
    _ensure_init()
    return _client


# ---------------------------------------------------------------- #
# 装饰器与 LangChain 接入
# ---------------------------------------------------------------- #


def observe(*dargs: Any, **dkwargs: Any) -> Callable:
    """`@observe(...)` / `@observe` 的追踪版装饰器；未启用时是纯直通。

    支持 langfuse 原生的 `name` / `as_type` / `capture_input` / `capture_output`
    等参数。`capture_input` / `capture_output` 未显式指定时跟随
    `LANGFUSE_CAPTURE_CONTENT`。
    """
    if len(dargs) == 1 and callable(dargs[0]) and not dkwargs:
        return observe()(dargs[0])

    def decorate(fn: Callable) -> Callable:
        cache: dict[str, Callable] = {}

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            _ensure_init()
            impl = _observe_impl
            if impl is None:
                return fn(*args, **kwargs)
            wrapped = cache.get("fn")
            if wrapped is None:
                merged = dict(dkwargs)
                merged.setdefault("capture_input", _CAPTURE_CONTENT)
                merged.setdefault("capture_output", _CAPTURE_CONTENT)
                wrapped = impl(**merged)(fn)
                cache["fn"] = wrapped
            return wrapped(*args, **kwargs)

        return wrapper

    return decorate


def langchain_callbacks() -> list:
    """给 LangChain / LangGraph 的 invoke / stream 用的 callback 列表。

    关闭内容上报时返回空列表：宁可丢掉 token 统计，也不把 prompt 原文送出去。
    """
    _ensure_init()
    if _callback_handler is None or not _CAPTURE_CONTENT:
        return []
    try:
        return [_callback_handler()]
    except Exception as exc:  # pragma: no cover - 兜底分支
        logger.warning(f"[tracing] 创建 LangChain callback 失败，本次调用不追踪：{exc}")
        return []


def llm_config(**extra: Any) -> dict:
    """LLM 调用点的 config：`client.invoke(prompt, config=llm_config())`。"""
    callbacks = langchain_callbacks()
    if not callbacks:
        return dict(extra)
    return {"callbacks": callbacks, **extra}


# ---------------------------------------------------------------- #
# span / trace 操作（未启用时全部是 no-op）
# ---------------------------------------------------------------- #


def _clean(mapping: dict) -> dict:
    return {key: value for key, value in mapping.items() if value not in (None, "", [], {})}


@contextmanager
def trace_scope(
    name: str,
    *,
    session_id: str | None = None,
    user_id: str | None = None,
    tags: list[str] | None = None,
    metadata: dict | None = None,
    input: Any = None,
    as_type: str = "span",
) -> Iterator[Any]:
    """开一条 trace 的根 span，并把 session / user / tags 挂到整条 trace 上。

    用法（请求入口 / 评测的一道题）：

        with trace_scope("query", session_id=sid, input=query):
            ...业务...
    """
    _ensure_init()
    client = _client
    if client is None:
        yield None
        return

    attrs = _clean(
        {
            "session_id": session_id,
            "user_id": user_id,
            "tags": tags,
            "trace_name": name,
        }
    )
    try:
        span_cm = client.start_as_current_observation(
            name=name,
            as_type=as_type,
            input=input if _CAPTURE_CONTENT else None,
            metadata=metadata or None,
        )
    except Exception as exc:  # pragma: no cover - 兜底分支
        logger.debug(f"[tracing] 创建 span 失败，本次不追踪：{exc}")
        yield None
        return

    propagate_cm = (
        _propagate_impl(**attrs) if (_propagate_impl is not None and attrs) else nullcontext()
    )
    with propagate_cm, span_cm as span:
        yield span


def update_current_span(**kwargs: Any) -> None:
    client = _client
    if client is None:
        return
    try:
        client.update_current_span(**kwargs)
    except Exception:  # pragma: no cover - 兜底分支
        pass


def update_current_trace(**kwargs: Any) -> None:
    client = _client
    if client is None:
        return
    try:
        client.update_current_trace(**kwargs)
    except Exception:  # pragma: no cover - 兜底分支
        pass


def score_current_trace(
    name: str,
    value: float | str,
    *,
    comment: str | None = None,
    data_type: str | None = None,
) -> None:
    """把指标写成当前 trace 的 score（评测结果回写 Langfuse 用这个）。"""
    client = _client
    if client is None:
        return
    try:
        client.score_current_trace(
            name=name,
            value=value,
            comment=comment,
            data_type=data_type,  # type: ignore[arg-type]
        )
    except Exception:  # pragma: no cover - 兜底分支
        pass


def flush() -> None:
    """把缓冲区里的 span 推给 Langfuse。进程退出、评测收尾、服务关闭时各调一次。"""
    client = _client
    if client is None:
        return
    try:
        client.flush()
    except Exception:  # pragma: no cover - 兜底分支
        pass


if __name__ == "__main__":
    import sys

    info = status()
    print("Langfuse 追踪状态：")
    for key, value in info.items():
        print(f"  {key} = {value!r}")
    if "--check" in sys.argv:
        client = get_client()
        if client is None:
            print("\n未启用追踪（检查 .env 里的 LANGFUSE_* 且 LANGFUSE_ENABLED 不为 false）")
            raise SystemExit(1)
        ok = client.auth_check()
        print(f"\nauth_check -> {ok}")
        flush()
        raise SystemExit(0 if ok else 1)
