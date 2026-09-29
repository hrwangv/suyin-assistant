# Langfuse 接入说明

链路追踪已经接进代码，未配置 `LANGFUSE_*` 时全部退化成 no-op，业务与评测行为不变。

## 1. 前置条件

| 项 | 说明 |
| --- | --- |
| 依赖 | `requirements.txt` 里的 `langfuse==4.15.6`（v4；未安装时代码自动跳过追踪） |
| 密钥 | `.env` 里的 `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_BASE_URL` |
| 区域 | 欧盟 `https://cloud.langfuse.com`；美国 `https://us.cloud.langfuse.com` |

`.env.example` 里还有三个开关（都可不配）：

```bash
LANGFUSE_ENABLED=auto          # auto（默认，配了密钥就开启）/ true / false
LANGFUSE_CAPTURE_CONTENT=true  # false = 只报结构、耗时、token、指标，不报 prompt/答案原文
LANGFUSE_MASK_ENABLED=true     # 内置脱敏：手机号、身份证号、16~19 位长数字
```

## 2. 自检

```bash
# 看开关状态（不联网）
PYTHONPATH=. python -m app.core.tracing

# 联网校验密钥与网络（真的调一次 Langfuse）
PYTHONPATH=. python -m app.core.tracing --check
```

## 3. 埋点地图

一次用户请求 / 一次评测运行 = 一条 trace；下面的 span 依次挂在其下。

| 入口 | trace 根 span | session | tag |
| --- | --- | --- | --- |
| `POST /query` | `query` | `session_id` | `query` |
| `POST /api/agent/chat` | `agent-chat` | `thread_id`（多轮可串起来） | `agent` / `resume` |
| `evaluation.run_eval` / `ablation` | `rag-question:<level>`（每题一条） | 运行目录名 | `eval`、`suite:*`、`level:*` |
| `evaluation/agent/run_agent_eval.py` | `agent-case:<suite>`（每例一条） | 运行时间戳 | `eval`、`suite:*` |

子 span 名称（按出现顺序）：

```
node:item_name_confirm → query-analyzer → ChatOpenAI(generation)
node:search_embedding / node:search_embedding_hyde
        → embedding:qwen-v4 / hyde-doc → qdrant:hybrid-search
node:web_search_mcp
node:rrf → node:rerank → rerank:qwen
node:answer_output → answer-generation → ChatOpenAI(generation)
```

Agent 侧额外有 `node:supervisor` / `node:knowledge` / `node:answer` / `node:ask_user`，
以及 memory 抽取里的模型调用（独立 trace）。

**内容上报的口径**：span 默认只报结构（`capture_input=False` / `capture_output=False`），
内容一律在函数体内显式 `update_current_span(input=..., output=...)`，只挑排查用得上的字段。
这么做的原因有两个：

1. 多数节点的入参是整份 `AgentState`（附件、检索原文、记忆、内部标记），自动抓参会又大又难读；
2. `mcp:call` 的第一个参数是 `MCPServerSpec`，自动抓参会把 MCP 的 `api_key` 推到 Langfuse。

已显式上报的 span：

| span | input | output |
| --- | --- | --- |
| `node:supervisor` | 问题 / 第几轮 / 附件数 | 路由结论 / 理由 / 置信度 / task |
| `node:knowledge` | query / filters | answer / 命中条数 / 改写后 query / 图片数 |
| `node:answer` | 问题 / 证据条数 / 是否反问 | answer / 图片数 |
| `node:ask_user` | 问题 / 缺什么信息 | 反问内容 |
| `node:item_name_confirm` | 原问题 / 历史条数 | 改写后 query / 结构化过滤条件 |
| `node:search_embedding` | 改写后 query / 过滤条件 | 召回条数 / 前 5 条标题与分数 |
| `node:search_embedding_hyde` | 改写后 query / 假想文档 | 召回条数 / 前 5 条 |
| `node:web_search_mcp` | 改写后 query | 资讯条数 / 前 5 条标题 |
| `node:rrf` | 三路召回条数 | 融合后条数 / 前 5 条 |
| `node:rerank` | query / 候选条数 | 精排后条数 / 前 5 条（分数、来源、标题） |
| `node:answer_output` | 原问题 / 改写后 query / 证据条数 | answer / 图片数 |
| `embedding:qwen-v4` | 文本条数 / 首条预览 / 模型 | 向量条数 / 维度 / 稀疏项数 |
| `rerank:qwen` | query / 候选条数 / top_n | 返回条数 / 前 5 名分数 |
| `qdrant:hybrid-search` | 稠密维度 / 稀疏项数 | 命中条数 / 前 5 个 id 与分数 |
| `tool:call` / `mcp:call` | 工具名与调用参数 | 工具返回 |

**指标回写**：评测里每道题的 `recall@5` `recall@10` `hit@5` `hit@10` `mrr@10` `ndcg@10`
`faithfulness` `answer_relevancy` 以及耗时，都会以 score 写在该题的 trace 上；
每个层级/套件另有一条 `rag-level:<id>` / `agent-suite:<suite>` 汇总 trace。

## 4. 常见问题

**Q：跑完了但 Langfuse 里没有 trace。**

按顺序查：

1. `python -m app.core.tracing` 看 `enabled` 与 `reason`；命令行跑评测时若加了 `--no-trace`，本次进程不会上报。
2. 看日志里有没有 `CERTIFICATE_VERIFY_FAILED`。span 走 OpenTelemetry 的 OTLP 导出（urllib3），
   在 macOS + python.org 的 Python 上默认没有可用 CA，表现为「score 有、trace 没有」。
   代码已自动把 `OTEL_EXPORTER_OTLP_CERTIFICATE` 指到 certifi，若你手动设过其他值则以你的为准。
3. 数据有秒级延迟，稍等再刷新。

**Q：为什么用 curl / 老文档里的 `/api/public/traces` 查不到数据？**

2026-09-16 之后新建的组织（你的项目就属于这种）不能用旧接口，改用：

- 读 span：`GET /api/public/v2/observations?fromStartTime=...`
- 读 score：`GET /api/public/v3/scores`

**Q：担心数据出境。**

两个手段：`LANGFUSE_CAPTURE_CONTENT=false` 只上报结构与指标；或把 `LANGFUSE_BASE_URL`
指向自建实例。默认的 `LANGFUSE_MASK_ENABLED=true` 会对手机号、身份证号、长数字做掩码，
但**它不认公司名、金额、合同号**——这些要靠 `capture_content=false` 或自建实例解决。
另外 `tool:call` / `mcp:call` 会上报工具的入参与返回（含 OCR 全文、企业查询结果），
这部分的原文同样出境，介意的话按上面的开关处理。

## 5. 下一步（还没做）

把 100 题评测集推进 Langfuse 的 dataset，用 `run_experiment` 跑分，让 Langfuse 做版本对比；
目前 trace / score 已就位，迁不迁是收益取舍问题。
