"""Agent 契约守卫（离线自检）：防止"子 Agent 的内部词汇"回流到 Supervisor。

用法：
    PYTHONPATH=. python app/test/test_supervisor_contract.py

【为什么需要它】改造前 `SupervisorDecision` 里有一个 `document_action` 字段，取值就是
Document Agent 的动作名（extract_mailing_address 等），等于把子 Agent 的内部编排暴露给了
主 Agent——子 Agent 加一个能力，主 Agent 的 schema、提示词、兜底规则、中文标签、评测数据集
要一起改。重构后动作归子 Agent（resolved_action），这条线由本文件静态守着。

四组断言：
1. Supervisor 的 schema 里不得出现子 Agent 的能力名 / 节点名
2. 提示词模板必须能渲染（历史 bug：未转义花括号导致 str.format 抛 KeyError）
3. 子 Agent 的内部状态必须有对应的对外 status 映射（否则新状态会静默丢语义）
4. task → 动作的规则推断符合预期（复用路径不调模型也要判得出来）
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.agent.schemas.agent_result import (  # noqa: E402
    APPLICATION_PHASE_MAP,
    DOCUMENT_STATUS_MAP,
    make_agent_result,
)
from app.agent.schemas.document import (  # noqa: E402
    DocumentTaskAction,
    DocumentTaskStatus,
)
from app.agent.schemas.supervisor import SupervisorDecision  # noqa: E402
from app.agent.services.document_service import infer_action_from_task  # noqa: E402

# 子 Agent 的内部词汇：出现在 Supervisor 的 schema 里就说明耦合回来了
FORBIDDEN_IN_SUPERVISOR = {
    # Document 子图的动作名
    "extract_mailing_address",
    "summarize_document",
    "extract_company_info",
    # 申请书产物名
    "generate_business_application",
    # 两个子图的节点名（含中断点）
    "node_prepare_document",
    "node_understand_document",
    "node_confirm_address",
    "node_finalize_document",
    "node_parse_application_request",
    "node_resolve_company",
    "node_company_select",
    "node_prepare_application",
    "node_human_confirmation",
    "node_render_and_finalize",
    # 子图本身的节点名
    "document_skill",
    "application_skill",
}

# 花括号占位符的合法形态：{task} / {image_content[0]}
_BRACE_GROUP = re.compile(r"(?<!\{)\{(?!\{)([^{}]*)\}(?!\})")
_ALLOWED_PLACEHOLDER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\[[^\[\]]*\])*$")


def test_supervisor_schema_has_no_subagent_vocabulary():
    """Supervisor 只讲"用户要什么"，不许出现子 Agent 的能力名 / 节点名。"""
    text = json.dumps(SupervisorDecision.model_json_schema(), ensure_ascii=False)
    hits = sorted(word for word in FORBIDDEN_IN_SUPERVISOR if word in text)
    assert not hits, f"Supervisor schema 出现了子 Agent 的词汇，说明耦合回来了：{hits}"

    fields = set(SupervisorDecision.model_fields)
    assert {"next_action", "task", "context"} <= fields, fields
    assert "document_action" not in fields, "document_action 应当已移除（动作归子 Agent）"
    print("[PASS] Supervisor schema 不含子 Agent 词汇（契约守卫）OK")


def test_supervisor_prompt_renders():
    """Supervisor 提示词必须能渲染：花括号没转义会让 str.format 抛 KeyError。"""
    from app.agent.supervisor import build_supervisor_prompt

    prompt = build_supervisor_prompt(
        {
            "request_text": "帮我提取这份询证函的邮寄地址",
            "attachments": [],
            # 带上子 Agent 的汇报，确保渲染路径里最容易泄词的那一段也被检查到
            "agent_result": {
                "source": "document_skill",
                "source_label": "文档理解",
                "status": "success",
                "task_type": "extract_mailing_address",
                "result": {"address": "江苏省南京市"},
                "message": "",
                "artifacts": [],
                "reason": "",
            },
        }
    )
    for placeholder in (
        "{today}",
        "{request}",
        "{attachments}",
        "{history}",
        "{subagent_block}",
        "{document_block}",
        "{knowledge_block}",
        "{application_block}",
    ):
        assert placeholder not in prompt, f"占位符没有被替换：{placeholder}"
    # 提示词必须教模型填 task，而不是填动作名
    assert "task" in prompt, "提示词必须说明 task 字段怎么填"
    assert "document_action" not in prompt, "提示词里不该再出现 document_action"
    # 行为层面的守卫：提示词正文里也不能出现子 Agent 的能力名，
    # 否则模型会照着写回 task，耦合从"行为"上重新长出来
    hits = sorted(word for word in FORBIDDEN_IN_SUPERVISOR if word in prompt)
    assert not hits, f"提示词里出现了子 Agent 的能力名 / 节点名：{hits}"
    print("[PASS] Supervisor 提示词可渲染且不含旧字段 OK")


def test_all_prompts_have_legal_braces():
    """所有提示词模板里的花括号要么是占位符，要么必须被转义。

    这条守卫就是针对真实踩过的 bug：agent_supervisor.prompt 的示例里写了
    `{"next_action":"ask_user"...}`，未转义 → str.format 抛 KeyError('"next_action"')
    → build_supervisor_prompt 直接失败 → 整条 Agent 链路在真实请求下不可用。
    离线自检当时发现不了（它们 stub 掉了 decide），所以必须单独守一道。
    """
    prompt_dir = Path(__file__).resolve().parents[2] / "prompts"
    problems: list[str] = []
    for path in sorted(prompt_dir.glob("*.prompt")):
        raw = path.read_text(encoding="utf-8")
        for group in _BRACE_GROUP.findall(raw):
            if not _ALLOWED_PLACEHOLDER.match(group.strip()):
                problems.append(f"{path.name}: {{{group[:40]}}}")
    assert not problems, "提示词里有未转义的花括号（请写成 {{ 与 }}）：\n" + "\n".join(problems[:10])
    print("[PASS] 所有提示词模板的花括号都合法（占位符或被转义）OK")


def test_status_maps_cover_subagent_states():
    """子 Agent 的内部状态必须都有对外 status 映射，否则新状态会静默丢语义。"""
    for status in DocumentTaskStatus.__args__:
        assert status in DOCUMENT_STATUS_MAP, f"DocumentTaskStatus.{status} 缺少对外映射"
    for phase in (
        "generated",
        "need_user_input",
        "need_company_choice",
        "cancelled",
        "error",
    ):
        assert phase in APPLICATION_PHASE_MAP, f"application_phase={phase} 缺少对外映射"

    allowed = {"success", "need_user_input", "failed"}
    assert set(DOCUMENT_STATUS_MAP.values()) <= allowed
    assert set(APPLICATION_PHASE_MAP.values()) <= allowed
    # 构造器要补齐全部字段，避免各处漏键
    card = make_agent_result("document_skill", "success")
    assert set(card) == {
        "source",
        "source_label",
        "status",
        "task_type",
        "result",
        "message",
        "artifacts",
        "reason",
    }, card
    print("[PASS] 内部状态到 AgentResult.status 的映射齐全 OK")


def test_infer_action_from_task():
    """task 文本 → 本 Agent 动作 的规则推断（复用路径与模型兜底都依赖它）。"""
    cases = {
        "提取这份询证函中可用于寄送的地址": "extract_mailing_address",
        "帮我拿一下回函地址": "extract_mailing_address",
        "总结这份文档讲了什么": "summarize_document",
        "提取该文件中的企业信息": "extract_company_info",
        "": "none",
        "帮我看看这个文件": "none",
    }
    valid = set(DocumentTaskAction.__args__)
    for task, expected in cases.items():
        got = infer_action_from_task(task)
        assert got == expected, f"task={task!r} 期望 {expected}，实际 {got}"
        assert got in valid, f"{got} 不在 DocumentTaskAction 白名单里"
    print("[PASS] task → 动作 的规则推断符合预期 OK")


def main():
    test_supervisor_schema_has_no_subagent_vocabulary()
    test_supervisor_prompt_renders()
    test_all_prompts_have_legal_braces()
    test_status_maps_cover_subagent_states()
    test_infer_action_from_task()
    print("\n契约守卫自检全部通过 ✅")


if __name__ == "__main__":
    main()
