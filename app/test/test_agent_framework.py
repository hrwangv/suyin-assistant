"""企业业务智能 Agent 框架自检（离线，不联网、不依赖 MySQL/Qdrant）。

用法：
    PYTHONPATH=. python app/test/test_agent_framework.py

覆盖：
1. Document Understanding Subgraph：OCR 结果 → 分类 → 地址候选
2. Business Application Subgraph：单一候选自动选中 → 模板选择 → HITL 确认 → 生成 DOCX
3. Business Application Subgraph：多候选 → interrupt → Command(resume) → 生成 DOCX
4. Supervisor 规则兜底与路由映射
5. Main Agent 主图：knowledge → supervisor → answer 的完整走通
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402

from app.agent import supervisor as supervisor_module  # noqa: E402
from app.agent.graph import build_agent_graph  # noqa: E402
from app.agent.routing import route_after_supervisor  # noqa: E402
from app.agent.schemas.supervisor import SupervisorDecision  # noqa: E402
from app.agent.services.company_service import (  # noqa: E402
    normalize_company_name,
    pick_company,
    rank_by_name,
)
from app.agent.services.file_store import save_upload  # noqa: E402
from app.agent.state import create_agent_state  # noqa: E402
from app.agent.subgraphs import business_application, document_understanding  # noqa: E402
from app.agent.subgraphs.business_application import build_application_app  # noqa: E402
from app.agent.subgraphs.document_understanding import build_document_app  # noqa: E402

# 子图单独调用时用自己的检查点（嵌套进主图时用 module 级的 document_app / application_app）
document_app = build_document_app(InMemorySaver())

INQUIRY_TEXT = """询证函
致：江苏某某科技有限公司
本公司聘请的会计师事务所正在对本公司财务报表进行审计，
按照中国注册会计师审计准则的要求，应当询证本公司与贵公司的往来款项。
截至 2026-08-31，贵公司欠本公司货款余额 1,234,567.89 元。
回函地址：江苏省南京市鼓楼区中山路1号苏银大厦12层
联系人：王会计    电话：025-88886666
"""

# 普通业务文档：既不是询证函也不是营业执照（用于验证 unsupported 边界）
GENERIC_TEXT = """关于召开2026年第三季度经营分析会的通知
各部门：
定于2026年9月28日上午9:00在公司12楼会议室召开季度经营分析会，
请各部门负责人准时参加并准备汇报材料。
联系人：张三 电话：025-12345678
特此通知。
"""


def _fake_ocr(file):
    """假的 OCR 结果：内容由文件名决定，避免测试依赖外部 MCP。"""
    if "ambiguous" in file.filename:
        # 两个同优先级的"邮寄地址" → 地址优选应当判为 ambiguous（走 HITL）
        text = """询证函
致：XX科技有限公司
截至2026年8月31日，贵公司欠本公司货款余额 1,234,567.89 元。
邮寄地址：江苏省南京市鼓楼区中山路1号
邮寄地址：江苏省苏州市工业园区星湖街2号
联系人：王会计 电话：025-88886666
"""
    elif "notice" in file.filename:
        text = GENERIC_TEXT
    elif "license" in file.filename:
        text = """营业执照
