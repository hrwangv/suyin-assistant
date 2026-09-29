from langgraph.checkpoint.sqlite import SqliteSaver

DB_PATH = "agent_state.db"          # 你的 .db 文件路径
THREAD_ID = "7c5509c4-faba-4428-8966-b5b7d340909a"         # 要查看的会话 ID

with SqliteSaver.from_conn_string(DB_PATH) as checkpointer:
    config = {"configurable": {"thread_id": THREAD_ID}}

    # 1. 最新 checkpoint
    tup = checkpointer.get_tuple(config)
    if tup:
        print("=== 当前状态 ===")
        print(tup.checkpoint.get("channel_values", {}))
        print("=== metadata ===")
        print(tup.metadata)
        print("=== pending writes ===")
        print(tup.pending_writes)
    else:
        print("没有找到该 thread_id")

    # 2. 历史 checkpoint
    print("\n=== 历史记录 ===")
    for tup in checkpointer.list(config):
        print(tup.config)
        print(tup.checkpoint.get("channel_values", {}))