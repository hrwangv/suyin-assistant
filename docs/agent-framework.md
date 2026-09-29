# 企业业务智能 Agent —— 改造说明与接入指南

对应实施规范《企业业务智能 Agent / LangGraph + RAG + OCR MCP + 企业信息 MCP + 文档生成》。

改造原则：**新增为主、存量少动**。已经调通的导入流水线、检索流水线、记忆系统
全部原样保留，Agent 层通过复用它们的函数/节点来接入，不做重写。

---

## 1. 改造后是什么样

```text
                        用户
                         │
         ┌───────────────┴────────────────┐
         │ 文本                            │ 图片（PNG / JPEG）
         ▼                                 ▼
  POST /api/agent/chat          POST /api/agent/upload → file_id
         └───────────────┬────────────────┘
                         ▼
                 Main Agent（Supervisor）
        任务理解 / 结构化决策 / 状态管理 / 多轮编排
                         │
        ┌────────────────┼────────────────┐
        ▼                ▼                ▼
  search_suyin_    process_document   generate_business_
  knowledge        (Document 子图)     application（Application 子图）
        │                │                │
        ▼                ▼                ▼
   现有 RAG 链路     OCR MCP（外部）    Company MCP（外部）
   (Qdrant+BM25+    ↓ 兜底：VL/MinerU   ↓ + DOCX 模板
    RRF+Rerank)                         ↓ Human-in-the-loop
        │                │                │
        └────────────────┴────────────────┘
                         ▼
                    node_answer
                  （统一组织回答）
```

三个已有项目现在的定位：

| 原项目 | 现在的角色 | 实现位置 |
| --- | --- | --- |
| 苏银晨报 RAG | **Knowledge Skill**：复用老 RAG 全链路（含 `answer_output`），答案与 `/query` 一致 | `app/agent/services/rag_service.py` + `app/rag/query_process/agent/main_graph.py` |
| 易拍寄 OCR | **Document Skill**：文档理解子图，OCR 走外部 MCP | `app/agent/subgraphs/document_understanding.py` |
| 业务申请书 | **Application Skill**：带 HITL 的申请书子图 | `app/agent/subgraphs/business_application.py` |

---

## 2. 文件清单（便于 review）

### 2.1 存量代码改动（只有三处，且都很小）

| 文件 | 改动 | 说明 |
| --- | --- | --- |
| `app/api/query_server.py` | +4 行 | 挂载 `agent_router`，用一个 `AGENT_ENABLED` 开关控制 |
| `app/conf/mcp_config.py` | 不变 | MCP 连接信息**不放这里**：统一写在 `app/agent/mcp/builtin.py`；本文件继续只服务老 RAG 链路 |
| `requirements.txt` / `.env.example` | +若干行 | 新增依赖与配置样例 |

其余全部是新增文件，不改动任何已有函数签名与行为。

### 2.2 新增文件

```text
app/agent/
├── graph.py                        Main Agent 主图（编译 + HITL 恢复 + 对外返回结构）
├── supervisor.py                   结构化决策（SupervisorDecision）+ 规则兜底
├── routing.py                      条件路由 + 循环保护（同类工具不重复调用 / 决策次数上限）
├── state.py                        统一 AgentState（父图 ⊇ 两个子图）
├── events.py                       SSE 事件名 + 推送封装
├── llm.py                          json_mode + Pydantic 的结构化调用封装
├── config_helpers.py
├── nodes/
│   ├── node_supervisor.py          决策节点
│   ├── node_knowledge.py           复用老 RAG 全链路（answer_output），把答案与证据写回 State
│   ├── node_answer.py              统一组织回答（含 token 预算裁剪、流式输出、写记忆）
│   ├── node_ask_user.py            反问 / 需要用户补充时结束本轮
│   └── clarify.py                  反问文案与 HITL 提示文案
├── subgraphs/
│   ├── document_understanding.py   按 resolved_action 分叉：共享前置 → 地址优选 / 摘要 / 企业信息 → facts + task_result
│   └── business_application.py     parse → resolve_company → merge → collect → template → HITL → DOCX
├── schemas/                        SupervisorDecision / AgentResult（上行契约）/ DocumentAnalysis / CompanyCandidate / ApplicationData
├── services/
│   ├── rag_service.py              检索子图（复用现有节点，跑到 rerank 为止）
│   ├── document_service.py         一次理解（分类/字段/地址/摘要）+ 地址优选 + 规则兜底
│   ├── ocr_providers.py            OCR 唯一通路：外部 OCR MCP（未配置则 OCRUnavailable）
│   ├── company_service.py          企业名称标准化、相似度、排序、自动选中判定
│   ├── docx_service.py             模板选择 + DOCX 渲染（docxtpl 优先，python-docx 兜底）
│   ├── file_store.py               会话附件存储（output/agent_files/，不进知识库）+ COS 公网 URL
│   └── memory_bridge.py            复用现有短期记忆写入 / 长期记忆抽取
└── mcp/
    ├── client.py                   通用 MCP 客户端（streamable_http / sse）+ 返回结构解析
    ├── builtin.py                  MCP 服务声明（有哪些 / 连哪里 / 工具名 / 入参形态）
    ├── ocr_client.py               OCR MCP 适配 + 结果规范化
    ├── company_client.py           Company MCP 适配 + 候选规范化
    └── registry.py                 MCP 服务注册表（加载声明 / 启停 / 探测工具 / 调用）

app/agent/tools/                    工具箱：工具的注册 / 管理 / 调用
├── registry.py                     @tool 装饰器 + 全局工具注册表（入参 schema、元数据）
├── buildin.py                      内置小工具：计算器 / 当前时间 / 文本统计 / 随机挑选
├── service.py                      统一入口：清单 / schema / 调用 / MCP 工具注册
└── router.py                       /api/agent/toolbox/* 管理与调试接口

app/api/agent.py                    统一入口 / 附件上传 / SSE / 文件下载 / MCP 自检
app/conf/agent_config.py            Agent 全部开关与目录配置
prompts/*.prompt                    9 个新增提示词（supervisor / answer / ask_user / document_* / application_*）
templates/*.docx                    江苏省版、其它地区版业务申请书模板
scripts/make_application_templates.py  模板生成脚本（改模板字段后重跑）
app/test/test_agent_framework.py    离线自检（16 项，含 HITL 恢复、子 Agent 自判动作、确认时补字段回边）
app/test/test_tool_registry.py      工具箱离线自检（9 项，含 MCP 工具注册与调用）
app/test/test_supervisor_contract.py 契约守卫（5 项）：Supervisor 不得出现子 Agent 的能力名
evaluation/agent/                   Routing / Company / Document 三套评测
page/src/api/agent.js               前端 Agent 客户端（不改动现有页面逻辑，供后续切换使用）
```