名称：江苏欣旺达新能源科技有限公司
统一社会信用代码：91320100MA1XXXXX9K
类型：有限责任公司
法定代表人：李四
注册资本：5000万元
住所：江苏省南京市江宁区秣周东路9号
成立日期：2020年3月18日
"""
    else:
        text = INQUIRY_TEXT
    return {
        "document_id": file.file_id,
        "full_text": text,
        "pages": [{"page_number": 1, "text": text, "blocks": []}],
        "confidence": 0.98,
        "provider": "fake",
    }


def _fake_company(query, limit=None, tool_name=None):
    """假的 Company MCP：返回与查询相关的候选。"""
    if query == "南京XX能源":
        return {
            "query": query,
            "candidates": [
                _candidate("南京XX能源有限公司", "91320100MA1AAAAA1A", "江苏省", "南京市"),
                _candidate("南京XX新能源有限公司", "91320100MA1BBBBB2B", "江苏省", "南京市"),
                _candidate("江苏XX能源科技有限公司", "91320100MA1CCCCC3C", "江苏省", "南京市"),
            ],
            "source": "fake_mcp",
        }
    if query in {"欣旺达", "欣旺达电子股份有限公司"}:
        return {
            "query": query,
            "candidates": [
                _candidate(
                    "欣旺达电子股份有限公司", "914403001921901585", "广东省", "深圳市"
                )
            ],
            "source": "fake_mcp",
        }
    if query == "江苏欣旺达新能源科技有限公司":
        return {
            "query": query,
            "candidates": [
                _candidate(
                    "江苏欣旺达新能源科技有限公司",
                    "91320100MA1XXXXX9K",
                    "江苏省",
                    "南京市",
                    "江苏省南京市江宁区秣周东路9号",
                )
            ],
            "source": "fake_mcp",
        }
    return {"query": query, "candidates": [], "source": "fake_mcp"}


def _candidate(name, code, province, city, address=None):
    return {
        "company_name": name,
        "unified_social_credit_code": code,
        "registered_address": address,
        "province": province,
        "city": city,
        "legal_person": None,
        "source": "fake_mcp",
    }


def _patch_offline(monkeypatch_map: dict) -> None:
    """把会联网的调用替换成离线版本。"""
    for module, name, value in monkeypatch_map.values():
        setattr(module, name, value)


def _install_stubs():
    """统一安装离线桩。"""
    # 1. 文档语义：不调模型，走规则/正则兜底
    document_understanding.document_service.json_completion = lambda *a, **k: None
    document_understanding.document_service.text_completion = lambda *a, **k: ""
    document_understanding.run_ocr = _fake_ocr
    # 2. 企业检索：不调 MCP
    business_application.company_service.company_mcp_configured = lambda: True
    business_application.company_service.search_company = _fake_company
    # 3. 申请书请求解析：不调模型，从提示词里的"用户原话"抠企业名；
    #    HITL 回复解析仍然走规则兜底（返回 None）
    import re

    def _fake_json_completion(schema, prompt, *args, **kwargs):
        if getattr(schema, "__name__", "") == "_ParsedRequest":
            user_text = prompt.split("用户原话：")[-1].strip()
            match = re.search(
                r"生成\s*([^\s，,。；;]{2,30}?)(?:公司)?的业务申请书", user_text
            )
            return schema(company_query=match.group(1) if match else None)
        return None

    business_application.json_completion = _fake_json_completion
    # 4. 回答生成：不调模型
    import app.agent.nodes.node_answer as node_answer_module

    node_answer_module._generate_answer = lambda state, prompt: "（离线桩回答）"
    # 5. Supervisor：由测试按顺序给出决策
    decisions = [SupervisorDecision(next_action="knowledge", reason="测试"), SupervisorDecision(next_action="answer", reason="测试")]
    supervisor_module.decide = lambda state: decisions.pop(0) if decisions else SupervisorDecision(next_action="answer")
    import app.agent.nodes.node_supervisor as node_supervisor_module

    node_supervisor_module.decide = supervisor_module.decide
    # 6. 知识问答：不跑老图（Qdrant / 大模型），用桩模拟老链路产物
    import app.agent.nodes.node_knowledge as node_knowledge_module

    node_knowledge_module.answer_with_legacy_chain = lambda **kwargs: {
        "answer": "（老链路桩答案）",
        "filters": {},
        "documents": [
            {
                "content": "欣旺达 2026 年 8 月发布半年报，净利润同比增长 20%。",
                "text": "欣旺达 2026 年 8 月发布半年报，净利润同比增长 20%。",
                "date": "2026-08-17",
                "company": "欣旺达",
                "score": 0.87,
                "title": "重点客户动态",
                "source": "local",
            }
        ],
        "rewritten_query": kwargs.get("query"),
        "image_urls": [],
    }


def _doc_state(thread: str, attachments=None, action: str = "none", task: str = "") -> dict:
    """构造 Document 子图的输入。

    `task` 是生产链路的输入：主 Agent 只传"用户要什么"，动作由子图自己判定。
    `action` 是**显式覆盖**通道（测试 / 评测 / 老客户端指定动作时用）。
    """
    return {
        "session_id": thread,
        "thread_id": thread,
        "is_stream": False,
        "attachments": attachments or [],
        "document_task_text": task,
        "document_action": action,
    }


def test_document_subgraph_address_task():
    """按 action=extract_mailing_address 执行：返回 facts + task_result（含地址优选）。"""
    upload = save_upload(INQUIRY_TEXT.encode("utf-8"), "询证函.png", "image/png")
    result = document_app.invoke(
        _doc_state("test-doc-addr", [upload.to_dict()], "extract_mailing_address"),
        {"configurable": {"thread_id": "test-doc-addr"}},
    )
    assert result["document_type"] == "inquiry_letter", result.get("document_type")

    # 任务结果（这次任务做得怎么样）
    task_result = result["document_task_result"]
    assert task_result["action"] == "extract_mailing_address"
    assert task_result["status"] == "success", task_result
    assert task_result["address_decision"] == "single", task_result
    assert "南京市" in (task_result["preferred_address"] or ""), task_result

    # 事实层（文件里有什么）——可被申请书子图复用
    facts = result["document_facts"]
    assert facts["document_type"] == "inquiry_letter"
    assert facts["contact_person"] == "王会计", facts
    assert facts["contact_phone"] == "025-88886666", facts
    assert facts["amount"] == "1,234,567.89", facts
    assert facts["candidate_addresses"], facts
    assert facts["candidate_addresses"][0]["label"] in {"回函地址", "邮寄地址"}
    assert facts["raw_ref"].endswith("询证函.png")
    assert result["document_analysis"]["task_result"]["status"] == "success"
    assert result["document_processed"] is True
    print("[PASS] Document 子图：action=地址提取 → facts + task_result(single) OK")


def test_document_subgraph_other_tasks():
    """summarize_document / extract_company_info 各自走对应分支。"""
    upload = save_upload(INQUIRY_TEXT.encode("utf-8"), "询证函.png", "image/png")
    summary_result = document_app.invoke(
        _doc_state("test-doc-sum", [upload.to_dict()], "summarize_document"),
        {"configurable": {"thread_id": "test-doc-sum"}},
    )
    assert summary_result["document_task_result"]["status"] == "success"
    assert summary_result["document_task_result"]["data"]["summary"]
    # 一次理解会把分类 / 字段 / 地址 / 摘要一起产出，所以只要摘要时地址候选也在
    # （这正是"换任务复用不必重跑模型"的前提）
    assert summary_result["candidate_addresses"], summary_result["candidate_addresses"]

    license_file = save_upload(INQUIRY_TEXT.encode("utf-8"), "business_license.png", "image/png")
    company_result = document_app.invoke(
        _doc_state("test-doc-co", [license_file.to_dict()], "extract_company_info"),
        {"configurable": {"thread_id": "test-doc-co"}},
    )
    data = company_result["document_task_result"]["data"]
    assert company_result["document_task_result"]["status"] == "success", company_result
    assert data["company_name"] == "江苏欣旺达新能源科技有限公司", data
    assert data["unified_social_credit_code"] == "91320100MA1XXXXX9K", data
    assert data["province"] == "江苏省", data
    print("[PASS] Document 子图：action=摘要 / 企业信息 各走各的分支 OK")


def test_document_subgraph_unsupported_and_no_action():
    """超出白名单要明确说 unsupported；没给 action 就只做通用理解。"""
    generic = save_upload(GENERIC_TEXT.encode("utf-8"), "notice.png", "image/png")
    unsupported = document_app.invoke(
        _doc_state("test-doc-uns", [generic.to_dict()], "extract_mailing_address"),
        {"configurable": {"thread_id": "test-doc-uns"}},
    )
    assert unsupported["document_task_result"]["status"] == "unsupported", unsupported[
        "document_task_result"
    ]
    assert unsupported["document_type"] in {"generic_document", "unknown"}

    no_action = document_app.invoke(
        _doc_state("test-doc-none", [generic.to_dict()], "none"),
        {"configurable": {"thread_id": "test-doc-none"}},
    )
    assert no_action["document_task_result"]["status"] == "none"
    assert no_action["document_facts"]["document_type"] == no_action["document_type"]
    print("[PASS] Document 子图：unsupported / 无 action 的边界 OK")


def test_document_subgraph_address_ambiguous_hitl():
    """两个同优先级地址 → ambiguous → interrupt 让用户选 → resume 后给出结果。"""
    from langgraph.types import Command

    upload = save_upload(
        INQUIRY_TEXT.encode("utf-8"), "询证函_ambiguous.png", "image/png"
    )
    app = build_document_app(InMemorySaver())
    thread = "test-doc-amb"
    cfg = {"configurable": {"thread_id": thread}}

    first = app.invoke(
        _doc_state(thread, [upload.to_dict()], "extract_mailing_address"), cfg
    )
    assert first.get("__interrupt__"), "地址歧义应当触发 HITL"
    payload = first["__interrupt__"][0].value
    assert payload["reason"] == "multiple_address_candidates", payload
    assert len(payload["candidates"]) == 2, payload

    second = app.invoke(Command(resume="2"), cfg)
    assert second["document_task_result"]["status"] == "success", second["document_task_result"]
    assert "苏州" in second["document_task_result"]["preferred_address"], second[
        "document_task_result"
    ]
    print("[PASS] Document 子图：地址歧义 interrupt → 用户选序号 → success OK")


def test_document_subgraph_reuse_same_file():
    """同一份文件换个任务：不重新 OCR，直接用已有理解结果跑专项分支。"""
    import app.agent.subgraphs.document_understanding as du

    upload = save_upload(INQUIRY_TEXT.encode("utf-8"), "询证函.png", "image/png")
    app = build_document_app(InMemorySaver())
    thread = "test-doc-reuse"
    cfg = {"configurable": {"thread_id": thread}}

    calls = []
    original_ocr = du.run_ocr
    du.run_ocr = lambda file: (calls.append(file.file_id), original_ocr(file))[1]
    try:
        first = app.invoke(_doc_state(thread, [upload.to_dict()], "summarize_document"), cfg)
        assert first["document_task_result"]["status"] == "success"
        assert first["candidate_addresses"], "一次理解应当顺带产出地址候选"

        # 第二轮：没有新附件，只给 action —— 复用同一份文件
        # 注意必须显式传 attachments=[]（生产里 create_agent_state 每轮都会带上这个键，
        # 不传的话会沿用上一轮的值，被当成"又上传了一次"）
        second = app.invoke(
            {
                "session_id": thread,
                "is_stream": False,
                "attachments": [],
                "document_action": "extract_mailing_address",
            },
            cfg,
        )
    finally:
        du.run_ocr = original_ocr

    assert len(calls) == 1, f"复用不应该重新 OCR，实际 OCR 次数={len(calls)}"
    assert second["document_reuse"] is True, second.get("document_reuse")
    # 复用轮必须执行"本轮"的 action，而不是重放上一轮的任务
    assert second["document_task_result"]["action"] == "extract_mailing_address", second[
        "document_task_result"
    ]
    assert second["document_task_result"]["status"] == "success", second["document_task_result"]
    print("[PASS] Document 子图：同一文件换任务复用（不重复 OCR）OK")


def _run_document(thread: str, upload, task: str = "") -> dict:
    """跑一次 Document 子图（生产链路：主 Agent 只给 task，不给动作）。"""
    return document_app.invoke(
        _doc_state(thread, [upload.to_dict()], task=task),
        {"configurable": {"thread_id": thread}},
    )


def test_document_decides_action_from_task():
    """生产链路：主 Agent 只给 task（用户目标），动作由 Document Agent 自己判定。

    这是重构的核心断言：契约生效后，动作名不再出现在主 Agent 那边，
    子图拿到的只有一句"用户要什么"，由它自己映射成本 Agent 的动作。
    """
    upload = save_upload(INQUIRY_TEXT.encode("utf-8"), "询证函.png", "image/png")

    # 1) 要地址：task 里说的是"可用于寄送的地址"，子图自己判定为 extract_mailing_address
    result = _run_document("test-doc-task-1", upload, task="提取这份询证函中可用于寄送的地址")
    assert result["resolved_action"] == "extract_mailing_address", result.get("resolved_action")
    assert result["document_task_result"]["status"] == "success", result["document_task_result"]
    assert result["document_task_result"]["preferred_address"], result["document_task_result"]
    assert result["agent_result"]["status"] == "success", result["agent_result"]
    assert result["agent_result"]["task_type"] == "extract_mailing_address"

    # 2) 换目标：同一个子图，动作跟着 task 变，主 Agent 不需要知道有哪些动作
    license_file = save_upload(INQUIRY_TEXT.encode("utf-8"), "business_license.png", "image/png")
    second = _run_document(
        "test-doc-task-2", license_file, task="把这家公司的企业信息提取出来"
    )
    assert second["resolved_action"] == "extract_company_info", second.get("resolved_action")
    assert second["document_task_result"]["status"] == "success", second["document_task_result"]

    # 3) task 里看不出要做什么 → 动作判为 none，由主 Agent 反问用户（不许猜）
    third = _run_document("test-doc-task-3", upload)
    assert third["resolved_action"] == "none", third.get("resolved_action")
    assert third["document_task_result"]["status"] == "none", third["document_task_result"]
    # 上行汇报把它翻译成"需要用户输入"，而不是"失败"
    assert third["agent_result"]["status"] == "need_user_input", third["agent_result"]
    print("[PASS] Document Agent 自己判定动作（task → resolved_action）OK")


def test_main_graph_document_task_contract():
    """主图端到端：Supervisor 只给 task（用户目标）→ Document Agent 自己判定动作 → 回答。

    这是重构验收的核心用例：主 Agent 全程没有出现任何动作名。
    """
    import app.agent.nodes.node_supervisor as node_supervisor_module

    calls = {"n": 0}

    def fake_decide(state):
        calls["n"] += 1
        if calls["n"] == 1:
            # 第一次决策：只说明用户要什么，不指定子 Agent 该执行哪个动作
            return SupervisorDecision(
                next_action="document", task="提取这份询证函中可用于寄送的地址"
            )
        # 第二次决策：子 Agent 已经汇报，直接组织回答
        return SupervisorDecision(next_action="answer", reason="子 Agent 已给出结果")

    node_supervisor_module.decide = fake_decide
    try:
        app = build_agent_graph(InMemorySaver())
        thread = "test-main-doc-contract"
        upload = save_upload(INQUIRY_TEXT.encode("utf-8"), "询证函.png", "image/png")
        state = create_agent_state(
            thread, "提取这份询证函中可用于寄送的地址", attachments=[upload.to_dict()]
        )
        result = app.invoke(state, {"configurable": {"thread_id": thread}})
    finally:
        node_supervisor_module.decide = supervisor_module.decide

    # 主 Agent 只写目标陈述；动作是子 Agent 自己判定的
    assert result["document_task_text"] == "提取这份询证函中可用于寄送的地址"
    assert result["resolved_action"] == "extract_mailing_address", result.get("resolved_action")
    assert result["document_task_result"]["status"] == "success", result["document_task_result"]
    # 上行契约也走通了
    assert result["agent_result"]["status"] == "success", result["agent_result"]
    assert result["agent_results"]["document_skill"]["source"] == "document_skill"
    assert result.get("answer"), result.get("answer")
    print("[PASS] 主图端到端：Supervisor 只给 task、子 Agent 自己判定动作 OK")


def test_application_single_candidate():
    app = build_application_app(InMemorySaver())
    thread = "test-app-1"
    state = {
        "session_id": thread,
        "thread_id": thread,
        "is_stream": False,
        "request_text": "帮我生成欣旺达公司的业务申请书",
    }
    cfg = {"configurable": {"thread_id": thread}}
    first = app.invoke(state, cfg)
    assert first.get("__interrupt__"), "应当停在生成前确认"
    assert first["application_phase"] == "template_ready"
    assert first["template_type"] == "other", first["template_type"]
    assert first["selected_company"]["company_name"] == "欣旺达电子股份有限公司"

    second = app.invoke(__import__("langgraph.types", fromlist=["Command"]).Command(resume="确认"), cfg)
    assert second["application_phase"] == "generated", second.get("application_phase")
    assert Path(second["generated_file_path"]).exists()
    assert second["template_type"] == "other"
    # 上行契约：出口写 AgentResult，主 Agent 只按 status 决策
    card = second["agent_result"]
    assert card["source"] == "application_skill", card
    assert card["status"] == "success", card
    assert card["artifacts"] and card["artifacts"][0]["name"] == second["generated_file_name"], card
    assert second["agent_results"]["application_skill"]["status"] == "success"
    print("[PASS] Application Subgraph（单候选 → HITL 确认 → DOCX）OK")


def test_application_multi_candidate():
    app = build_application_app(InMemorySaver())
    thread = "test-app-2"
    state = {
        "session_id": thread,
        "thread_id": thread,
        "is_stream": False,
        "request_text": "帮我生成南京XX能源的业务申请书",
    }
    cfg = {"configurable": {"thread_id": thread}}
    from langgraph.types import Command

    first = app.invoke(state, cfg)
    assert first.get("__interrupt__"), "多候选应当 interrupt"
    payload = first["__interrupt__"][0].value
    assert payload["reason"] == "multiple_company_candidates", payload
    assert len(payload["candidates"]) == 3

    second = app.invoke(Command(resume="2"), cfg)
    assert second.get("__interrupt__"), "选完企业后应当停在生成前确认"
    assert second["selected_company"]["company_name"] == "南京XX新能源有限公司"

    third = app.invoke(Command(resume="确认"), cfg)
    assert third["application_phase"] == "generated", third.get("application_phase")
    assert third["template_type"] == "jiangsu", third["template_type"]
    assert Path(third["generated_file_path"]).exists()
    print("[PASS] Application Subgraph（多候选 → 选择 → 确认 → DOCX）OK")


def test_application_confirm_with_extra_fields_loops_back():
    """确认时顺手补字段 → 回到准备节点重新合并 / 校验 / 选模板。

    这是节点合并后的重点回归点：合并前回边指向 node_merge_document_information，
    合并后指向 node_prepare_application（它还要顺带做校验与选模板）。
    """
    from langgraph.types import Command

    from app.agent.subgraphs import business_application as ba

    app = build_application_app(InMemorySaver())
    thread = "test-app-confirm-fields"
    cfg = {"configurable": {"thread_id": thread}}
    first = app.invoke(
        {
            "session_id": thread,
            "thread_id": thread,
            "is_stream": False,
            "request_text": "帮我生成欣旺达公司的业务申请书",
        },
        cfg,
    )
    assert first["application_phase"] == "template_ready", first.get("application_phase")

    # 用户在第 3 轮确认时补了"省份"。离线规则解析不出字段值，这里直接桩掉解析器
    # （模型路径下这一步由 application_user_input.prompt 完成）。
    original = ba._parse_user_input
    ba._parse_user_input = lambda pending, options, text: ba._UserInput(
        confirm=True, province="广东省"
    )
    try:
        second = app.invoke(Command(resume="确认，省份是广东省"), cfg)
    finally:
        ba._parse_user_input = original

    assert second["application_data"]["province"] == "广东省", second["application_data"]
    # 补的字段要重新走一遍准备节点，因此又回到"待确认"，而不是直接生成
    assert second["application_phase"] == "template_ready", second.get("application_phase")
    assert second.get("__interrupt__"), "补字段后应当重新请用户确认"

    third = app.invoke(Command(resume="确认"), cfg)
    assert third["application_phase"] == "generated", third.get("application_phase")
    assert Path(third["generated_file_path"]).exists()
    print("[PASS] Application 确认时补字段 → 回到准备节点重算 OK")


def test_application_confirm_corrects_company_name():
    """确认时**修正**企业名 → 最终按新名字生成。

    回归靶子：合并顺序里 application_data（上一轮快照）原本排在最后，
    等于优先级最高，会把用户本轮的修正盖回去——user_fields 收到了新名字，
    但重新合并后仍按旧企业名渲染，用户措手不及。
    """
    from langgraph.types import Command

    from app.agent.subgraphs import business_application as ba

    app = build_application_app(InMemorySaver())
    thread = "test-app-correct-name"
    cfg = {"configurable": {"thread_id": thread}}
    first = app.invoke(
        {
            "session_id": thread,
            "thread_id": thread,
            "is_stream": False,
            "request_text": "帮我生成欣旺达公司的业务申请书",
        },
        cfg,
    )
    assert first["application_phase"] == "template_ready", first.get("application_phase")
    assert first["application_data"]["company_name"] == "欣旺达电子股份有限公司"

    original = ba._parse_user_input
    ba._parse_user_input = lambda pending, options, text: ba._UserInput(
        confirm=True,
        company_name="苏州测试科技有限公司",
        unified_social_credit_code="91320500MA1YYYYY1A",
    )
    try:
        second = app.invoke(Command(resume="公司名改成苏州测试科技有限公司"), cfg)
    finally:
        ba._parse_user_input = original

    # 用户给的字段必须赢过上一轮快照
    assert second["application_data"]["company_name"] == "苏州测试科技有限公司", second[
        "application_data"
    ]
    assert (
        second["application_data"]["unified_social_credit_code"] == "91320500MA1YYYYY1A"
    ), second["application_data"]
    assert second.get("__interrupt__"), "补字段后应当重新请用户确认"

    third = app.invoke(Command(resume="确认"), cfg)
    assert third["application_phase"] == "generated", third.get("application_phase")
    assert "苏州测试科技有限公司" in Path(third["generated_file_path"]).name, third[
        "generated_file_name"
    ]
    print("[PASS] Application 确认时修正企业名 → 按新名字生成 OK")


def test_license_to_application_merge():
    """营业执照 OCR 字段 + MCP 字段合并后直接进模板选择。"""
    upload = save_upload(INQUIRY_TEXT.encode("utf-8"), "business_license.png", "image/png")
    thread = "test-app-3"
    cfg = {"configurable": {"thread_id": thread}}
    doc_state = {
        "session_id": thread,
        "thread_id": thread,
        "is_stream": False,
        "attachments": [upload.to_dict()],
    }
    doc_result = document_app.invoke(doc_state, cfg)
    assert doc_result["document_type"] == "business_license", doc_result.get("document_type")
    assert doc_result["document_fields"]["unified_social_credit_code"] == "91320100MA1XXXXX9K"

    from langgraph.types import Command

    app = build_application_app(InMemorySaver())
    result = app.invoke(
        {
            **doc_state,
            "request_text": "根据这个文件生成业务申请书",
            "document_fields": doc_result["document_fields"],
        },
        {"configurable": {"thread_id": thread + "-app"}},
    )
    assert result.get("__interrupt__"), result
    assert result["template_type"] == "jiangsu", result.get("template_type")
    final = app.invoke(Command(resume="确认"), {"configurable": {"thread_id": thread + "-app"}})
    assert final["application_phase"] == "generated"
    assert final["application_data"]["company_name"] == "江苏欣旺达新能源科技有限公司"
    # 营业执照上的企业性质 / 注册资本要跟着走到申请书里（原来这两个字段模板里是示例值）
    assert final["application_data"]["enterprise_nature"] == "有限责任公司", final[
        "application_data"
    ]
    assert final["application_data"]["registered_capital"] == "5000万元", final[
        "application_data"
    ]

    from docx import Document

    rendered = Document(final["generated_file_path"])
    blob = "\n".join(p.text for p in rendered.paragraphs) + "\n" + "\n".join(
        cell.text for table in rendered.tables for row in table.rows for cell in row.cells
    )
    assert "江苏欣旺达新能源科技有限公司" in blob and "有限责任公司" in blob, blob[:200]
    assert "5000万元" in blob, blob[:200]
    # 租赁物名称 / 状态按原模板固定，不是占位符
    assert "设备" in blob and "正常使用" in blob
    assert "{{" not in blob, "还有没替换掉的占位符"
    print("[PASS] 营业执照 + Company MCP 字段合并 → 江苏省模板 OK")


def test_docx_single_template_and_page_count():
    """只有一套模板；省内 / 省外的差别是《综合信息查询授权书》的份数（省内 1 份、省外 2 份）。"""
    from docx import Document

    from app.agent.services import docx_service

    base = {
        "unified_social_credit_code": "91320100MA1TEST01A",
        "registered_address": "江苏省南京市鼓楼区中山路1号",
        "legal_person": "张三",
        "application_date": "2026-09-25",
    }
    # 两家企业名不同：生成文件名按企业名+日期，同名会互相覆盖
    jiangsu = docx_service.render_application(
        {**base, "company_name": "江苏测试企业有限公司", "province": "江苏省"}
    )
    other = docx_service.render_application(
        {**base, "company_name": "广东测试企业有限公司", "province": "广东省"}
    )

    assert Path(jiangsu["file_path"]).exists() and Path(other["file_path"]).exists()
    assert jiangsu["template_type"] == "jiangsu" and other["template_type"] == "other"
    # 份数规则：省内一份、省外一式两份
    assert jiangsu["page_count"] == 1, jiangsu["page_count"]
    assert other["page_count"] == 2, other["page_count"]
    # 数据里显式给了就用给的
    assert docx_service.page_count_for("jiangsu", {"page_count": 3}) == 3
    assert docx_service.page_count_for("jiangsu", {"page_count": "不是数字"}) == 1

    def _copies_of_authorization(path: str) -> int:
        """数一数生成件里有几张《综合信息查询授权书》。"""
        doc = Document(path)
        return sum(
            1 for table in doc.tables if "综合信息查询授权书" in (table.cell(0, 0).text or "")
        )

    assert _copies_of_authorization(jiangsu["file_path"]) == 1
    assert _copies_of_authorization(other["file_path"]) == 2

    # 占位符全部替换：企业信息、两种日期写法都落进正文
    doc = Document(other["file_path"])
    blob = "\n".join(p.text for p in doc.paragraphs) + "\n" + "\n".join(
        cell.text for table in doc.tables for row in table.rows for cell in row.cells
    )
    assert "广东测试企业有限公司" in blob and "91320100MA1TEST01A" in blob, blob[:200]
    assert "2026年09月25日" in blob and "2026.09" in blob, blob[:200]
    # 模板原件里的示例企业不许残留（否则会串到别的企业名下）
    assert "盐城市环保" not in blob and "913209006921075291" not in blob
    assert "{{" not in blob, "还有没替换掉的占位符"
    print("[PASS] DOCX：单一模板 + 省外《综合信息查询授权书》两份 OK")


def test_caihui_company_adapter():
    """财汇企业信息 MCP：两步式入参 + 真实返回结构解析（离线，样本是真实抓包）。"""
    from app.agent.mcp import company_client as cc

    # --- 1) 两步式入参：真实工具名是 execute_tool，子工具与参数包在 arguments 里 ---
    payload = cc.build_company_arguments("贵州茅台酒股份有限公司", 5)
    assert payload["tool_name"] == "get_company_basic_info", payload
    sub = payload["arguments"]
    assert sub["target_company"] == ["贵州茅台酒股份有限公司"], sub
    assert "企业名称" in sub["indicator_name"] and "注册资本" in sub["indicator_name"], sub

    # --- 2) 成功返回：长表（headInfo 三列 + 每行一个指标）→ 透视成候选 ---
    success = {
        "status": {"code": 0, "info": "成功", "message": "SUCCESS"},
        "data": {
            "summary": {"return_records": 8, "total_records": 8},
            "records": {
                "headInfo": [
                    {"field": "company_name"},
                    {"field": "index_name"},
                    {"field": "index_value"},
                ],
                "data": [
                    ["贵州茅台酒股份有限公司", "企业名称", "贵州茅台酒股份有限公司"],
                    ["贵州茅台酒股份有限公司", "统一社会信用代码", "9152000071430580XT"],
                    ["贵州茅台酒股份有限公司", "法定代表人", "陈华"],
                    ["贵州茅台酒股份有限公司", "注册资本(万元)", "125008.16"],
                    ["贵州茅台酒股份有限公司", "注册资本币种", "人民币"],
                    ["贵州茅台酒股份有限公司", "注册地址", "贵州省仁怀市茅台镇"],
                    ["贵州茅台酒股份有限公司", "企业性质", "地方国有企业"],
                    ["贵州茅台酒股份有限公司", "组织形式", "股份有限公司"],
                    ["贵州茅台酒股份有限公司", "所属省", "贵州省"],
                    ["贵州茅台酒股份有限公司", "所属市", "遵义市"],
                ],
            },
        },
    }
    candidates = cc.normalize_company_candidates(success)
    assert len(candidates) == 1, candidates
    candidate = candidates[0]
    assert candidate.company_name == "贵州茅台酒股份有限公司"
    assert candidate.unified_social_credit_code == "9152000071430580XT"
    assert candidate.registered_address == "贵州省仁怀市茅台镇"
    assert candidate.province == "贵州省", candidate.province
    assert candidate.city == "遵义市", candidate.city
    assert candidate.legal_person == "陈华"
    # 组织形式优先于企业性质（申请书模板那一格要的是"有限责任公司"这类）
    assert candidate.enterprise_nature == "股份有限公司", candidate.enterprise_nature
    # 财汇的注册资本是数字（单位万元）+ 单独给币种，拼成模板要的写法
    assert candidate.registered_capital == "125008.16万元人民币", candidate.registered_capital

    # --- 2b) code=1 是"成功但有兼容处理"，不能当失败 ---
    warning = dict(success)
    warning["status"] = {"code": 1, "info": "成功，存在参数兼容处理，已按默认值继续查询", "message": "SUCCESS"}
    assert len(cc.normalize_company_candidates(warning)) == 1, "code=1 不该被当成错误"

    # --- 3) 失败信封：不能把"请求回显"当成候选，否则单候选会被自动选中 ---
    failure = {
        "status": {
            "code": 300,
            "info": "入参校验异常，存在入参校验失败，已中断查询",
            "message": "PARAM_ERROR",
            "error": {"type": "PARAM_ERROR", "message": '检索无结果：没有查询到您输入的"XX公司"企业。'},
        },
        "paramDetails": {
            "parameter_list": {
                "original": [
                    {"indicator_name": "企业名称", "indicator_value": ["江苏欣旺达新能源科技有限公司"]}
                ]
            }
        },
    }
    try:
        cc.normalize_company_candidates(failure)
        raise AssertionError("失败信封应当抛 CompanyQueryError")
    except cc.CompanyQueryError as exc:
        assert "检索无结果" in str(exc), exc

    # --- 4) 档案类工具没有"关键字"变体：查不到就只调一次，然后抛错交上层降级 ---
    calls: list[str] = []
    original = cc.call_tool

    def fake_call_tool(spec, tool_name, arguments):
        calls.append(tool_name)
        return failure

    cc.call_tool = fake_call_tool
    try:
        try:
            cc.search_company("查不到的企业", limit=5)
            raise AssertionError("查不到时应当抛 CompanyQueryError")
        except cc.CompanyQueryError:
            pass
    finally:
        cc.call_tool = original
    assert calls == ["execute_tool"], f"不该重复调用：{calls}"
    print("[PASS] 财汇企业 MCP：两步式入参 + 真实返回解析（含失败信封）OK")


def test_supervisor_fallback_and_routing():
    decision = supervisor_module.fallback_decision(
        {"attachments": [{"file_id": "x"}], "request_text": "帮我看看"}
    )
    assert decision.next_action == "document"

    decision = supervisor_module.fallback_decision(
        {
            "document_analysis": {"document_type": "inquiry_letter", "candidate_addresses": ["A"]},
            "request_text": "帮我提取邮寄地址",
        }
    )
    assert decision.next_action == "answer"
    # 兜底路径同样只产出"用户目标"，不产出子 Agent 的动作名
    assert "地址" in decision.task, decision.task

    decision = supervisor_module.fallback_decision(
        {"document_analysis": {"document_type": "inquiry_letter"}, "request_text": "看看这个"}
    )
    assert decision.next_action == "ask_user", decision.next_action

    assert route_after_supervisor({"route": "knowledge"}) == "node_knowledge"
    # knowledge 一次到底，不受决策次数上限影响
    assert route_after_supervisor({"route": "knowledge", "supervisor_turns": 99}) == "node_knowledge"
    assert route_after_supervisor({"route": "document", "document_processed": True,
                                   "document_analysis": {"document_type": "unknown"}}) == "node_answer"
    # 申请书分支：本轮没跑过 → 进子图；跑过 → 收敛到回答，不重复进子图
    assert route_after_supervisor({"route": "application"}) == "application_skill"
    assert route_after_supervisor({"route": "application",
                                   "application_processed": True}) == "node_answer"
    assert route_after_supervisor({"route": "application", "supervisor_turns": 99}) == "node_ask_user"

    # 规则兜底：本轮已跑过申请书流程 → 直接回答（不再选 application）
    decision = supervisor_module.fallback_decision({"application_processed": True})
    assert decision.next_action == "answer", decision.next_action
    print("[PASS] Supervisor 规则兜底与路由映射 OK")


def test_main_graph_knowledge_uses_legacy_chain():
    """knowledge 分支必须复用老 RAG 链路（答案来自 answer_output），不走 Agent 的 node_answer。"""
    app = build_agent_graph(InMemorySaver())
    thread = "test-main-1"
    state = {
        "thread_id": thread,
        "session_id": thread,
        "user_id": "",
        "request_text": "2026年8月欣旺达有什么动态？",
        "original_query": "2026年8月欣旺达有什么动态？",
        "attachments": [],
        "is_stream": False,
        "supervisor_turns": 0,
        "supervisor_decisions": [],
        "user_message_recorded": False,
    }
    result = app.invoke(state, {"configurable": {"thread_id": thread}})
    # 桩答案刻意与 node_answer 的桩（离线桩回答）不同，用来证明走的是老链路
    assert result["answer"] == "（老链路桩答案）", result.get("answer")
    assert len(result["retrieved_documents"]) == 1
    assert result["intent"] == "knowledge"
    assert result["supervisor_turns"] == 1, result["supervisor_turns"]
    print("[PASS] Main Agent 主图（knowledge → 老 RAG 链路 → END，只决策一次）OK")


def test_main_graph_application_hitl():
    """主图里的 HITL：子图 interrupt 会冒泡到主图，Command(resume) 能继续。"""
    from langgraph.types import Command

    app = build_agent_graph(InMemorySaver())
    thread = "test-main-app"
    cfg = {"configurable": {"thread_id": thread}}

    # 让 Supervisor 第一次就直接进申请书流程
    supervisor_module.decide = lambda state: SupervisorDecision(
        next_action="application", reason="测试：生成申请书"
    )
    import app.agent.nodes.node_supervisor as node_supervisor_module

    node_supervisor_module.decide = supervisor_module.decide

    state = {
        "thread_id": thread,
        "session_id": thread,
        "user_id": "",
        "request_text": "帮我生成欣旺达公司的业务申请书",
        "original_query": "帮我生成欣旺达公司的业务申请书",
        "attachments": [],
        "is_stream": False,
        "supervisor_turns": 0,
        "supervisor_decisions": [],
        "user_message_recorded": False,
    }
    first = app.invoke(state, cfg)
    assert first.get("__interrupt__"), "主图应当在生成前确认处暂停"
    from app.agent.graph import build_response

    response = build_response(first)
    assert response["type"] == "interrupt", response
    assert response["reason"] == "confirm_before_generate", response

    second = app.invoke(Command(resume="确认"), cfg)
    assert second["application_phase"] == "generated", second.get("application_phase")
    assert Path(second["generated_file_path"]).exists()
    # application_skill 跑完要回到 supervisor 做第二次决策（supervisor_turns 由 1 变 2）；
    # 这里的桩一直返回 application，同时也验证了 routing 的防重复保护
    # ——否则会再次进子图并再次 interrupt，拿不到 generated。
    assert second["supervisor_turns"] == 2, second["supervisor_turns"]
    assert second["application_processed"] is True
    final_response = build_response(second)
    assert final_response["type"] == "file", final_response
    assert final_response["file"]["name"].endswith("业务申请书.docx")
    print("[PASS] Main Agent 主图 HITL（interrupt → resume → DOCX → 返回 file）OK")


def test_file_card_only_in_generating_turn():
    """回归：生成过一次申请书之后，后续无关问题不能再被包装成文件卡片。

    病灶：回答出口用**会话级**的 generated_file_url 判"本轮出了文件"。那个字段跨轮保留，
    于是生成过一次之后，之后每一轮回答都带着旧的下载卡片——用户看到的就是
    "我明明只是提取了一下询证函，怎么又生成了一份业务申请书"。
    修法：轮次级标记 generated_this_turn，只有本轮真渲染过才给卡片。
    """
    import app.agent.nodes.node_supervisor as node_supervisor_module
    from langgraph.types import Command

    from app.agent.graph import build_response

    def decide(state):
        if "业务申请书" in (state.get("request_text") or ""):
            return SupervisorDecision(next_action="application", reason="测试")
        return SupervisorDecision(next_action="answer", reason="测试")

    original_decide = supervisor_module.decide
    supervisor_module.decide = decide
    node_supervisor_module.decide = decide
    try:
        app = build_agent_graph(InMemorySaver())
        thread = "test-file-card-turn"
        cfg = {"configurable": {"thread_id": thread}}

        # 第一轮：生成申请书（走确认 → resume → 渲染）
        first = app.invoke(create_agent_state(thread, "帮我生成欣旺达公司的业务申请书"), cfg)
        assert first.get("__interrupt__"), "应当停在生成前确认"
        second = app.invoke(Command(resume="确认"), cfg)
        assert second["generated_this_turn"] is True, second.get("generated_this_turn")
        assert build_response(second)["type"] == "file"

        # 第二轮：问一个完全无关的问题
        third = app.invoke(create_agent_state(thread, "2026年8月欣旺达有什么动态？"), cfg)
        assert third["generated_this_turn"] is False, "新一轮不该继承上一轮的出文件标记"
        response = build_response(third)
        assert response["type"] == "message", response
        # 会话级的文件信息仍保留（后续轮次可以引用、可以再下载），只是不再弹"已生成"卡片
        assert third.get("generated_file_url"), third.get("generated_file_url")

        # SSE 流式那条出口（前端实际用的）同样只能在本轮出文件时带 type=file
        import app.agent.nodes.node_answer as node_answer_module

        captured: list[tuple[str, dict]] = []
        original_emit = node_answer_module.emit
        node_answer_module.emit = lambda session, event, payload: captured.append((event, payload))
        try:
            node_answer_module._push_final(
                {
                    "is_stream": True,
                    "session_id": thread,
                    # 上一轮留下的会话级文件信息：这轮没出文件，就不该带卡片
                    "generated_file_name": "old.docx",
                    "generated_file_url": "/api/agent/files/old.docx",
                },
                "普通回答",
                [],
            )
            node_answer_module._push_final(
                {
                    "is_stream": True,
                    "session_id": thread,
                    "generated_this_turn": True,
                    "generated_file_name": "x.docx",
                    "generated_file_url": "/api/agent/files/x.docx",
                },
                "已生成",
                [],
            )
        finally:
            node_answer_module.emit = original_emit
        assert captured[0][1].get("type") != "file", captured[0]
        assert captured[1][1]["type"] == "file", captured[1]
    finally:
        supervisor_module.decide = original_decide
        node_supervisor_module.decide = original_decide
    print("[PASS] 文件卡片只在生成那一轮出现（第二轮不再误报）OK")


def test_company_name_matching():
    """企业名标准化 + 候选排序 + 自动选中判定。

    排序只影响展示顺序（HITL 按序号选用的就是它）；能不能自动选中**不看排序**，
    pick_company 自己在候选里找完全一致的那一个。
    """
    assert normalize_company_name(" 欣 旺 达（电子）有限公司 ") == normalize_company_name(
        "欣旺达电子有限公司"
    )
    from app.agent.schemas.company import CompanyCandidate

    ranked = rank_by_name(
        "欣旺达",
        [
            CompanyCandidate(company_name="欣旺达电子股份有限公司"),
            CompanyCandidate(company_name="深圳市欣旺达物业管理有限公司"),
        ],
    )
    assert ranked[0]["company_name"] == "欣旺达电子股份有限公司", ranked
    assert "match_score" not in ranked[0] and "name_similarity" in ranked[0], ranked[0]

    # 自动选中：单候选 → 选中；多个候选且用户只报简称 → 一律交给用户确认
    only_one = [CompanyCandidate(company_name="欣旺达电子股份有限公司").model_dump()]
    assert pick_company(only_one, "欣旺达")["company_name"] == "欣旺达电子股份有限公司"
    assert pick_company(ranked, "欣旺达") is None, "简称 + 多候选必须走 HITL"
    # 用户给的是完整名称 → 自己找完全一致的那个（不依赖它排在第几位）
    shuffled = list(reversed(ranked))
    picked = pick_company(shuffled, "欣旺达电子股份有限公司")
    assert picked and picked["company_name"] == "欣旺达电子股份有限公司", shuffled
    print("[PASS] 企业名称标准化 + 候选排序 + 自动选中判定 OK")


def test_state_lifecycle_between_turns():
    """State 生命周期：轮次级字段每轮清空，会话级字段跨轮保留。

    这是两个很容易踩的坑，专门锁住：
    1. 第二轮的知识问题不能复用第一轮的 retrieved_documents（否则会答旧资料）；
    2. 第二轮上传的新文件要重新走 OCR（document_processed 每轮重置）。
    """
    import app.agent.nodes.node_knowledge as node_knowledge_module
    import app.agent.nodes.node_supervisor as node_supervisor_module

    original_search = node_knowledge_module.answer_with_legacy_chain
    original_decide = node_supervisor_module.decide
    calls = []

    def query_aware_legacy_chain(**kwargs):
        query = kwargs.get("query")
        calls.append(query)
        return {
            "answer": f"老链路答案：{query}",
            "filters": {},
            "documents": [
                {"content": f"{query} 的证据", "text": f"{query} 的证据", "title": query, "source": "local", "score": 0.9}
            ],
            "rewritten_query": query,
            "image_urls": [],
        }

    # 每一轮的知识问题都重新走老链路
    node_knowledge_module.answer_with_legacy_chain = query_aware_legacy_chain
    node_supervisor_module.decide = lambda state: SupervisorDecision(
        next_action="knowledge",
        reason="测试",
    )
    try:
        app = build_agent_graph(InMemorySaver())
        thread = "test-lifecycle"
        cfg = {"configurable": {"thread_id": thread}}

        turn1 = app.invoke(create_agent_state(thread, "2026年8月欣旺达有什么动态？"), cfg)
        assert turn1["answer"] == "老链路答案：2026年8月欣旺达有什么动态？"
        assert turn1["retrieved_documents"][0]["title"] == "2026年8月欣旺达有什么动态？"

        turn2 = app.invoke(create_agent_state(thread, "润泽科技最近有什么动态？"), cfg)
        assert len(calls) == 2, f"第二轮必须重新检索，实际检索次数={len(calls)}"
        assert turn2["answer"] == "老链路答案：润泽科技最近有什么动态？"
        assert turn2["retrieved_documents"][0]["title"] == "润泽科技最近有什么动态？", turn2[
            "retrieved_documents"
        ]

        # 会话级字段（document_analysis 等）要保留；轮次级字段（证据/回答/暂停原因）要清空
        assert turn2["supervisor_turns"] == 1, turn2["supervisor_turns"]
    finally:
        node_knowledge_module.answer_with_legacy_chain = original_search
        node_supervisor_module.decide = original_decide

    # 第二轮上传新文件 → 重新理解，并保留第一轮的会话态
    node_supervisor_module.decide = lambda state: SupervisorDecision(
        next_action="document" if not state.get("document_processed") else "ask_user",
        reason="测试",
    )
    try:
        app = build_agent_graph(InMemorySaver())
        thread = "test-lifecycle-doc"
        cfg = {"configurable": {"thread_id": thread}}
        inquiry = save_upload(INQUIRY_TEXT.encode("utf-8"), "询证函.png", "image/png")
        license_file = save_upload(INQUIRY_TEXT.encode("utf-8"), "business_license.png", "image/png")

        # 第一轮：询证函 + 要地址（任务类型由主 Agent 给）
        state1 = create_agent_state(thread, "帮我提取邮寄地址", attachments=[inquiry.to_dict()])
        state1["document_action"] = "extract_mailing_address"
        first = app.invoke(state1, cfg)
        assert first["document_type"] == "inquiry_letter", first.get("document_type")
        assert first["document_reuse"] is False
        assert first["candidate_addresses"], "第一轮的地址候选应当保留在 State 里"
        assert first["document_task_result"]["status"] == "success", first["document_task_result"]

        state2 = create_agent_state(
            thread, "这份营业执照帮我看看", attachments=[license_file.to_dict()]
        )
        state2["document_action"] = "extract_company_info"
        second = app.invoke(state2, cfg)
        assert second["document_type"] == "business_license", second.get("document_type")
        assert second["document_fields"]["unified_social_credit_code"] == "91320100MA1XXXXX9K"
        # 换新文件后地址候选必须来自新文件，不能残留上一份询证函的回函地址
        assert second["candidate_addresses"], second["candidate_addresses"]
        assert all("江宁" in value for value in second["candidate_addresses"]), second[
            "candidate_addresses"
        ]
        assert second["document_facts"]["company_name"] == "江苏欣旺达新能源科技有限公司"
        assert second["document_task_result"]["data"]["province"] == "江苏省"
    finally:
        node_supervisor_module.decide = supervisor_module.decide
    print("[PASS] State 生命周期（轮次清空 / 会话保留 / 换文件重理解）OK")


def main():
    _install_stubs()
    test_document_subgraph_address_task()
    test_document_subgraph_other_tasks()
    test_document_subgraph_unsupported_and_no_action()
    test_document_subgraph_address_ambiguous_hitl()
    test_document_subgraph_reuse_same_file()
    test_document_decides_action_from_task()
    test_main_graph_document_task_contract()
    test_application_single_candidate()
    test_application_multi_candidate()
    test_application_confirm_with_extra_fields_loops_back()
    test_application_confirm_corrects_company_name()
    test_license_to_application_merge()
    test_docx_single_template_and_page_count()
    test_caihui_company_adapter()
    test_supervisor_fallback_and_routing()
    test_company_name_matching()
    test_main_graph_knowledge_uses_legacy_chain()
    test_main_graph_application_hitl()
    test_file_card_only_in_generating_turn()
    test_state_lifecycle_between_turns()
    print("\n全部自检通过 ✅")


if __name__ == "__main__":
    main()
