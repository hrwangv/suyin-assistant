"""Main Agent 的条件路由与循环保护。"""
from app.conf.agent_config import agent_config
from app.core.logger import logger


def route_after_supervisor(state: dict) -> str:
    """把 SupervisorDecision.next_action 映射到图节点，并做循环保护。"""
    route = state.get("route") or "answer"
    turns = int(state.get("supervisor_turns") or 0)

    # knowledge 分支一次到底（复用老 RAG 链路 → END），不存在反复调用的风险，
    # 因此不受"决策次数上限"约束，直接放行
    if route == "knowledge":
        return "node_knowledge"

    # 1. 决策预算用完：必须收敛到一个出口，不能再继续调工具
    if turns >= agent_config.max_supervisor_turns:
        forced = _terminal_route(state) # 循环结束、兜底：node answer/node ask user
        logger.warning(f"[routing] Supervisor 决策次数达到上限，强制走 {forced}")
        return forced

    # 2. 同一类工具不重复调用（防止 supervisor → tool → supervisor 打转）
    # 如果路由指令是文档处理，且已经分析过文档，就直接回答，不再调文档处理。
    if route == "document" and state.get("document_processed"):
        return "node_answer" if state.get("document_analysis") else "node_ask_user"

    # 申请书分支同理：本轮已经跑过一次申请书子图（生成完成 / 缺字段 / 已取消 / 出错），
    # 第二次决策必须收敛到回答节点，不能重复进子图——重复进入会重跑企业 MCP 查询
    # 甚至二次渲染 DOCX。回答节点会按 application_phase 决定"报结果"还是"反问"。
    if route == "application" and state.get("application_processed"):
        return "node_answer"

    return {
        "document": "document_skill",
        "application": "application_skill",
        "answer": "node_answer",
        "ask_user": "node_ask_user",
    }.get(route, "node_answer")
    # 如果 route 在字典里，返回对应节点；
    # 如果 route 不在字典里（异常或未知指令），默认去 node_answer，保证流程不会卡死。


def _terminal_route(state: dict) -> str:
    """收敛出口：有结果就回答，没结果就问用户。"""
    if (
        state.get("generated_file_url")  # 有生成文件
        or state.get("document_analysis") # 文档分析的结果，主agent 二次决策
        or state.get("retrieved_documents") # 检索回来的列表
        or state.get("application_data") # 用于生成申请书的字段
    ):
        return "node_answer"
    return "node_ask_user"