---

## 3. Main Agent 决策

决策只用结构化输出，**不做字符串匹配**：

```python
class SupervisorDecision(BaseModel):
    next_action: Literal["knowledge", "document", "application", "answer", "ask_user"]
    task: str          # "提取这份询证函中可用于寄送的地址"（用户目标，不是能力名）
    context: dict      # 请求侧事实：document_id / 用户明确给出的字段
    reason: str
    confidence: float
    missing_info: list[str]
```

> **契约（2026-09 改造）**：Supervisor 只说"用户要什么"（`next_action` + `task` + `context`），
> **不再产出子 Agent 的动作名**。动作（`resolved_action`）由 Document Agent 在同一次理解
> 调用里自己判定；子 Agent 跑完用 `AgentResult{status, result, message, artifacts}` 回报。
> 详见 `docs/Supervisor契约改造设计.md`，守卫测试 `app/test/test_supervisor_contract.py`。
> 迁移期仍保留 `AgentState.document_action` 作为**显式覆盖**通道（测试 / 评测用），
> 但主图不再写它。

图上的走向：

```text
START → node_supervisor
          ├── knowledge   → node_knowledge  → END（内部复用老 RAG 全链路，答案已产出）
          ├── document    → document_skill  → 回到 supervisor（文档理解完成后的第二次决策）
          ├── application → application_skill（内部可暂停） → 回到 supervisor（第二次决策）
          ├── answer      → node_answer → END
          └── ask_user    → node_ask_user → END
```

两道循环保护（`app/agent/routing.py`）：

1. knowledge 分支一次到底（复用老链路 → END），不受决策次数约束；
2. document 分支：同一份文件重复进入子图时复用已理解结果（只跑专项分支）；
   application 分支：本轮跑过一次子图（`application_processed`）后，路由收敛到
   `node_answer`，不会重复查询企业 MCP 或二次渲染 DOCX；
   `AGENT_MAX_SUPERVISOR_TURNS`（默认 4）用满后强制收敛到 `node_answer` 或 `node_ask_user`。

两个子图的出口都回到 `node_supervisor`：子图只产出结构化结果（文档事实 / 任务结果 /
申请书阶段），"结果够不够回答用户、还要不要再补信息"由主 Agent 做第二次决策。
`node_knowledge` 是唯一例外——老 RAG 链路自己产出了最终答案，直接 `END`。

模型不可用时（网络/网关异常）走 `supervisor.fallback_decision` 的规则兜底，
仍然保证「有附件先理解、有证据先回答、缺信息就反问」，不会编造企业信息。

### 3.1 State（`app/agent/state.py`）

字段分组与规范第 7 节一致：请求信息 / Supervisor 决策 / 附件 / Document /
RAG / Company / Application / 产物与 HITL / 出口。

**每个字段的逐行注释都在 `app/agent/state.py` 里**（含义、谁写、谁读）。
理解它只需要先记住三条规则：

1. State 按 `thread_id` 存在 checkpointer 里，上一轮写进去的键下一轮默认还在；
2. 每轮 `invoke` 只覆盖「传进来的键」，所以字段分三类生命周期；
3. 子图（Document / Application）只能声明并写入 AgentState 里已有的键，
   否则父图合并状态会失败。

