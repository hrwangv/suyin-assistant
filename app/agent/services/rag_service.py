"""Knowledge Skill：复用老 RAG 全链路，一次跑到底。

Agent 的知识问答**不另起一条检索链路**，而是直接复用已经调优、并且被评测对齐的
`query_app`（`app/rag/query_process/agent/main_graph.py`）：

    node_item_name_confirm（Query Analyzer：改写 + 结构化条件 + 写用户消息）
        ↓
    node_search_embedding ┐
    node_search_embedding_hyde ├→ node_rrf → node_rerank
    node_web_search_mcp   ┘            ↓
                            node_answer_output
                    （长期记忆 + answer_out.prompt + 图片回传 + 写记忆 + 触发长期抽取）

这样 `/query` 与「统一入口的知识问答」跑的是同一张编译图，答案逐字一致。

"""
from app.rag.query_process.agent.state import create_query_default_state


def answer_with_legacy_chain(
    query: str,
    session_id: str,
    user_id: str = "",
    is_stream: bool = False,
) -> dict:
    """把整条老 RAG 链路跑到底，返回最终答案与证据。

    :return: {answer, documents, rewritten_query, filters, image_urls}
    """
    # 延迟导入：main_graph 在导入时就编译好图，放在函数里可以避免导入期开销，
    # 也方便测试直接替换 main_graph.query_app。
    from app.rag.query_process.agent.main_graph import query_app

    state = create_query_default_state(
        session_id=session_id,
        user_id=user_id or "",
        original_query=query,
        is_stream=is_stream,
    )
    final_state = query_app.invoke(state)
    docs = final_state.get("reranked_docs") or []
    return {
        "answer": final_state.get("answer") or "",
        "documents": [_to_evidence(doc) for doc in docs],
        "rewritten_query": final_state.get("rewritten_query") or query,
        "filters": final_state.get("retrieval_filters") or {},
        "image_urls": final_state.get("image_urls") or [],
    }


def _to_evidence(doc: dict) -> dict:
    """把检索结果映射成证据结构（保留原字段便于前端溯源展示）。"""
    text = doc.get("text") or ""
    return {
        "content": text,
        "date": doc.get("news_date") or doc.get("date"),
        "category": doc.get("category"),
        "domain": doc.get("domain"),
        "company": doc.get("item_name") or doc.get("company_name"),
        "score": doc.get("score"),
        # 溯源展示需要的原始字段
        "text": text,
        "title": doc.get("title"),
        "source": doc.get("source"),
        "file_title": doc.get("file_title"),
        "section": doc.get("section"),
        "chunk_id": doc.get("chunk_id"),
        "url": doc.get("url"),
    }
