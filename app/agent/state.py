"""Main Agent 的统一状态（AgentState）。

理解 State 之前先记住三条规则，字段的语义都由它们决定：

1. **State 是按 thread_id 持久化的**。
   Main Agent 用 checkpointer（当前是 InMemorySaver）按 thread_id 存整份 State，
   所以上一轮写进去的键，下一轮默认还在。这就是"用户上一轮上传了询证函，
   这一轮直接问'联系人是谁'"能回答的原因。

2. **每一轮 invoke 只覆盖「传进来的键」**。
   API 每轮用 create_agent_state(...) 构造初始状态，里面出现的键会被覆盖，
   没出现的键保留上一轮的值。所以字段分三类：
       - 请求级：每轮必传（thread_id / request_text / attachments / is_stream ...）
       - 轮次级：每轮重置为空（answer / retrieved_documents / interrupt_* ...）
       - 会话级：跨轮保留（document_analysis / candidate_addresses /
                   selected_company / application_data / user_fields /
                   generated_file_* ...）
   轮次级的重置清单集中在 create_agent_state 的 _TURN_RESET 里。

3. **键集合是父图与两个子图的超集**。
   LangGraph 的子图作为节点被调用时，写入的键必须存在于父图 schema 中，
   否则合并状态会失败。因此 DocumentState / ApplicationState 只声明
   AgentState 的子集，所有子图字段都在这里集中定义。

另外保留 session_id / is_stream / original_query 三个旧字段：
现有 RAG 检索节点（node_item_name_confirm 等）直接读这三个键，
复用它们就能零改动地保住已经调通的检索链路。
"""
import copy
from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    # ======================= 一、请求级（每轮由 API 传入） =======================

    # 会话/线程 ID。同时充当三处角色：
    #   1) checkpointer 的 thread_id（State 持久化维度）
    #   2) 短期记忆的 scope（run:{thread_id}）
    #   3) SSE 队列的 session_id（进度与答案推送）
    thread_id: str
    # 用户标识。长期记忆按它聚合（跨对话共享），不传则长期记忆退化为会话级。
    user_id: str
    # 本轮用户原话。Supervisor 决策、申请书参数解析、回答节点都用它。
    request_text: str
    # 预留字段（当前未使用）：留给后续接入 LangChain 消息列表式上下文。
    messages: list
    # 兼容字段 = thread_id：现有 RAG 节点用 session_id 读短期记忆、推 SSE 进度。
    session_id: str
    # 兼容字段 = request_text：现有 RAG 节点（Query Analyzer）的入参名。
    original_query: str
    # 是否流式：True 时节点把回答推 SSE delta，False 时写任务结果（同步接口取）。
    is_stream: bool
    # 短期记忆快照。由 RAG 检索节点写入；回答节点不读它（自己重新读记忆，取更新）。
    history: list

    # ======================= 二、Supervisor 决策（轮次级） =======================

    # 本轮最近一次决策的 next_action，对外的"意图"字段（响应体里会带）。
    intent: str
    # 本轮最近一次决策的 next_action，供路由函数 route_after_supervisor 消费。
    # 与 intent 同值，分开是为了让"路由"和"对外展示"各自演进。
    route: str
    # 本轮已决策次数（每轮重置）。达到 AGENT_MAX_SUPERVISOR_TURNS 后强制收敛，防死循环。
    supervisor_turns: int
    # 本轮每次决策的完整快照（模型原始输出），用于 trace、复盘和评测。
    supervisor_decisions: list
    # 最近一次决策的理由（决策可解释性，出问题时先看它）。
    decision_reason: str

    # ======================= 三、附件（请求级，每轮覆盖） =======================

    # 本轮附件：[{file_id, filename, mime_type}]。只取第一个做文档理解；
    # 注意它会每轮覆盖，所以"上一轮传的文件"不会在这里残留，
    # 要靠 document_analysis / candidate_addresses 这类会话级字段承接。
    attachments: list

    # ======================= 四、Document（会话级：跨轮保留） =======================
    # 保留的意义：用户上传一次文件后，后续轮次可以继续问"这个文件里的联系人是谁"。
    # 新一轮上传新文件时，document_processed 会被重置为 False，于是重新走一遍 OCR。

    # 本轮附件的元数据 + 本地路径：{file_id, filename, mime_type, size, path}。
    document_file: dict
    # 本轮任务契约：{file_id, action}。主 Agent 决定"做什么"，子图只执行、不猜意图。
    document_task: dict
    # 本轮是否复用已理解过的文件：True = 没有新附件，只想对同一份文件执行另一个任务
    document_reuse: bool
    # OCR 原始结果：{document_id, full_text, pages, confidence, provider}。
    document_ocr_raw: dict
    # 截断后的正文（按 AGENT_DOCUMENT_MAX_CHARS），分类/抽取/摘要都基于它。
    document_text: str
    # OCR 页结构（含 blocks/bbox/confidence）。当前只写不读，预留给前端展面与原图定位。
    document_pages: list
    # OCR 置信度（0~1）。未返回时是 0，代表"厂商没给"，不代表识别失败。
    document_confidence: float
    # 分类模型的原始输出：{document_type, confidence, reason}。
    document_classification: dict
    # 分类结果，取值：inquiry_letter / business_license / generic_document / unknown。
    document_type: str
    # 抽取到的结构化字段（企业名称、统一社会信用代码、注册地址、法人、日期…）。
    # 只保留文本中真实出现的内容；信用代码必须是合法 18 位，否则丢弃。
    document_fields: dict
    # 地址候选（已按业务标签优先级排序）；address_labels 是它的标签映射。
    candidate_addresses: list
    address_labels: dict
    # 地址优选结论：single（唯一或高优先级明显领先）/ ambiguous / none + 优选地址
    address_decision: str
    preferred_address: str
    # 文档摘要（3~5 句）。
    document_summary: str
    # 给 Main Agent 的完整结构（DocumentAnalysis 的全部字段：type/confidence/
    # structured_fields/candidate_addresses/available_actions/summary/error…）。
    # Supervisor 的第二次决策、回答节点、反问文案都读它。
    document_analysis: dict
    # 可复用的文档事实（8 个通用字段 + 候选地址 + raw_ref），给主 Agent 与申请书子图消费
    document_facts: dict
    # 本轮文档任务结果：{action, status, address_decision, preferred_address, data, reason}
    document_task_result: dict
    # 主 Agent 交代的"用户目标"陈述（本轮，document 分支由 node_supervisor 写）。
    # 这是重构后主 Agent 对文档能力**唯一的输入**：它只讲用户要什么
    # （"提取这份询证函中可用于寄送的地址"），不讲怎么做。
    document_task_text: str
    # Document Agent 自己判定的动作（本轮，node_understand_document 写）。
    # 取值同 document_action；判不出来是 "none" —— 由主 Agent 反问用户，不许猜。
    resolved_action: str
    # **显式覆盖**通道（本轮）：测试 / 评测 / 老客户端直接指定动作时用。
    # 生产链路（Supervisor → 子图）不再写它；子图优先采用 resolved_action。
    document_action: str
    # 请求侧事实（本轮）：主 Agent 从用户话里抽出的结构化线索（document_id、用户明确
    # 给出的企业名/字段值）。只放事实，不放执行计划。
    request_context: dict
    # 本轮附件是否已经理解完（每轮重置 False）。路由用它防止重复 OCR，
    # 也是 document 分支不重复调用工具的判断依据。
    document_processed: bool
    # 理解失败的原因：no_attachment / attachment_not_found / ocr_unavailable /
    # ocr_failed / ocr_empty_text。回答节点据此决定反问还是让用户重传。
    document_error: str

    # 子 Agent 的上行汇报（本轮最近一次，见 schemas/agent_result.py）：
    # {source, status, task_type, result, message, artifacts, reason}。
    # 主 Agent 只按 status 决策（success / need_user_input / failed），不解析 result 内部字段。
    agent_result: dict
    # 各子 Agent 最近一次的汇报（会话级，按 source 归档），用于跨轮溯源与前端展示。
    agent_results: dict

    # ======================= 五、RAG（轮次级：每轮重新检索） =======================

    # 实际用于检索的查询（Query Analyzer 改写后的），用于追溯"是不是改写错了"。
    rag_query: str
    # 实际生效的结构化过滤条件（时间/分类/领域/主体），供日志与问题定位。
    rag_filters: dict
    # 检索回来的证据列表（node_knowledge 写；Supervisor 与回答节点读）。
    # 注意：必须每轮清空，否则上一轮的证据会让新一轮的检索被短路（会答旧资料）。
    retrieved_documents: list

    # ======================= 六、Company（会话级：跨轮保留） =======================
    # 保留的意义：多候选时用户下一轮才回复"2"，选中结果要能落在同一份 State 上。

    # 用户原话里的企业名称/简称（不做补全，补全交给 MCP 检索）。
    company_query: str
    # 企业 MCP 返回、并按名称相似度排过序的候选（含 name_similarity 与主体字段）。
    # 排序只决定展示顺序；能不能自动选中由 pick_company 自己判定（不看顺序）。
    company_candidates: list
    # 最终确定的企业主体。**企业事实的唯一权威来源**：
    # 全称、统一社会信用代码、省份、注册地址都取自这里，LLM 不得自己生成。
    selected_company: dict
    # 候选来源：company_mcp / unconfigured / error / user_input（用户手工补的）。
    company_source: str

    # ======================= 七、Application（会话级 + 阶段机） =======================

    # 已收集的申请书字段（company_name / unified_social_credit_code / province /
    # registered_address / city / application_date / notes）。
    # 合并优先级：文档 OCR → Company MCP → 用户补充（后者覆盖前者）。
    application_data: dict
    # 第一版必填字段清单（company_name / unified_social_credit_code / province）。
    required_fields: list
    # 本轮缺失的必填字段。反问文案直接读它，缺字段的上限由 field_ask_rounds 控制。
    missing_fields: list
    # 用户明确给出的字段值（优先级最高，跨轮保留，用户纠正过的不再被文档覆盖）。
    user_fields: dict
    # 本轮确认环节是否新补了字段（轮次级）。申请书子图的回边判据用它而不是上面那个
    # 累积字段——否则用户补过一次字段后就永远回不到"生成"这一步。
    user_fields_updated: bool
    # 申请日期（默认今天，YYYY-MM-DD），渲染进模板。
    application_date: str
    # 申请书子图的阶段机，回答节点据此决定"报结果"还是"反问"：
    #   parsed / company_resolved / company_selected / need_company_choice /
    #   need_user_input / template_ready / generated / cancelled / error
    application_phase: str
    # 本轮是否已经跑过一次申请书子图（每轮重置 False）。路由用它判断"申请书工具本轮
    # 是不是已经调过"，防止 Supervisor 第二次决策又选 application 时重复进子图。
    application_processed: bool
    # 缺字段补充轮次（每轮重置，所以它是"单轮保护"而不是累计计数）。
    field_ask_rounds: int
    # 选中的模板类型：jiangsu（注册地江苏）/ other（其它地区）。
    template_type: str
    # 模板文件绝对路径（当前只写不读：渲染时由 docx_service 按省份重新定位）。
    template_path: str

    # ======================= 八、产物与 HITL =======================

    # 生成物：磁盘绝对路径 / 文件名 / 下载地址（/api/agent/files/{name}）。
    # 三个都是会话级，API 用它们把响应拼成 {"type":"file", ...}。
    generated_file_path: str
    generated_file_name: str
    generated_file_url: str
    # 本轮是否**真的渲染出了文件**（轮次级，每轮重置）。
    # 【为什么必须有它】generated_file_* 是会话级的（跨轮保留，供后续轮次引用），
    # 拿它判"这轮要不要给文件卡片"会让生成过一次之后，之后每轮回答都带下载卡片。
    generated_this_turn: bool
    # 暂停原因：multiple_company_candidates / confirm_before_generate /
    # missing_application_fields / company_not_found / invalid_company_selection…
    # 它是"为什么问用户"的机器可读标识。
    interrupt_reason: str
    # 暂停时给用户看的载荷（候选列表、待确认字段、缺失字段…），
    # 同时作为 LangGraph interrupt(value) 的值，前端可结构化渲染。
    interrupt_payload: dict
    # 需要用户补充的信息清单（Supervisor 或子图写，反问文案读）。
    missing_info: list
    # 本轮是否以"等用户确认"结束。当前只写不读：供 /api/agent/state 观察与前端状态展示，
    # 真正的暂停判断用 LangGraph 的 __interrupt__ / snapshot.next。
    awaiting_user_confirmation: bool
    # 恢复后清空的占位（当前只写不读）：resume 值由 LangGraph 直接交给 interrupt() 所在节点。
    resume_value: Any

    # ======================= 九、出口（轮次级） =======================

    # 最终回答文本（node_answer / node_ask_user 写）。写短期记忆与 API 响应都读它。
    answer: str
    # 回答里要回传的图片地址（从检索证据的 Markdown / url 字段提取）。
    image_urls: list
    # 本轮错误信息（检索失败 / 文档理解失败 / 渲染失败）。反问与回答节点读它，
    # 决定是"如实说明失败"还是"继续正常回答"。
    error: str
    # 用户消息是否已写入短期记忆。知识分支由 RAG 检索节点写（它自己会写），
    # 其它分支由 memory_bridge 补写，用这个标记避免同一轮写两次。
    user_message_recorded: bool


