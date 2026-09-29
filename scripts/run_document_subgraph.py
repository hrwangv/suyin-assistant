"""单独跑 Document 子图：不经过主 Agent、不经过 Supervisor，便于定位问题。

它在链路上的位置（本脚本只跑中间这一段）：

    Supervisor → [document_skill 子图] → Supervisor
      ↑ 本脚本不跑                      ↑ 本脚本不跑

    子图内部：node_prepare_document → node_understand_document
              → node_confirm_address（仅地址歧义时 interrupt）→ node_finalize_document

三种输入方式
------------
    # 1) 直接喂 OCR 正文（不调 OCR，只测理解与动作判定，最常用）
    PYTHONPATH=. python scripts/run_document_subgraph.py --text-file 询证函.txt --task "提取邮寄地址"

    # 2) 命令行直接给短文本
    PYTHONPATH=. python scripts/run_document_subgraph.py --text "询证函…回函地址：南京市…" --task "总结"

    # 3) 走真实 OCR（需要配好 OCR MCP + COS，会真的联网）
    PYTHONPATH=. python scripts/run_document_subgraph.py --file 询证函.png --task "提取邮寄地址"

查看与调试
----------
    --json              打印完整 State（默认只打印关键块）
    --history           打印该 thread 的检查点历史（每一步写到哪、下一步是谁）
    --mermaid           打印子图结构
    --checkpoint sqlite:output/agent_state.db   换检查点（默认 memory，进程结束即丢）
    --resume 1          续跑被 interrupt 的地址确认（HITL）

多轮复用（同一份文件换问题：不重新 OCR、不重新理解）
----------------------------------------------------
    第一轮：--thread t1 --file 询证函.png --task "提取邮寄地址"
    第二轮：--thread t1 --task "总结一下这份文件" --reuse

    注意：用默认 memory 检查点时，两轮必须在同一个进程里；跨进程请配 sqlite。
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langgraph.types import Command  # noqa: E402

from app.agent.checkpoint import build_checkpointer  # noqa: E402
from app.agent.services.file_store import save_upload  # noqa: E402
from app.agent.subgraphs import document_understanding as du  # noqa: E402


def _install_text_stub(text: str):
    """把 OCR 换成"直接返回这段文本"，其余链路（理解 / 路由 / 收口）都跑真的。"""
    original = du.run_ocr

    def fake_ocr(file):
        return {
            "document_id": file.file_id,
            "full_text": text,
            "pages": [{"page_number": 1, "text": text, "blocks": []}],
            "confidence": 0.0,
            "provider": "stub",
        }

    du.run_ocr = fake_ocr
    return original


def _load_text(args) -> str:
    if args.text:
        return args.text
    if args.text_file:
        return Path(args.text_file).read_text(encoding="utf-8")
    return ""


def _build_attachments(args, text: str) -> list:
    """--file 走真实 OCR（附件交给子图）；否则把文本包成一个假附件。"""
    if args.file:
        path = Path(args.file)
        if not path.exists():
            raise SystemExit(f"找不到附件：{path}")
        return [save_upload(path.read_bytes(), path.name).to_dict()]
    if text:
        upload = save_upload(text.encode("utf-8"), "ocr_text.txt", "text/plain")
        return [upload.to_dict()]
    return []


def _print_summary(state: dict) -> None:
    """只打印人看的那几块：本轮动作、任务结论、文件事实、错误。"""
    print("\n=== 本轮结论 ===")
    print(f"  文档类型   : {state.get('document_type') or '（未识别）'}")
    print(f"  本轮动作   : {state.get('resolved_action') or '（未判定）'}")
    print(f"  是否复用   : {state.get('document_reuse')}")
    if state.get("document_error"):
        print(f"  错误       : {state['document_error']}")

    raw = state.get("document_ocr_raw") or {}
    if raw:
        print(
            f"  OCR        : provider={raw.get('provider')} "
            f"chars={len(raw.get('full_text') or '')} confidence={raw.get('confidence')}"
        )

    result = state.get("document_task_result") or {}
    if result:
        print("\n=== task_result ===")
        print(f"  status     : {result.get('status')}")
        print(f"  action     : {result.get('action')}")
        if result.get("reason"):
            print(f"  reason     : {result.get('reason')}")
        if result.get("data"):
            print(f"  data       : {json.dumps(result['data'], ensure_ascii=False)}")

    facts = state.get("document_facts") or {}
    if facts:
        print("\n=== document_facts ===")
        for key, value in facts.items():
            if value not in (None, "", [], {}):
                shown = json.dumps(value, ensure_ascii=False)
                print(f"  {key:<18}: {shown[:120]}")

    upstream = state.get("agent_result") or {}
    if upstream:
        print("\n=== 上行给主 Agent 的 AgentResult ===")
        print(f"  status     : {upstream.get('status')}")
        print(f"  message    : {upstream.get('message')}")


def main() -> None:
    parser = argparse.ArgumentParser(description="单独运行 Document 子图")
    parser.add_argument("--thread", default="doc-cli", help="thread_id（多轮复用靠它）")
    parser.add_argument("--task", default="", help="用户目标陈述（主 Agent 平时给的那句话）")
    parser.add_argument("--action", default="", help="显式指定动作（测试通道，优先于 task 判定）")
    parser.add_argument("--text", default="", help="直接给 OCR 正文")
    parser.add_argument("--text-file", default="", help="从文件读 OCR 正文")
    parser.add_argument("--file", default="", help="真实附件路径（走 OCR MCP）")
    parser.add_argument("--reuse", action="store_true", help="不带附件，复用同 thread 已理解的文件")
    parser.add_argument("--resume", default="", help="HITL 续跑值（例如地址序号 1）")
    parser.add_argument("--checkpoint", default="", help="memory / sqlite:<路径>")
    parser.add_argument("--json", action="store_true", help="打印完整 State")
    parser.add_argument("--history", action="store_true", help="打印检查点历史")
    parser.add_argument("--mermaid", action="store_true", help="打印子图结构")
    args = parser.parse_args()

    app = du.build_document_app(build_checkpointer(args.checkpoint or None))
    config = {"configurable": {"thread_id": args.thread}}

    if args.mermaid:
        print(app.get_graph().draw_mermaid())

    text = _load_text(args)
    restore = _install_text_stub(text) if text else None
    try:
        if args.resume:
            payload = Command(resume=args.resume)
        else:
            payload = {
                "session_id": args.thread,
                "thread_id": args.thread,
                "is_stream": False,
                "attachments": [] if args.reuse else _build_attachments(args, text),
                "document_task_text": args.task,
                "document_action": args.action,
            }
        state = app.invoke(payload, config)
    finally:
        if restore is not None:
            du.run_ocr = restore

    _print_summary(state)

    if state.get("document_error") == "no_attachment":
        print(
            "\n[提示] 没找到可复用的理解结果。--reuse 依赖同 thread 的历史：\n"
            "       用默认 memory 检查点时两轮必须在同一进程里（见 --history 确认有没有历史），\n"
            "       跨进程复用请加 --checkpoint sqlite:output/agent_state.db"
        )

    if state.get("__interrupt__"):
        print("\n=== 停在这里了（HITL）===")
        for item in state["__interrupt__"]:
            print(f"  {getattr(item, 'value', item)}")
        print(f"  续跑：--thread {args.thread} --resume <你的选择>")

    if args.json:
        printable = {k: v for k, v in state.items() if k != "__interrupt__"}
        dumped = json.dumps(printable, ensure_ascii=False, indent=2, default=str)
        print("\n=== 完整 State ===\n" + dumped)

    if args.history:
        print("\n=== 检查点历史（新→旧）===")
        for snapshot in app.get_state_history(config):
            keys = sorted(k for k in snapshot.values if k != "__interrupt__")
            print(f"  step={snapshot.metadata.get('step')} next={snapshot.next} 写入键={keys[:8]}")


if __name__ == "__main__":
    main()