| 生命周期 | 什么时候变 | 典型字段 |
| --- | --- | --- |
| 请求级 | 每轮由 API 传入并覆盖 | `thread_id` `user_id` `request_text` `attachments` `is_stream`（以及兼容字段 `session_id` `original_query`） |
| 轮次级 | 每轮重置（`create_agent_state` 的 `_TURN_RESET`） | `answer` `error` `image_urls`、`intent` `route` `decision_reason` `supervisor_turns` `supervisor_decisions`、`retrieved_documents` `rag_query` `rag_filters`、`document_task_text` `resolved_action` `document_action` `request_context` `document_task` `document_reuse` `document_processed` `document_error` `document_file` `document_ocr_raw` `address_decision` `preferred_address`、`agent_result`、`missing_fields` `field_ask_rounds` `application_processed`、`interrupt_reason` `interrupt_payload` `missing_info` `awaiting_user_confirmation` `resume_value`、`user_message_recorded` `history` |
| 会话级 | 跨轮保留 | `document_analysis` `document_facts` `document_task_result` `document_type` `document_fields` `candidate_addresses` `address_labels` `document_summary`、`agent_results`、`company_query` `company_candidates` `selected_company` `company_source`、`application_data` `required_fields` `user_fields` `application_phase` `template_type` `template_path` `generated_file_*` |

为什么必须这么分：上一轮的 `retrieved_documents` 如果留着，`route_after_supervisor`
会以为「本轮已经检索过」，于是跳过 `node_knowledge` 直接拿旧证据回答；
而 `candidate_addresses` / `company_candidates` 这类字段又恰恰要靠跨轮保留，
才能在「用户下一轮才回复选哪个」时把结果落回同一份 State。

两个实现细节：

- `session_id`、`original_query`、`is_stream` 三个老字段保留并镜像自
  `thread_id`、`request_text`，用于零改动复用现有检索节点；
- 父图 State 是子图的超集，子图只写入父图声明过的键（LangGraph 的状态合并要求）。

---

## 4. 三个能力

### 4.1 Knowledge：复用老 RAG 全链路

Agent 的 knowledge 分支**直接复用编译好的老图 `query_app`**（`app/agent/services/rag_service.py`
里的 `answer_with_legacy_chain`），链路一次跑到底：

```text
node_item_name_confirm（Query Analyzer：改写 + 结构化条件 + 写用户消息）
   ↓
三路并行召回：node_search_embedding / node_search_embedding_hyde / node_web_search_mcp
   ↓
node_rrf → node_rerank
   ↓
node_answer_output  ← 长期记忆注入 + answer_out.prompt + 图片回传 + 写短期记忆 + 触发长期抽取
```

**为什么走到底而不是只取证据**：知识问答的答案必须带长期记忆、走 `answer_out.prompt`，
而且评测（`evaluation/answering.py`）对齐的就是这条链路。所以 Agent 这边不再另写答案
生成——`/query` 与 Agent 的 knowledge 分支跑的是**同一张编译图**，答案逐字一致。

> 说明：早期版本这里还有一个 `search_suyin_knowledge()`（只跑到 rerank、只返回证据）
> 和 `app/agent/tools/` 三个 `@tool` 骨架。主流程改成"复用整条老链路"之后，那条重复的
> 入口路径已删除——RAG / 文档 / 申请书三件事的编排，统一交给主图与两个子图。
> （现在 `app/agent/tools/` 是后来新增的**工具箱**，里面只放通用小工具，
> 不再承载任何业务能力，见 5.5 节。）

### 4.2 Document：Document Understanding Subgraph

```text
START → node_prepare_document（定位文件 + 复用判断 + 需要时 OCR）
          ├─(复用已理解)──────► node_understand_document ──┐
          ├─(新附件，OCR 成功)─► node_understand_document ──┤
          └─(出错 / 无附件)─────────────────────────────────┤
                                                            ▼
                               node_confirm_address（仅地址歧义时 interrupt）
                                                            │
                                                            ▼
                                            node_finalize_document → END

4 个节点。原 node_load_document 与 node_run_ocr 已合并为 node_prepare_document：
两者之间只有一条"要不要真的调 OCR"的分叉，且都不含 interrupt。

输入是结构化任务（主 Agent 只给 document_task_text 用户目标），子图不读用户原话、
不猜意图：主 Agent 说"用户要什么"，子图自己判定动作（resolved_action）并给出结果。

两个关键约定（改造后）：

1. **一次理解，全部产出**：分类 / 通用字段 / 地址候选 / 摘要在同一次模型调用里产出
   （`document_service.understand_document`），新文件只花一次模型调用（改造前最多三次）。
   于是任何 action 的执行都只是"读这些字段"，同一份文件换任务复用时不必重新调模型。
2. **地址优选是确定性规则**（`pick_mailing_address`），不交给模型；歧义时的 `interrupt`
   仍然单独放在 `node_confirm_address` 里，避免恢复时重放副作用。
```

v1 能力白名单（不做"万能文档 Agent"）：

| 维度 | 只允许 |
| --- | --- |
| 文档类型 | 询证函 / 营业执照 / 普通业务文档 / unknown（分不出类型时的兜底） |
| 通用字段（8） | 公司名称、统一社会信用代码、地址、法人、联系人、电话、日期、金额（另保留省市供申请书用） |
| 专项能力 | `extract_mailing_address` / `extract_company_info` / `summarize_document`（+ `none` = 只上传没说要干什么） |
| 超出白名单 | 返回 `task_result.status = unsupported`，由主 Agent 决定怎么回复，**不许硬抽** |

