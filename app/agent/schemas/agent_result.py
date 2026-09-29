"""Sub Agent → Supervisor 的统一返回协议。

设计见 docs/Supervisor契约改造设计.md。核心约定：

    下行：Supervisor 只说"用户要什么"（intent + task + context）
    上行：Sub Agent 只说"我做完了没有、结果是什么"（AgentResult）

【为什么需要它】改造前主 Agent 了解子 Agent 的方式是"约定俗成地读某几个 State 键"
（document_analysis / document_task_result / application_phase …）。这种隐式契约有两个问题：

1. 它没有边界。子 Agent 换了内部字段，主 Agent 就悄悄读不到了。
   实测过的例子：application 子图读 `document_facts`，但该键没写进 ApplicationState，
   LangGraph 在子图作为节点被调用时只传"子图 schema 里声明过的键"，于是这个读永远是 None。
2. 主 Agent 被迫理解子 Agent 的内部结构，两边一起改。

有了显式出口之后：
    · 主 Agent **只按 status 决策**（报结果 / 反问 / 报错），不解析 result 的内部字段；
    · 子 Agent 的内部字段可以自由演进；
    · 子 Agent 之间也不应该再直接读对方的 State 键，而是各写各的 AgentResult。
"""
from typing import Literal, TypedDict

# success        任务完成，可以组织回答
# need_user_input 需要用户补充 / 确认（等价于 HITL，但不是 interrupt）
# failed         执行失败（OCR 失败、模板缺失、渲染异常…）
AgentResultStatus = Literal["success", "need_user_input", "failed"]


class AgentResult(TypedDict, total=False):
    """一个子 Agent 跑完一轮后的汇报。

    字段语义：
        source       子 Agent 的内部标识（doc 用 document_skill），进日志与 State，不进提示词
        source_label 给主 Agent / 用户看的名字（"文档理解" / "业务申请书"）。
                     子 Agent 自己声明——这样主 Agent 不必内置一份"标识 → 名字"的映射表
        status     见 AgentResultStatus
        task_type  子 Agent 自己判定的结果类型（如 extract_mailing_address / inquiry_letter），
                   只用于观测与日志，**主 Agent 不对它做分支、也不进提示词**
        result     结构化结果（地址 / 企业信息 / 摘要 / 申请书字段…）
        message    需要用户输入时给用户看的话（子 Agent 自己组织，主 Agent 直接转述）
        artifacts  产物：DOCX 等，[{type, name, url}]
        reason     失败 / 无法完成时的原因
    """

    source: str
    source_label: str
    status: AgentResultStatus
    task_type: str
    result: dict
    message: str
    artifacts: list
    reason: str


def make_agent_result(
    source: str,
    status: AgentResultStatus,
    *,
    source_label: str = "",
    task_type: str = "",
    result: dict | None = None,
    message: str = "",
    artifacts: list | None = None,
    reason: str = "",
) -> AgentResult:
    """构造一个 AgentResult（统一补齐字段，避免各处漏键）。"""
    return {
        "source": source,
        "source_label": source_label or source,
        "status": status,
        "task_type": task_type or "",
        "result": dict(result or {}),
        "message": message or "",
        "artifacts": list(artifacts or []),
        "reason": reason or "",
    }


# 子 Agent 的内部状态 → 对外 status 的映射。
# 这两个映射表是"内部细节"与"对外契约"之间唯一的翻译层：
# 子 Agent 以后加状态，只改这里，主 Agent 不受影响。
DOCUMENT_STATUS_MAP: dict[str, AgentResultStatus] = {
    "success": "success",
    "ambiguous": "need_user_input",
    "not_found": "need_user_input",
    "unsupported": "need_user_input",
    "failed": "failed",
    "none": "need_user_input",
}

APPLICATION_PHASE_MAP: dict[str, AgentResultStatus] = {
    "generated": "success",
    "need_user_input": "need_user_input",
    "need_company_choice": "need_user_input",
    "cancelled": "need_user_input",
    "error": "failed",
}
