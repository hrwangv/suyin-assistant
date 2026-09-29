"""查看检查点 sqlite 文件（output/agent_state.db）里存了什么。

为什么不能直接 SELECT：State 在 `checkpoints.checkpoint` 这个 BLOB 里，
用 msgpack 序列化过，裸查只能看到二进制长度。本脚本负责解码并打印人看得懂的部分。

用法
----
    # 1) 概览：文件在哪、有哪些会话、各存了多少步
    PYTHONPATH=. python scripts/inspect_checkpoint_db.py

    # 2) 某个会话的检查点时间线（每一步写到哪、下一步是谁）
    PYTHONPATH=. python scripts/inspect_checkpoint_db.py --thread sqlite-check

    # 3) 最新 State 的关键字段（人看的）
    PYTHONPATH=. python scripts/inspect_checkpoint_db.py --thread sqlite-check --state

    # 4) 完整 State（JSON，排查细节用）
    PYTHONPATH=. python scripts/inspect_checkpoint_db.py --thread sqlite-check --json

    # 5) 看 writes（节点写入 / HITL 中断值都在这里）
    PYTHONPATH=. python scripts/inspect_checkpoint_db.py --thread sqlite-check --writes

    # 6) 换一个库文件（默认取 .env 里 AGENT_CHECKPOINT 配的那个）
    PYTHONPATH=. python scripts/inspect_checkpoint_db.py --db output/other.db
"""
import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

from app.utils.path_util import PROJECT_ROOT  # noqa: E402

# 必须显式加载 .env：脚本不经过任何会 load_dotenv 的模块时，AGENT_CHECKPOINT 读不到
load_dotenv(PROJECT_ROOT / ".env")

# 人看的关键字段（--state 只打印这些，其余用 --json）
_HIGHLIGHT = (
    "thread_id",
    "request_text",
    "resolved_action",
    "document_type",
    "document_error",
    "candidate_addresses",
    "preferred_address",
    "application_phase",
    "template_type",
    "missing_fields",
    "generated_file_name",
    "generated_this_turn",
    "answer",
    "error",
    "interrupt_reason",
    "interrupt_payload",
)


def _default_db_path() -> Path:
    """默认用 .env 里 AGENT_CHECKPOINT 配的库，保持"看到的 === 服务在用的"。

    路径按**项目根目录**解析（与 checkpoint.resolve_sqlite_path 同一套逻辑），
    所以在 page/ 之类的子目录下执行也能找到文件。
    """
    from app.agent.checkpoint import ENV_KEY, resolve_sqlite_path

    spec = (os.getenv(ENV_KEY) or "").strip()
    if spec:
        return resolve_sqlite_path(spec)
    return resolve_sqlite_path("output/agent_state.db")


def _decode(serde, value_type: str, blob):
    if blob is None:
        return None
    try:
        return serde.loads_typed((value_type, blob))
    except Exception as exc:  # 解码失败不要整体崩
        return {"__decode_error__": f"{type(exc).__name__}: {exc}"}