输出分两层（与主 Agent 的契约）：

```json
{
  "document_facts": {
    "document_type": "inquiry_letter",
    "company_name": null, "unified_social_credit_code": null,
    "address": null, "province": null, "city": null, "legal_person": null,
    "contact_person": "王会计", "contact_phone": "025-88886666",
    "document_date": "2026-08-31", "amount": "1,234,567.89",
    "candidate_addresses": [{"value": "江苏省南京市…", "label": "回函地址", "priority": 20}],
    "raw_ref": "output/agent_files/file_xxx/询证函.png"
  },
  "document_task_result": {
    "action": "extract_mailing_address",
    "status": "success",
    "address_decision": "single",
    "preferred_address": "江苏省南京市鼓楼区中山路1号苏银大厦12层",
    "data": {"address": "…"},
    "reason": null
  }
}
```

- `document_facts` = 文件里有什么，**可复用**（申请书子图直接消费）
- `document_task_result` = 这次任务做得怎么样，主 Agent **只读它**决定：直接回答 / 发起 HITL / 换动作 / 转申请书
- `status` 取值：`success` / `ambiguous` / `not_found` / `unsupported` / `failed` / `none`

地址优选在子图内完成（`pick_mailing_address`，优先级按规范第 20 节
`邮寄地址 > 回函地址 > 通讯地址 > 办公地址 > 注册地址`）：高优先级明显领先 → 直接给
`preferred_address`；同优先级多个 → `ambiguous` 并 `interrupt` 让用户选；一个都没有 → `not_found`。
**主 Agent 不再从多个地址里挑一个。**

多轮：同一份文件换任务（先总结、再要地址）时，`load_document` 发现
`document_analysis` 里已有同一 `document_id` → `document_reuse=True`，
跳过 OCR 与模型调用，直接用 State 里已有的理解结果重算本轮 action 的结论。

**只上传文件、用户没说做什么**（`action=none`）时，子图只做通用理解并返回
`status=none`，由主 Agent 按规范第 21 节反问用户；
如果希望强化易拍寄老业务，把 `AGENT_DEFAULT_INQUIRY_ACTION=extract_mailing_address`
打开，就会自动执行地址提取。

### 4.3 Application：Business Application Subgraph

```text
parse_application_request
   ↓
resolve_company（Company MCP）
   ├── 唯一候选 / 用户给的是完整企业名 → 继续
   ├── 多个候选 → node_company_select（interrupt，等用户选）
   └── 没找到     → 提示补全名称或上传营业执照
   ↓
merge_document_information（文档 OCR → MCP → 用户补充，后者覆盖前者）
   ↓
collect_required_fields（company_name / unified_social_credit_code / province）
   ├── 缺字段 → 反问 + 可上传营业执照补齐
   └── 齐全   → choose_template
   ↓
choose_template（只有一套模板；注册地江苏 / 省外只差 page_count —— 那一页的份数）
   ↓
human_confirmation（interrupt：生成前确认）
   ↓
render_docx → output/ 落盘 → /api/agent/files/{name} 下载
```

**模板与占位符**：`templates/苏银金租备案材料.docx` 是业务原件（不改）；
`templates/business_application.docx` 是渲染模板，由
`scripts/prepare_application_template.py` 从原件生成（把示例企业信息换成 `{{ 占位符 }}`），
改了原件要重跑一次。字段来源分三类：

| 类别 | 字段 | 来源 |
| --- | --- | --- |
| Agent 输出 | 企业名称 / 统一社会信用代码 / 注册地址 / 法定代表人 / 企业性质 / 注册资本 / 日期 | 营业执照抽取 或 企业 MCP 或 用户补充 |
| 固定文本 | 租赁物名称「设备」、租赁物状态「正常使用」 | 与原模板一致，不替换 |
| 空着手填 | 租赁物预估价值 / 融资用途 / 拟还款来源 / 签字盖章栏 | 原模板就是空的，保持空着 |

自动选中规则（`company_service.pick_company`）：**只有一个候选**，或**用户给的名称
标准化后与候选完全一致**（在候选里自己找那一个，不依赖排序位置）。
多个候选 + 用户只报简称（如"南京XX能源"）一律走 HITL。

---

## 5. 外部 MCP 接入（重点）

**MCP 的配置与信息都在代码里**：`app/agent/mcp/builtin.py`（对照 Yuxi 的
`backend/package/yuxi/agents/mcp/builtin.py`）。每个服务一条声明，写清
展示名、说明、传输方式、地址、鉴权头、超时、默认开关、工具名、入参适配：

