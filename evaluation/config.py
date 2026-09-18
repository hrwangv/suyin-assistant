"""评测配置：路径、实验梯度（消融阶梯）、运行参数。

消融阶梯与线上节点的对应关系（**不改动任何线上代码，只是复用其中的函数**）：

    L0 baseline         纯稠密单路检索 + 用户原始问题（不用改写/HyDE/过滤/精排）
    L1 +query_rewrite   Query Analyzer 改写问题（只用 rewritten_query，不用 filters）
    L2 +hyde            增加 HyDE 第二路，两路用「朴素轮询」合并（对照组）
    L3 +rrf             每路改为 dense+sparse 混合检索（Qdrant 内置 RRF），
                        两路之间再用加权 RRF 融合 —— 对应 node_search_embedding + node_rrf
    L4 +metadata_filter 叠加结构化过滤条件（时间/分类/领域）—— 对应 node_item_name_confirm
                        + qdrant_filter_builder
    L5 +rerank          叠加 Cross-Encoder 精排 + 断崖截断 —— 对应 node_rerank，
                        这一级才是「线上完整配置」

L2 用朴素轮询而不是 RRF，是为了把「多一路召回」和「用 RRF 融合」两个变量的增益拆开：
如果 L2 就用 RRF，L3 的 +RRF 就没有可归因的增量了。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from app.conf.qdrant_config import qdrant_config
from app.conf.retrieval_config import retrieval_config

# ------------------------------------------------------------------ #
# 路径
# ------------------------------------------------------------------ #
EVAL_ROOT: Path = Path(__file__).resolve().parent
PROJECT_ROOT: Path = EVAL_ROOT.parent

DATASET_DIR: Path = EVAL_ROOT / "datasets"
RESULTS_DIR: Path = EVAL_ROOT / "results"
CACHE_DIR: Path = RESULTS_DIR / "_cache"

DEFAULT_DATASET: Path = DATASET_DIR / "eval_dataset.json"

# 生成数据集时扫描的本地解析产物（离线可用，不需要 Qdrant）
LOCAL_CHUNK_GLOB: str = os.getenv("EVAL_LOCAL_CHUNK_GLOB", "output/**/chunks.json")

# ------------------------------------------------------------------ #
# 与线上保持一致（直接读线上配置，避免评测口径和线上漂移）
# ------------------------------------------------------------------ #
# 候选池大小：线上 node_rrf 融合后保留 merge_top_k 条交给精排
CANDIDATE_TOP_K: int = retrieval_config.merge_top_k
# 精排后最多保留条数
RERANK_TOP_K: int = retrieval_config.rerank_max_topk
# 检索的 collection（与导入节点一致）
CHUNKS_COLLECTION: str = qdrant_config.chunks_collection

# ------------------------------------------------------------------ #
# 评测参数
# ------------------------------------------------------------------ #
# 打分只看前 K 条，两个 K 都算，方便对比"候选池够不够大"
EVAL_KS: tuple[int, ...] = (5, 10)
# MRR 截断位置
MRR_K: int = 10
# Answer Relevancy（RAGAS 式）由答案反推几个问题
RELEVANCY_N_QUESTIONS: int = int(os.getenv("EVAL_RELEVANCY_N") or "3")

# LLM Judge 使用的模型：默认沿用项目配置的 LLM。
# 强烈建议换成**与生成模型不同**的模型，避免"自己判自己"的偏差：
#   export EVAL_JUDGE_MODEL=<另一个模型ID>
JUDGE_MODEL: str | None = os.getenv("EVAL_JUDGE_MODEL") or None

# 是否把 LLM 调用结果缓存到磁盘（一次完整评测上千次调用，缓存让重跑几乎免费）
_CACHE_FLAG: str = (os.getenv("EVAL_CACHE_ENABLED") or "true").strip().lower()
CACHE_ENABLED: bool = _CACHE_FLAG in ("1", "true", "yes", "on")


# ------------------------------------------------------------------ #
# 实验梯度
# ------------------------------------------------------------------ #
@dataclass(frozen=True)
class EvalLevel:
    """一个消融层级。字段即开关，retrieval.py 按这些开关编排现有函数。"""

    level_id: str
    name: str
    query_rewrite: bool = False
    hyde: bool = False
    hybrid_retrieval: bool = False  # 每路 dense+sparse（Qdrant 内置 RRF 融合）
    rrf_fusion: bool = False        # 多路之间加权 RRF 融合
    metadata_filter: bool = False   # 结构化条件过滤
    rerank: bool = False            # Cross-Encoder 精排 + 断崖截断
    note: str = ""


LEVELS: tuple[EvalLevel, ...] = (
    EvalLevel(
        level_id="baseline",
        name="Baseline（纯稠密单路）",
        note="原始问题 + dense-only 检索，不使用改写/HyDE/过滤/精排",
    ),
    EvalLevel(
        level_id="rewrite",
        name="+ Query Rewrite",
        query_rewrite=True,
        note="Query Analyzer 改写问题，仍为稠密单路",
    ),
    EvalLevel(
        level_id="hyde",
        name="+ HyDE",
        query_rewrite=True,
        hyde=True,
        note="主路 + HyDE 路，两路朴素轮询合并（对照组，用于隔离 RRF 的增益）",
    ),
    EvalLevel(
        level_id="rrf",
        name="+ RRF",
        query_rewrite=True,
        hyde=True,
        hybrid_retrieval=True,
        rrf_fusion=True,
        note="两层 RRF 同时打开：每路改为 dense+sparse（Qdrant 内置 RRF 融合），"
        "多路之间再做加权 RRF 融合。若要把这两个变量拆开归因，可在此层级前再插一个"
        " hybrid_retrieval=True / rrf_fusion=False 的层级",
    ),
    EvalLevel(
        level_id="filter",
        name="+ Metadata Filter",
        query_rewrite=True,
        hyde=True,
        hybrid_retrieval=True,
        rrf_fusion=True,
        metadata_filter=True,
        note="叠加时间/分类/领域结构化过滤，带零召回放宽兜底",
    ),
    EvalLevel(
        level_id="full",
        name="+ Rerank（线上完整配置）",
        query_rewrite=True,
        hyde=True,
        hybrid_retrieval=True,
        rrf_fusion=True,
        metadata_filter=True,
        rerank=True,
        note="在过滤后的候选池上做 Cross-Encoder 精排 + 断崖截断",
    ),
)

LEVEL_BY_ID: dict[str, EvalLevel] = {level.level_id: level for level in LEVELS}

# 两套预设：面试/作品集最常用的是第二套
SUITES: dict[str, list[str]] = {
    # Baseline vs 线上完整配置，最直观的"改造前 / 改造后"
    "baseline_vs_enhanced": ["baseline", "full"],
    # 完整消融阶梯，用来回答"每一层分别贡献了多少"
    "ablation_ladder": [level.level_id for level in LEVELS],
}


def get_level(level_id: str) -> EvalLevel:
    try:
        return LEVEL_BY_ID[level_id]
    except KeyError:
        raise KeyError(
            f"未知的实验层级：{level_id}；可选：{', '.join(LEVEL_BY_ID)}"
        ) from None


def resolve_levels(spec: str | None, suite: str | None = None) -> list[EvalLevel]:
    """把 --levels / --suite 解析成层级列表。

    :param spec:  逗号分隔的 level_id，支持 "all"
    :param suite: SUITES 里的预设名
    """
    if suite:
        if suite not in SUITES:
            raise KeyError(f"未知的 suite：{suite}；可选：{', '.join(SUITES)}")
        return [get_level(level_id) for level_id in SUITES[suite]]
    if not spec or spec == "all":
        return list(LEVELS)
    return [get_level(item.strip()) for item in spec.split(",") if item.strip()]