def _load(conn, serde):
    rows = conn.execute(
        "SELECT thread_id, checkpoint_id, parent_checkpoint_id, type, checkpoint, metadata "
        "FROM checkpoints ORDER BY thread_id, checkpoint_id"
    ).fetchall()
    records = []
    for thread_id, cid, parent, ctype, blob, meta_blob in rows:
        envelope = _decode(serde, ctype, blob) or {}
        metadata = _decode(serde, "json", meta_blob) if meta_blob else {}
        records.append(
            {
                "thread_id": thread_id,
                "checkpoint_id": cid,
                "parent_checkpoint_id": parent,
                "step": (metadata or {}).get("step"),
                "source": (metadata or {}).get("source"),
                "state": (envelope or {}).get("channel_values") or {},
            }
        )
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description="查看检查点 sqlite 文件")
    parser.add_argument("--db", default="", help="库文件路径（默认取 AGENT_CHECKPOINT 里的）")
    parser.add_argument("--thread", default="", help="只看某个会话")
    parser.add_argument("--state", action="store_true", help="打印最新 State 的关键字段")
    parser.add_argument("--json", action="store_true", help="打印完整 State（JSON）")
    parser.add_argument("--writes", action="store_true", help="打印 writes 表（节点写入 / 中断值）")
    args = parser.parse_args()

    db_path = Path(args.db) if args.db else _default_db_path()
    if not db_path.is_absolute():
        db_path = PROJECT_ROOT / db_path
    if not db_path.exists():
        raise SystemExit(
            f"找不到库文件：{db_path}\n"
            f"  当前工作目录：{Path.cwd()}\n"
            f"  配置来源：.env 的 AGENT_CHECKPOINT（相对路径按项目根目录解析）\n"
            f"  项目根目录：{PROJECT_ROOT}\n"
            f"  提示：服务启动日志会打印实际路径；也可用 --db 显式指定"
        )

    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    serde = JsonPlusSerializer()
    conn = sqlite3.connect(str(db_path))
    print(f"库文件：{db_path}（{db_path.stat().st_size / 1024:.0f} KB）")

    if args.writes:
        if not args.thread:
            raise SystemExit("--writes 要配合 --thread 用")
        rows = conn.execute(
            "SELECT checkpoint_id, task_id, idx, channel, type, value FROM writes "
            "WHERE thread_id = ? ORDER BY checkpoint_id, task_id, idx",
            (args.thread,),
        ).fetchall()
        print(f"writes：{len(rows)} 条")
        for cid, task_id, idx, channel, vtype, blob in rows:
            value = _decode(serde, vtype, blob)
            shown = json.dumps(value, ensure_ascii=False, default=str)
            print(f"  step@{cid[:8]} task={task_id[:8]} #{idx} channel={channel} → {shown[:160]}")
        return

    records = _load(conn, serde)
    by_thread: dict[str, list[dict]] = {}
    for item in records:
        by_thread.setdefault(item["thread_id"], []).append(item)

    if not args.thread:
        print(f"\n共 {len(by_thread)} 个会话：")
        for thread_id, items in by_thread.items():
            latest = items[-1]
            state = latest["state"]
            mark = state.get("application_phase") or state.get("document_type") or "-"
            print(
                f"  {thread_id:<24} 检查点 {len(items):>3} 步  "
                f"最新 step={latest['step']}  阶段={mark}"
            )
        print("\n（加 --thread <id> 看时间线，--thread <id> --state 看最新状态）")
        return

    items = by_thread.get(args.thread)
    if not items:
        raise SystemExit(f"没有这个会话：{args.thread}（可用：{list(by_thread)}）")

    if args.json:
        latest = items[-1]
        print(f"\n=== 最新 State（step={latest['step']}，共 {len(items)} 步）===")
        print(json.dumps(latest["state"], ensure_ascii=False, indent=2, default=str)[:8000])
        return

    if args.state:
        latest = items[-1]
        state = latest["state"]
        print(f"\n=== 最新 State 关键字段（step={latest['step']}）===")
        for key in _HIGHLIGHT:
            if key in state and state[key] not in (None, "", [], {}):
                value = json.dumps(state[key], ensure_ascii=False, default=str)
                print(f"  {key:<22}: {value[:140]}")
        other = sorted(k for k in state if k not in _HIGHLIGHT and not k.startswith("__"))
        print(f"  （其余 {len(other)} 个键用 --json 看：{other[:8]}…）")
        return

    print(f"\n会话 {args.thread} 的检查点时间线（共 {len(items)} 步）：")
    for item in items:
        state = item["state"]
        keys = [k for k in ("application_phase", "document_type", "resolved_action") if state.get(k)]
        summary = " ".join(f"{k}={state[k]}" for k in keys) or "-"
        # checkpoint_id 前缀是时间戳（都一样），截后 8 位才好区分
        print(f"  step={str(item['step']):>3} id=…{item['checkpoint_id'][-8:]} {summary}")
    print("\n（加 --state 看最新状态，--json 看完整 State，--writes 看节点写入）")


if __name__ == "__main__":
    main()