```python
BUILTIN_MCP_SERVERS = {
    "ocr": {
        "name": "OCR MCP",
        "transport": "streamable_http",
        "url": "",                       # ← 服务地址直接写这里
        "url_env": "OCR_MCP_BASE_URL",   # ← 留空时回退读这个环境变量（本地/CI 覆盖）
        "api_key_env": "OCR_MCP_API_KEY",# ← 密钥只写"变量名"，不进仓库
        "auth_header": "x-api-key",
        "enabled": True,
        "tools": {"recognize": "GeneralOcrRecognition"},  # 语义名 → 服务端真实工具名
        "adapter": {"input_mode": "url", "args_template": {"pictureUrl": "{file_url}"}},
    },
    "company": {...},
    "yjt": {...},   # 企业预警通资讯（老 RAG 联网检索通路）
}
```

`.env` 只剩两件事：**密钥**（`OCR_MCP_API_KEY` / `COMPANY_MCP_API_KEY` / `YJT_API_KEY`）
和**可选的地址覆盖**（`OCR_MCP_BASE_URL` 等，给本地联调 / CI 用）。
换厂商、加服务、改工具名、改入参形态，都只改这一个文件。

查看当前状态：`GET /api/agent/tools` 会按声明列出每个 MCP 的地址、工具名、是否配齐。

### 5.1 OCR MCP

```text
工具名：builtin.py 里 ocr 的 tools.recognize（当前 GeneralOcrRecognition）
入参形态：builtin.py 里 ocr 的 adapter.input_mode（当前 url）
    base64 → {"file_base64": "...", "file_name": "...", "mime_type": "...", "document_id": "..."}
    url    → {"file_url": "...", "mime_type": "...", "document_id": "..."}   ← 当前：附件先传 COS
    path   → {"file_path": "...", "mime_type": "...", "document_id": "..."}
期望返回（能识别到就取，识别不到不报错）：
    {"document_id": "...", "full_text": "...", "confidence": 0.97,
     "pages": [{"page_number": 1, "text": "...", "blocks": [{"text": "...", "bbox": [..], "confidence": 0.98}]}]}
```

规范化逻辑（`app/agent/mcp/ocr_client.py`）会深度遍历返回结构，兼容
`full_text / fullText / text / markdown / content`、`pages / layouts / pageList`
等常见命名；字段名和厂商不一致时，改 builtin.py 里的 `adapter.args_template`：

```python
"adapter": {
    "input_mode": "base64",
    "args_template": {"image_base64": "{file_base64}", "doc_type": "image", "lang": "ch"},
}
# 支持占位符：{file_base64} {file_path} {file_url} {mime_type} {file_name} {document_id}
```

### 5.2 Company MCP

```text
工具名：builtin.py 里 company 的 tools.search（财汇：execute_tool）
入参：{"tool_name": adapter.sub_tool, "arguments": {…子工具参数…}}   # execute_tool 两步式
     或 {"query": "欣旺达", "limit": 10}                            # direct 单步厂商
期望返回：候选列表（对象数组 / 财汇的 headInfo + 二维数组 / 纯二维数组兜底）
    字段：company_name / unified_social_credit_code / registered_address / province / city
          / legal_person / enterprise_nature / registered_capital
    （厂商不返回分数；候选只按本地名称相似度排序，用于展示顺序）
```

字段别名兼容：`ent_name / entname / corp_name / cust_name / 企业名称` 等；
信用代码支持 `uscc / credit_code / reg_no / 统一社会信用代码`。
如果厂商返回的是二维数组（企业预警通风格），会按特征兜底识别
（18 位信用代码列、含"省/自治区"的省份列、含"路/号/区"的地址列）。

### 5.3 联调顺序（建议）

```bash
# 1. 到 app/agent/mcp/builtin.py 填地址（密钥放 .env），然后列出厂商实际暴露的工具名
curl "http://127.0.0.1:8001/api/agent/tools?probe=true" | python -m json.tool

# 2. 确认工具名对得上（不一致就改 builtin.py 里的 tools 映射）
# 3. 用真实文件走一次文档理解
#    先上传拿 file_id，再抽地址
curl -F "files=@询证函.jpg" http://127.0.0.1:8001/api/agent/upload
curl -X POST http://127.0.0.1:8001/api/agent/chat -H "Content-Type: application/json" \
     -d '{"thread_id":"t1","message":"帮我提取邮寄地址","attachments":[{"file_id":"file_xxx"}]}'
```

### 5.4 降级策略（重要：MCP 没配好也能跑通流程）

| 环节 | 有 MCP | 没有 MCP |
| --- | --- | --- |
| OCR | 调外部 OCR MCP | 图片走本地 VL 模型；PDF/Office 复用已有 MinerU 链路（`AGENT_OCR_PROVIDER=auto`） |
| 企业信息 | 调外部 Company MCP | 明确返回"未找到匹配企业"，引导用户补全名称或上传营业执照（绝不猜） |
| 文档语义 | 大模型 | 大模型不可用时退化为关键词分类 + 正则抽取 |
| 回答生成 | 大模型 | 反问 / 兜底文案，不编造事实 |

`AGENT_OCR_PROVIDER` 可选 `auto`（默认）/ `mcp` / `vl` / `none`，用来强制验证某一条通路。

---

## 5.5 工具箱：工具与 MCP 的注册 / 管理 / 调用

