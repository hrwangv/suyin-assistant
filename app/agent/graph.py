"""Main Agent 主图（规范第 77 节）。

    START → node_supervisor
              ├── knowledge   → node_knowledge    → END
              │                 （内部复用老 RAG 全链路，答案已产出，不再回 Supervisor）
              ├── document    → document_skill    → 回 node_supervisor（第二次决策）
              ├── application → application_skill → 回 node_supervisor（第二次决策）
              ├── answer      → node_answer       → END
              └── ask_user    → node_ask_user     → END

关于「为什么 document / application 跑完要再回 Supervisor 一次」：
子图只产出结构化结果（文档事实、任务结果、申请书阶段），"这几个结果够不够回答用户、
还要不要再补信息"属于 Main Agent 的判断，所以由 Supervisor 结合子图结果做第二次决策
（answer / ask_user）。为了防止来回打转，routing.route_after_supervisor 里有两道保护：
同类工具不重复调用（document_processed / application_processed）+ 决策次数上限。
"""
from langgraph.graph import END, START, StateGraph

from app.agent.checkpoint import build_checkpointer
from app.agent.nodes.node_answer import node_answer
from app.agent.nodes.node_ask_user import node_ask_user
from app.agent.nodes.node_knowledge import node_knowledge
from app.agent.nodes.node_supervisor import node_supervisor
from app.agent.routing import route_after_supervisor
from app.agent.state import AgentState
from app.agent.subgraphs.business_application import application_app
from app.agent.subgraphs.document_understanding import document_app
from app.core.logger import logger

# 检查点：按 .env 的 AGENT_CHECKPOINT 构造（本项目用 sqlite，文件落在 output/ 下）。
# 没配时退回 InMemorySaver（进程内）。图结构不用动（规范第 65 节）。
# 详见 app/agent/checkpoint.py。
_default_checkpointer = build_checkpointer()


def build_agent_graph(checkpointer=None):
    """编译 Main Agent 主图。"""
    builder = StateGraph(AgentState)
    builder.add_node("node_supervisor", node_supervisor)
    builder.add_node("node_knowledge", node_knowledge)
    builder.add_node("document_skill", document_app)
    builder.add_node("application_skill", application_app)
    builder.add_node("node_answer", node_answer)
    builder.add_node("node_ask_user", node_ask_user)

    builder.add_edge(START, "node_supervisor")
    builder.add_conditional_edges(
        "node_supervisor",# # ① 从哪个节点出发
        route_after_supervisor, # # ② 用哪个函数决定下一步
        { # # ③ 函数返回值 → 实际节点名的映射
            "node_knowledge": "node_knowledge",
            "document_skill": "document_skill",
            "application_skill": "application_skill",
            "node_answer": "node_answer",
            "node_ask_user": "node_ask_user",
        },
    )
    # knowledge 分支一次到底：node_knowledge 内部复用老 RAG 链路（含 answer_output），
    # 答案已产出并推给前端，不需要再回 Supervisor 做第二次决策
    builder.add_edge("node_knowledge", END)
    # 文档 / 申请书子图跑完都回主 Agent 做第二次决策（routing 里有防重复进入的保护）
    builder.add_edge("document_skill", "node_supervisor")
    builder.add_edge("application_skill", "node_supervisor")
    builder.add_edge("node_answer", END)
    builder.add_edge("node_ask_user", END)
    # langgraph补充添加checkpoint
    return builder.compile(checkpointer=checkpointer or _default_checkpointer)


agent_app = build_agent_graph()


def thread_config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}

# 是前端决定调用哪个 API 的依据
def is_thread_paused(thread_id: str) -> bool:
    """线程是否停在 interrupt 上（前端决定「继续对话」还是「恢复流程」）。"""
    try:
        snapshot = agent_app.get_state(thread_config(thread_id))
    except Exception as exc:
        logger.warning(f"[agent] 读取线程状态失败：{exc}")
        return False
    # snapshot.next 非空表示“还有下一步没执行”，即图停在某个节点前，通常就是 interrupt。
    return bool(snapshot and snapshot.next)# 


def get_thread_state(thread_id: str) -> dict:
    """读取线程当前 State（调试 / 前端恢复用）。"""
    # StateSnapshot 对象，包含 .values、.next、.interrupts；
    snapshot = agent_app.get_state(thread_config(thread_id))
    if snapshot is None:
        return {}
    state = dict(snapshot.values or {})
    state["__next__"] = list(snapshot.next or ())
    state["__interrupts__"] = [
        getattr(item, "value", item) for item in (snapshot.interrupts or ())
    ]
    return state

# 未暂停
def invoke_agent(state: dict, thread_id: str) -> dict:
    """执行一轮对话。"""
    # 开始执行主agent
    return agent_app.invoke(state, thread_config(thread_id))

# 已暂停
def resume_agent(value, thread_id: str) -> dict:
    """用用户输入恢复被 interrupt 暂停的流程（规范第 35 节 Command(resume)）。"""
    from langgraph.types import Command

    return agent_app.invoke(Command(resume=value), thread_config(thread_id))

# 统一出参
def build_response(state: dict) -> dict:
    """把图最终 State 转成对外的返回结构（规范第 68 节）。"""
    '''
    interrupt:	有 __interrupt__	需要用户确认，带 payload/candidates
    file:	generated_this_turn（本轮真的渲染了文件）	文件下载链接
    message:	默认	普通文本回答
    '''
    interrupts = state.get("__interrupt__") or []
    if interrupts:
        payload = getattr(interrupts[0], "value", interrupts[0])
        payload = payload if isinstance(payload, dict) else {"value": payload}
        return {
            "type": "interrupt",
            "reason": payload.get("reason") or state.get("interrupt_reason"),
            "payload": payload,
            "candidates": payload.get("candidates"),
            "message": "需要你确认后继续。",
            "thread_id": state.get("thread_id"),
        }

    content = state.get("answer") or ""
    # 必须是"本轮"生成的才给文件卡片：generated_file_* 跨轮保留，
    # 用它判会让之后每轮回答都重复弹一次"业务申请书已生成"
    if state.get("generated_this_turn"):
        return {
            "type": "file",
            "content": content or "业务申请书已生成。",
            "file": {
                "name": state.get("generated_file_name"),
                "url": state.get("generated_file_url"),
            },
            "thread_id": state.get("thread_id"),
        }
    return {
        "type": "message",
        "content": content,
        "thread_id": state.get("thread_id"),
        "intent": state.get("intent"),
        "document_type": (state.get("document_analysis") or {}).get("document_type"),
    }
