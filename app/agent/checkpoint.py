"""检查点（checkpointer）工厂：State 存哪里，只改这一个地方。

langgraph 的 checkpointer 决定「State 与 HITL 中断点存在哪」：

    InMemorySaver   进程内，重启即丢。当前默认——同一进程内的多轮对话 / interrupt→resume 够用
    SqliteSaver     单个文件，重启不丢。**本项目采用**：单机 / 单实例部署足够，
                    也支持跨进程的 HITL 恢复（同一台机器上）

切换只改环境变量，图结构不动：

    AGENT_CHECKPOINT=memory                                        # 默认
    AGENT_CHECKPOINT=sqlite:output/agent_state.db                  # 采用这个

sqlite saver 是可选依赖（已装）：

    pip install langgraph-checkpoint-sqlite

没装就用 sqlite 模式 → 抛 CheckpointConfigError，报错里带上面这条命令。

【为什么不上 Postgres】本项目是单实例部署，HITL 的 resume 总落在同一个进程上，
sqlite 已经够用且零运维。真要多副本部署时再引入共享检查点（那时需要 langgraph 的
PostgresSaver，或自己按 BaseCheckpointSaver 实现一套）。
"""
import os
from pathlib import Path
from typing import Any, Optional

from app.core.logger import logger
from app.utils.path_util import PROJECT_ROOT

ENV_KEY = "AGENT_CHECKPOINT"


class CheckpointConfigError(RuntimeError):
    """检查点模式写法不对，或缺少对应的可选依赖。"""


# sqlite 的底层连接必须活着，否则 saver 会在 GC 后失效
_connections: dict[str, Any] = {}


def resolve_sqlite_path(path: str) -> Path:
    """把 `sqlite:<路径>` 里的路径解析成绝对路径。

    **相对路径按项目根目录解析，而不是当前工作目录**：
    配置里写的是 `output/agent_state.db` 这种项目相对路径，而脚本可能从别的目录执行
    （例如在 page/ 下跑），按 CWD 解析会指向一个不存在的文件，表现为"找不到库文件"。
    """
    raw = path.strip().split(":", 1)[-1] if ":" in path else path.strip()
    target = Path(raw).expanduser()
    return target if target.is_absolute() else (PROJECT_ROOT / target)


def build_checkpointer(spec: Optional[str] = None) -> Any:
    """按 spec / 环境变量构造 checkpointer。

    :param spec: 形如 memory / sqlite:<路径>；为空时读 AGENT_CHECKPOINT
    """
    raw = (spec if spec is not None else os.getenv(ENV_KEY) or "memory").strip()
    kind, _, target = raw.partition(":")
    kind = (kind or "memory").strip().lower()

    if kind in {"memory", "inmemory"}:
        from langgraph.checkpoint.memory import InMemorySaver

        logger.info("[checkpoint] 使用 InMemorySaver（进程内，重启丢失）")
        return InMemorySaver()

    if kind in {"sqlite", "sqlite3"}:
        return _build_sqlite(target.strip())

    raise CheckpointConfigError(
        f"不认识的 {ENV_KEY}={raw!r}；可选 memory / sqlite:<路径>"
    )


def _build_sqlite(path: str) -> Any:
    """单文件持久化：重启进程后 State 与中断点都还在。"""
    if not path:
        raise CheckpointConfigError(
            "sqlite 模式要写文件路径，例如 AGENT_CHECKPOINT=sqlite:output/agent_state.db"
        )
    try:
        import sqlite3

        from langgraph.checkpoint.sqlite import SqliteSaver
    except ImportError as exc:
        raise CheckpointConfigError(
            "缺少依赖，先执行：pip install langgraph-checkpoint-sqlite"
        ) from exc

    target_path = resolve_sqlite_path(path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target = str(target_path)
    connection = _connections.setdefault(
        target, sqlite3.connect(target, check_same_thread=False)
    )
    logger.info(f"[checkpoint] 使用 SqliteSaver：{target}")
    return SqliteSaver(connection)