上面两节讲的是"Agent 内部怎么用 MCP"。工具箱（`app/agent/tools/` + `app/agent/mcp/registry.py`）
解决的是另一个问题：**有哪些工具、谁能用、怎么被调用**——上层（大模型 / 前端 / 脚本）
只需要一句"按名字调工具"，不需要知道它背后是本地函数还是外部 MCP。

```text
注册                          管理                          调用
@tool 装饰器（本地函数）  ─┐
MCP 服务探测到的工具      ─┼─►  工具注册表 / MCP 注册表  ─►  call_tool(name, arguments)
                           │    （清单、schema、启停）        └─ 本地：Pydantic 校验后执行
                           └─                                 └─ MCP ：转交 MCP 客户端调用
```

五个文件，各管一件事：

| 文件 | 职责 | 关键 API |
| --- | --- | --- |
| `app/agent/mcp/builtin.py` | MCP 声明（唯一配置处）：有哪些服务、连哪里、工具名、入参适配 | `BUILTIN_MCP_SERVERS` / `resolve_url` / `resolve_api_key` |
| `app/agent/tools/registry.py` | 本地工具的注册与描述：`@tool` 装饰器 + 全局注册表 | `tool` / `register_tool` / `get_tool` / `get_tool_metadata` |
| `app/agent/mcp/registry.py` | MCP 服务注册表：加载声明、启停、探测工具、按 slug 调用 | `register_server` / `list_servers` / `set_server_enabled` / `server_tools` / `call_mcp_tool` |
| `app/agent/tools/service.py` | 对外的统一入口（清单 / schema / 调用 / MCP 工具注册） | `list_tools` / `tool_schemas` / `call_tool` / `register_mcp_tools` / `run_tool_calls` |
| `app/agent/tools/router.py` | HTTP 管理与调试接口（挂在 `/api/agent/toolbox`） | 见下面的接口表 |

内置工具只有四个**纯功能性的小工具**，用来把链路跑通、给 review 看"注册 → 调用"长什么样：

| 工具名 | 做什么 | 特点 |
| --- | --- | --- |
| `calculator` | 数学表达式求值（`+ - * / // % **` 与 abs/round/min/max/sum） | AST 安全求值，不用 `eval`；限制表达式长度、复杂度与指数 |
| `current_time` | 取当前日期时间（可指定 IANA 时区） | 只读系统时钟；时区不合法明确报错，不静默兜底 |
| `text_stats` | 文本统计：字符 / 行 / 词数 / 中文双字高频词 | 纯字符串处理 |
| `random_pick` | 从候选里随机挑若干个 | 有随机性，说明里已注明不适合需要确定答案的问题 |

**业务能力刻意不进工具箱**：知识库问答、文档理解、企业检索、申请书生成都由各自的
子图（`node_knowledge` / `document_skill` / `application_skill`）负责——
它们要多步编排、要 HITL 暂停恢复、还要保证"企业事实不许猜"。
工具箱只承接"小而通用、无副作用、一步就能做完"的能力，避免同一件事出现两个 Owner。

给大模型用的两种出口（项目里模型走的是 `json_mode`，所以两种都留着）：

```python
from app.agent.tools import tool_schemas, describe_tools_for_prompt, call_tool, run_tool_calls

tool_schemas()                       # OpenAI function calling 格式的工具描述
describe_tools_for_prompt()          # 直接塞进提示词的文本片段
call_tool("calculator", {"expression": "(1+2)*3"})   # {"ok": True, "tool": ..., "result": {"value": 9}}
run_tool_calls([{"name": "calculator", "arguments": {"expression": "6*7"}}])
```

调用约定：**失败不抛异常**，统一返回 `{"ok": false, "error": ...}` 信封——
模型和前端拿到的是可解释的失败原因，接口不会变成 500。

MCP 工具注册进工具箱的命名沿用 Yuxi 的写法：`mcp__<服务 slug>__<工具名>`
（例如 Company MCP 的检索工具会注册成 `mcp__company__search_company`）。
注意这不改变业务链路——子图仍然通过 `app/agent/mcp/ocr_client.py` /
`company_client.py` 调这两个 MCP，工具箱只是把同一个服务的工具额外暴露出来。
服务清单同样来自 `app/agent/mcp/builtin.py`：
想再挂一个 MCP，就在 `BUILTIN_MCP_SERVERS` 里加一条（密钥写进 `.env`，
声明里只填 `api_key_env`），不需要动工具箱的代码。

```bash
# 看声明里有哪些 MCP、哪个连得上、各暴露了哪些工具（并注册进工具箱）
curl "http://127.0.0.1:8001/api/agent/toolbox/mcp?probe=true" | python -m json.tool
# 临时停掉某个服务（进程内状态，重启回到配置值）
curl -X POST http://127.0.0.1:8001/api/agent/toolbox/mcp/company/enable \
     -H "Content-Type: application/json" -d '{"enabled": false}'
```

两条纪律与项目其它 MCP 通道一致：

1. **导入期不建连**。只有探测（`GET /toolbox/mcp?probe=true`）和调用才会真的连 MCP，
   `tool_schemas()` 默认只给已注册的工具，不会因为"列个清单"就去打网络。
