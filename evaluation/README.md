# evaluation —— RAG 消融评测

评测**不修改** `app/` 下任何业务逻辑：检索、改写、HyDE、RRF、精排、答案生成全部
直接调用线上已有函数，本目录只负责「编排 → 打分 → 出表」。

## 目录结构

```
evaluation/
├── config.py           实验梯度（消融阶梯）与运行参数
├── dataset.py          数据集结构、读写、金标匹配
├── build_dataset.py    从真实语料反向生成 100 条测试问题
├── retrieval.py        按层级编排检索（复用线上函数）
├── answering.py        答案生成（复用 answer_out.prompt，无副作用）
├── judge.py            LLM Judge：Faithfulness / Answer Relevancy
├── metrics.py          Recall@K / Hit@K / MRR@K / nDCG@K
├── report.py           主表 + 分题型表 + CSV
├── run_eval.py         评测主入口
├── ablation.py         消融阶梯 + 作品集摘要
├── selftest.py         离线自检（不联网）
├── datasets/           eval_dataset.json（100 题）
└── results/            每次运行一个目录：逐题 jsonl + metrics.json + report.md + table.csv
```

## 快速开始

```bash
# 0. 自检：验证指标算法、金标匹配、层级单调性（不联网，秒级）
PYTHONPATH=. python -m evaluation.selftest

# 1. 生成 100 条测试问题（离线，读 output/**/chunks.json）
PYTHONPATH=. python -m evaluation.build_dataset

# 2. 【重要】确认金标确实在索引里，覆盖率低说明数据集和索引不是同一批语料
PYTHONPATH=. python -m evaluation.run_eval --check-gold

# 3. 只跑检索指标（不需要 LLM，最快）
PYTHONPATH=. python -m evaluation.run_eval --suite ablation_ladder --no-answer

# 4. 完整评测：检索 + 答案 + 两个生成指标
PYTHONPATH=. python -m evaluation.run_eval --suite baseline_vs_enhanced

# 5. 一键消融 + 自动生成可粘贴的简历句式
PYTHONPATH=. python -m evaluation.ablation
```

改 prompt / 调参数后重跑用 `--refresh` 忽略缓存；只跑一小部分用 `--limit 20`
（按题型分层抽样，不会只抽到某一类题）。

## 实验阶梯（消融）

| 层级 | 名称 | 打开的开关 | 对应线上节点 |
|---|---|---|---|
| L0 | Baseline | 无 | 纯 dense 单路检索 |
| L1 | + Query Rewrite | 问题改写 | `node_item_name_confirm` |
| L2 | + HyDE | 第二路假设性文档召回（朴素轮询合并） | `node_search_embedding_hyde` |
| L3 | + RRF | dense+sparse 混合检索 + 多路加权 RRF 融合 | `node_search_embedding` + `node_rrf` |
| L4 | + Metadata Filter | 时间/分类/领域结构化过滤 | `qdrant_filter_builder` |
| L5 | + Rerank | Cross-Encoder 精排 + 断崖截断（线上完整配置） | `node_rerank` |

L2 用**朴素轮询**而不是 RRF，是为了把「多一路召回」和「用 RRF 融合」两个变量的
增益拆开；L3 打开的是两层 RRF（路内 dense+sparse 融合、路间加权融合），
`selftest` 会校验层级只增不减，保证增益可归因。

## 指标

| 指标 | 含义 |
|---|---|
| Recall@5 / Recall@10 | 金标中有多少比例被召回（覆盖率） |
| Hit@K | 前 K 条里有没有任意一条金标（能不能找到） |
| MRR@10 | 第一条金标位置的倒数（排得靠不靠前） |
| nDCG@K | 带位置折损的排序质量 |
| Faithfulness | 答案的原子陈述中被参考内容支持的比例（RAGAS 风格） |
| Answer Relevancy | 由答案反推的问题与原始问题的余弦相似度均值（RAGAS 风格） |

分面型问题（"某期晨报的重点客户有哪些"）金标天然是多条，Recall@K 上限就是
K/|gold|，这类题要看 **Hit@K / MRR@K**；报告已按题型拆开，避免总体指标被题型结构带偏。

## 数据集格式

```jsonc
{
  "meta": { "source": "local", "seed": 20260915, "summary": { "total": 100, "...": "..." } },
  "items": [
    {
      "id": "q0001",
      "question": "经营晨报里关于「协创数据」的信息有哪些？",
      "question_type": "entity_fact",   // entity_fact / colloquial_entity / file_category
                                        // / domain_facet / time_category / file_overview
      "difficulty": "medium",
      "requires_filter": false,          // 该题是否依赖元数据过滤
      "gold": [
        {
          "content_hash": "…",           // md5(strip(content))，跨导入批次稳定
          "chunk_id": "",                // 从 Qdrant 生成的数据集才有
          "file_title": "经营晨报 20260410",
          "title": "## 协创数据：…",
          "part": 1,
          "snippet": "…"
        }
      ],
      "gold_meta": { "category": "重点客户" },
      "raw_gold_size": 3
    }
  ]
}
```

金标匹配按优先级取第一个命中的 key：`cid:<chunk_id>` → `hash:<md5(content)>`
→ `ttl:<file_title>|<title>|<part>`。本地 `chunks.json` 是入库**之前**的备份、
没有 `chunk_id`，所以默认走内容哈希；正式评测建议用
`--source qdrant` 重建数据集，金标直接带 `chunk_id`，也保证金标一定在索引里。

## 两个已知偏差（写报告时要一起说）

1. **判分模型可能和生成模型是同一个**，会系统性偏高。用 `EVAL_JUDGE_MODEL`
   换一个模型，并人工抽检 20~30% 校准。Faithfulness 低 = 可能编，
   Answer Relevancy 低 = 可能答非所问，两个要一起看。
2. **答案生成不带短期历史与长期记忆**（`history` / `long_term_memories` 固定为空），
   否则评测会污染线上记忆库（评测数据变成用户历史）。这对"比较不同检索方案"
   是更干净的口径，但和线上完整问答有差异。

另外发现一个线上行为值得注意：`node_rerank` 调 `text_rerank` 时没传 `top_n`，
用的是函数默认值 10，所以候选池里排在 10 名之后的文档拿不到真实精排分
（被赋 0 分沉底）。评测里刻意保持了一致；如果想让精排覆盖整个候选池，
可以显式传 `top_n=len(docs)`，但那会改变线上行为，需要单独评估。

## 输出物

每次运行在 `results/<时间戳>__<suite>/` 下产出：

- `records_<level>.jsonl`：逐题记录（问题、命中的 key、改写后的问题、过滤条件、
  答案、判分细节、耗时），可直接用来做人工复核
- `metrics.json`：总体 + 分题型指标
- `report.md`：主表 + 分题型表 + 说明
- `table.csv`：主表（贴 PPT / 简历）

`evaluation.ablation` 还会额外产出 `portfolio_summary.md`，把本次实测数字
自动填进一段可直接粘贴的简历句式（离线冒烟模式会明确标注"不可写入简历"）。
