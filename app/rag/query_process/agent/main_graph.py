from langgraph.graph import StateGraph, END

from app.rag.query_process.agent.nodes.node_answer_output import node_answer_output
from app.rag.query_process.agent.nodes.node_item_name_confirm import node_item_name_confirm
from app.rag.query_process.agent.nodes.node_rerank import node_rerank
from app.rag.query_process.agent.nodes.node_rrf import node_rrf
from app.rag.query_process.agent.nodes.node_search_embedding import node_search_embedding
from app.rag.query_process.agent.nodes.node_search_embedding_hyde import node_search_embedding_hyde
from app.rag.query_process.agent.nodes.node_web_search_mcp import node_web_search_mcp
from app.rag.query_process.agent.state import QueryGraphState

builder = StateGraph(QueryGraphState)

# 节点添加完毕！！
builder.add_node("node_item_name_confirm",node_item_name_confirm)
builder.add_node("node_search_embedding",node_search_embedding)
builder.add_node("node_search_embedding_hyde",node_search_embedding_hyde)
builder.add_node("node_web_search_mcp",node_web_search_mcp)
builder.add_node("node_rrf",node_rrf)
builder.add_node("node_rerank",node_rerank)
builder.add_node("node_answer_output",node_answer_output)

# 添加边
builder.set_entry_point("node_item_name_confirm")

# node_item_name_confirm 只负责提取主体并重写问题，不再根据 item_name 置信度提前结束。
# 是否值得回答、是否需要澄清，交给后续检索、rerank 和答案生成节点处理。
# 条件边！！！ conditional_edges

def route_after_node_item_name_confirm(state: QueryGraphState):
    # 不再用 state 中的 answer 做入口短路，统一进入多路召回。
    return "node_search_embedding","node_search_embedding_hyde","node_web_search_mcp"

builder.add_conditional_edges("node_item_name_confirm"
                              , route_after_node_item_name_confirm,
                              {
                                  "node_answer_output":"node_answer_output",
                                  "node_search_embedding":"node_search_embedding",
                                  "node_search_embedding_hyde":"node_search_embedding_hyde",
                                  "node_web_search_mcp":"node_web_search_mcp"
                              })

builder.add_edge("node_search_embedding","node_rrf")
builder.add_edge("node_search_embedding_hyde","node_rrf")
builder.add_edge("node_web_search_mcp","node_rrf")
builder.add_edge("node_rrf","node_rerank")
builder.add_edge("node_rerank","node_answer_output")
builder.add_edge("node_answer_output",END)

query_app = builder.compile()