2. **只给脱敏信息**。MCP 服务清单不带 `api_key`，接口不会漏密钥。

与主链路的关系：工具箱是**独立的一层**，第一版不改变 Supervisor 的决策方式
（还是 `SupervisorDecision` 结构化输出 + 子图编排）。想让模型真正"用工具"，
下一步是把 `describe_tools_for_prompt()` 拼进提示词、再把模型返回的 `tool_calls`
交给 `run_tool_calls()` 执行；本地只用一个函数就能接上，不需要动图结构。

```bash
# 离线自检（不联网、不依赖 MySQL/Qdrant）
PYTHONPATH=. python app/test/test_tool_registry.py
```

---

## 6. API 与 SSE

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/agent/chat` | 统一入口（`stream=false` 同步 / `true` 流式 / 带 `resume` 恢复 HITL） |
| POST | `/api/agent/upload` | 会话附件上传，返回 `file_id` |
| GET | `/api/agent/stream/{thread_id}` | SSE 长连接（与 `/stream/{session_id}` 共用队列） |
| GET | `/api/agent/state/{thread_id}` | 查看线程 State（调试 HITL / 多轮上下文） |
| GET | `/api/agent/files/{name}` | 下载生成的 DOCX |
| GET | `/api/agent/tools` | MCP / 模板配置自检（`?probe=true` 会实际连一次 MCP 列工具） |
| GET | `/api/agent/toolbox` | 工具清单（`?category=buildin` / `?category=mcp` 可选过滤） |
| GET | `/api/agent/toolbox/schema` | 工具描述（OpenAI 格式；`?include_mcp=true` 会先注册 MCP 工具） |
| POST | `/api/agent/toolbox/call` | 按名调用工具：`{"name": "...", "arguments": {...}}` |
| GET | `/api/agent/toolbox/mcp` | MCP 服务清单（脱敏）；`?probe=true` 探测并注册工具 |
| POST | `/api/agent/toolbox/mcp/{slug}/enable` | 启用 / 停用某个 MCP 服务：`{"enabled": bool}` |

请求：

```json
{
  "thread_id": "abc123",
  "message": "帮我生成欣旺达公司的业务申请书",
  "attachments": [{"file_id": "file_001", "mime_type": "image/jpeg"}],
  "user_id": "u_001",
  "stream": false,
  "resume": null,
  "new_turn": false
}
```

返回（规范第 68 节）：

```json
{"type": "message", "content": "……", "thread_id": "abc123", "intent": "answer"}
{"type": "interrupt", "reason": "multiple_company_candidates", "candidates": [...], "payload": {...}}
{"type": "file", "content": "业务申请书已生成。", "file": {"name": "XX公司_20260919_业务申请书.docx", "url": "/api/agent/files/..."}}
```

**HITL 恢复**：只要线程停在 `interrupt` 上，下一轮 `POST /api/agent/chat` 会自动把
`message` 当作 `Command(resume=...)` 的值（也可以用 `resume` 字段显式传值）。
想在这种情况下强行开新话题，传 `"new_turn": true`。

```bash
# 第一次：触发多候选
curl -X POST .../api/agent/chat -d '{"thread_id":"t1","message":"帮我生成南京XX能源的业务申请书"}'
# → {"type":"interrupt","reason":"multiple_company_candidates", ...}
# 第二次：用户选 2（自动识别为 resume）
curl -X POST .../api/agent/chat -d '{"thread_id":"t1","message":"2"}'
# → {"type":"interrupt","reason":"confirm_before_generate", ...}
# 第三次：确认
curl -X POST .../api/agent/chat -d '{"thread_id":"t1","message":"确认"}'
# → {"type":"file","file":{...}}
```

SSE 事件（新增事件名与规范第 66 节一致，旧前端会忽略不认识的事件）：

```text
progress（原有） / delta（原有） / final（原有） / error（原有）
agent_started / routing / tool_call / tool_result / agent_message
document_processing / ocr_completed
company_candidates / human_confirmation_required
template_selected / document_generating / document_ready
resume
```

---

## 7. 环境变量

完整清单见 `.env.example` 的「企业业务智能 Agent」段。最常改的：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `AGENT_ENABLED` | true | 关掉后 `/api/agent/*` 不挂载，后端行为与改造前一致 |
| `AGENT_LLM_MODEL_ID` | 空 | 空则复用 `LLM_MODEL_ID` |
| `OCR_MCP_API_KEY` / `COMPANY_MCP_API_KEY` | 空 | MCP 密钥（**只有密钥放 .env**） |
| `OCR_MCP_BASE_URL` / `COMPANY_MCP_BASE_URL` | 空 | 可选：临时覆盖 builtin.py 里的地址（本地联调 / CI） |
| `AGENT_DEFAULT_INQUIRY_ACTION` | 空 | 空 = 询证函只上传时反问用户；填 `extract_mailing_address` = 自动抽地址 |
| `AGENT_REQUIRE_CONFIRM_BEFORE_RENDER` | true | 生成 DOCX 前是否要用户确认 |
| `AGENT_TEMPLATE_DIR` / `AGENT_OUTPUT_DIR` | templates / outputs | 模板与产物目录 |

MCP 的地址、传输方式、鉴权头、工具名、入参形态都不在 `.env`，
统一写在 `app/agent/mcp/builtin.py`（见 5 节）。

模板字段与 `ApplicationData` 对齐（`{{ company_name }}` 这类占位符），
改完模板结构后重跑 `PYTHONPATH=. python scripts/make_application_templates.py`。
装了 `docxtpl` 就用它渲染，没装自动回退 `python-docx`，功能不受影响。

---

## 8. 与记忆系统、SSE、前端的关系

- **短期记忆**：`thread_id` 同时作为 `session_id`，复用现有 `RecentMessageService`。
  知识分支由检索节点写用户消息，文档/申请书分支由 `memory_bridge.record_user_message` 补写；
  回答写入与长期记忆抽取直接复用 `step_5_write_history` / `step_6_extract_long_term_memory`。
- **长期记忆**：`AGENT` 与 `/query` 共用同一套 scope 规则（按 `user_id` 聚合）。
- **检查点（State 持久化）**：用 **sqlite**（`AGENT_CHECKPOINT=sqlite:output/agent_state.db`），
  文件落在项目 `output/` 下，重启不丢、也支持同机跨进程的 HITL 恢复；
  不配则退回 `memory`（进程内、重启即丢）。只改环境变量，图结构与业务代码不动
  （见 `app/agent/checkpoint.py`）。依赖 `langgraph-checkpoint-sqlite`（已装）。
  【边界】sqlite 是单机方案：多副本部署时 resume 可能落到别的实例，那才需要共享检查点。
- **前端**：现有页面继续走 `/query`，不受影响。`page/src/api/agent.js` 已经把
  Agent 入口、SSE、HITL 恢复封装好，确认后再把 `ChatView` 切过去即可（Agent 返回的
  `type=file` 可以直接渲染下载卡片）。

---

## 9. 自检与评测

```bash
# 框架自检（离线，不联网、不依赖 MySQL/Qdrant）：16 项，含 interrupt/resume 全链路
PYTHONPATH=. python app/test/test_agent_framework.py

# 工具箱自检（离线）：9 项，含 MCP 服务管理与 MCP 工具注册/调用
PYTHONPATH=. python app/test/test_tool_registry.py

# 契约守卫（离线）：5 项，防止子 Agent 的能力名回流到 Supervisor
PYTHONPATH=. python app/test/test_supervisor_contract.py

# Agent 评测：Routing / Company / Document
PYTHONPATH=. python -m evaluation.agent.run_agent_eval --suite all            # 需要模型
PYTHONPATH=. python -m evaluation.agent.run_agent_eval --suite all --offline   # 规则基线
```

规范第 75 节的验收场景与实现位置：

| Case | 场景 | 实现 | 验证方式 |
| --- | --- | --- | --- |
| 1 | 知识问答 | `node_knowledge` → 老 RAG 链路（含 `answer_output`） | 自检 `test_main_graph_knowledge_uses_legacy_chain` |
| 2 | 询证函 → 邮寄地址 | Document 子图自己判定 `resolved_action=extract_mailing_address` | 自检 `test_document_subgraph` / `test_document_decides_action_from_task` + 评测 document 套件 |
| 3 | 询证函 → 摘要 | `summarize_document` | 同上 |
| 4 | 营业执照 → 业务申请书 | Document 子图 → Application 子图 | 自检 `test_license_to_application_merge` |
| 5 | 直接输入企业简称 | Application 子图 + Company MCP | 自检 `test_application_single_candidate` |
| 6 | 多个企业候选 → HITL | `node_company_select` interrupt | 自检 `test_application_multi_candidate` / `test_main_graph_application_hitl` |
| 7 | 企业不存在 | `company_not_found` + 反问 | 自检 + 评测 company 套件（`not_found` 用例） |

---

## 10. 下一步建议

1. **接入外部 MCP**：先用 `/api/agent/tools?probe=true` 对齐工具名与入参，再跑真实文件。
2. **让模型真正用上工具箱**：把 `describe_tools_for_prompt()` 拼进 Supervisor / 子图提示词，
   再把模型返回的 `tool_calls` 交给 `run_tool_calls()` 执行（见 5.5 节）。
3. **多副本部署时换共享检查点**：那时 resume 可能落到别的实例，sqlite 文件不共享，
   需要引入 Postgres 之类的共享 saver（或按 `BaseCheckpointSaver` 自己实现一套）。
4. **前端切换**：`ChatView` 改用 `page/src/api/agent.js`，把 `type=file`、`human_confirmation_required`
   渲染成下载卡片与确认按钮。
5. **评测跑真实模型**：`run_agent_eval --suite all` 得到 Intent/Tool/Field 成绩，
   与 `--offline` 的规则基线对比，作为简历里的量化结果。
6. **扩展点**（保持第一版边界，不要提前加 Agent）：消息推送到企业微信/邮件、
   多轮简历式表单、模板字段扩展，都只需要新增子图节点或 Tool，不动 Main Agent 结构。