# 每一轮开始时需要重置的字段：它们只描述"本轮"的过程，跨轮残留会导致误判。
# 最典型的坑：上一轮的 retrieved_documents 留着，路由会以为"本轮已经检索过"，
# 于是跳过 node_knowledge 直接回答旧证据。
#
# 不在这个清单里的字段（document_analysis / candidate_addresses / address_labels /
# selected_company / company_query / company_candidates / application_data /
# required_fields / user_fields / application_phase / template_* / generated_file_* /
# document_type / document_fields / document_summary …）都是**会话级**，
# 跨轮保留才能支持"接着上一轮的文件继续问"和"两轮内补充信息"。
_TURN_RESET: dict = {
    # --- Supervisor 决策 ---
    "intent": "",
    "route": "",
    "decision_reason": "",
    "supervisor_turns": 0,
    "supervisor_decisions": [],
    # --- Document（本轮的过程态） ---
    "document_task": {},
    "document_task_text": "",
    "document_reuse": False,
    "document_file": {},
    "document_ocr_raw": {},
    "document_action": "",
    "resolved_action": "",
    "request_context": {},
    "document_processed": False,
    "document_error": "",
    "address_decision": "",
    "preferred_address": "",
    # --- 子 Agent 上行汇报（本轮最近一次；agent_results 是会话级的，不在这里重置） ---
    "agent_result": {},
    # --- RAG（必须每轮重检，否则会拿旧证据回答新问题） ---
    "rag_query": "",
    "rag_filters": {},
    "retrieved_documents": [],
    # --- Application 的本轮过程态 ---
    "missing_fields": [],
    "user_fields_updated": False,
    "field_ask_rounds": 0,
    "application_processed": False,
    # 本轮是否真的出了文件（会话级的 generated_file_* 不做重置，见字段注释）
    "generated_this_turn": False,
    # --- HITL ---
    "interrupt_reason": "",
    "interrupt_payload": {},
    "missing_info": [],
    "awaiting_user_confirmation": False,
    "resume_value": None,
    # --- 出口 ---
    "answer": "",
    "image_urls": [],
    "error": "",
    "user_message_recorded": False,
    # --- 兼容字段 ---
    "history": [],
}


def create_agent_state(
    thread_id: str,
    request_text: str,
    user_id: str = "",
    attachments: list | None = None,
    is_stream: bool = False,
) -> AgentState:
    """构造一轮对话的初始状态。

    先铺轮次级默认值（清掉上一轮的过程态），再写入本轮的请求级字段。
    没在这里出现的键（会话级字段）会保留 checkpointer 里的上一轮值——
    这是刻意的，多轮追问与 HITL 都依赖它。
    """
    state: AgentState = copy.deepcopy(_TURN_RESET)
    state.update(
        {
        "thread_id": thread_id,
        "session_id": thread_id,
        "user_id": user_id or "",
        "request_text": request_text,
        "original_query": request_text,
        "messages": [],
        "attachments": attachments or [],
        "is_stream": is_stream,
        }
    )
    return state
