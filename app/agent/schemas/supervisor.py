"""Supervisor（Main Agent）的结构化决策 —— 只讲"用户要什么"。

契约（设计见 docs/Supervisor契约改造设计.md）：

    下行：next_action（让谁做）+ task（一句话目标）+ context（请求侧事实）
    上行：子 Agent 用 AgentResult 汇报（schemas/agent_result.py）

【纪律】Supervisor 的 schema 里**不得出现子 Agent 的能力名 / 节点名**。一旦出现
`extract_mailing_address`、`node_prepare_document` 这类词，就意味着主 Agent 开始规定
"子 Agent 先做什么、再做什么"，子 Agent 想加能力就得回来改主 Agent。
这条线由 `app/test/test_supervisor_contract.py` 静态守着。

对照（同一件事的两种说法）：
    ❌ {"next_action": "document", "document_action": "extract_mailing_address"}
    ✅ {"next_action": "document", "task": "提取这份询证函中可用于寄送的地址",
        "context": {"document_id": "file_9f3c2a1b"}}
"""
from typing import Literal

from pydantic import BaseModel, Field

# 一级路由：主 Agent 只决定"让谁做"，一共 5 种
NextAction = Literal["knowledge", "document", "application", "answer", "ask_user"]


class SupervisorDecision(BaseModel):
    """Main Agent 每一步的决策。

    next_action 与三个顶层能力一一对应：
        knowledge   → 老 RAG 全链路（query_app，含 answer_output）
        document    → Document Understanding Subgraph
        application → Business Application Subgraph
        answer      → 直接组织回答（工具结果已在 State 里）
        ask_user    → 信息不足，需要用户补充
    """

    next_action: NextAction = Field(description="下一步动作：让谁做")
    # 用户视角的一句话目标，交给子 Agent 自己映射到它的能力。
    # 写"提取这份询证函中可用于寄送的地址"，不要写"extract_mailing_address"。
    task: str = Field(
        default="",
        description="用一句话陈述要帮用户完成什么（用户视角，不得出现能力名 / 节点名）",
    )
    # 只放请求侧事实：文件 id、用户明确给出的字段值、用户指定的约束。
    # 不放执行计划（先分类再抽取、用哪个节点这类）。
    context: dict = Field(
        default_factory=dict,
        description="请求侧事实：document_id / company_name / 用户明确给出的字段",
    )
    reason: str = Field(default="", description="决策理由（写进 trace 便于复盘）")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="决策置信度")
    # 需要用户补充时，把要问的话放在这里（缺省由 ask_user 节点生成）
    missing_info: list[str] = Field(default_factory=list, description="缺失的关键信息")
