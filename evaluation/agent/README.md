# Agent 评测

对应实施规范第 71 节：Routing / Tool Selection / Document Extraction / Company Resolution。
评测**不修改** `app/` 下的业务逻辑，只调用线上同一份函数。

```bash
# 全量（需要能调大模型；routing 会真实调用 Supervisor）
PYTHONPATH=. python -m evaluation.agent.run_agent_eval --suite all

# 只跑企业匹配（纯离线，不调模型、不调 MCP）
PYTHONPATH=. python -m evaluation.agent.run_agent_eval --suite company

# 规则基线：Supervisor 走 fallback、文档抽取走正则，用来对比「模型 vs 规则」
PYTHONPATH=. python -m evaluation.agent.run_agent_eval --suite all --offline

# 只跑前 5 条
PYTHONPATH=. python -m evaluation.agent.run_agent_eval --suite routing --limit 5
```

结果写入 `evaluation/agent/results/<时间戳>_<suite>/`：`metrics.json` + `report.md` + 逐条明细。

数据集：

| 文件 | 内容 | 指标 |
| --- | --- | --- |
| `datasets/intent_routing.json` | 请求 + State → next_action（含文档动作） | Intent Accuracy / Document Action Accuracy |
| `datasets/company_resolution.json` | 完整名 / 简称 / 空格 / 模糊 / 多候选 / 无结果 | Action Accuracy / Company Accuracy / Ambiguous Detection |
| `datasets/document_extraction.json` | 询证函 / 营业执照 / 普通文档 / 空文本 | Document Type Accuracy / Field Accuracy |
