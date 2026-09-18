"""召回阶段的候选数量配置。

为什么要单独配这几个数：**召回池太窄会让 rerank 失去意义**。
rerank 的价值是"从宽召回里挑准的"，如果上游只给 5 条候选，
它只能在 5 条里排序，而语义更相关、但向量分数略低的文档早在 RRF 那一步被截掉了。

另外「召回放大」并不会撑大 prompt：进 prompt 的条数由回答节点的 token 总控
和 rerank 的输出上限决定，与召回多少条无关。所以放宽召回买到的是
**rerank 的选择余地**，这部分是纯赚的。
"""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class RetrievalConfig:
    """召回各阶段的候选数量。"""

    # 每一路（dense / sparse）各自预取多少条进入 RRF 融合。
    # 向量库检索本身很便宜，池子开大一点没有明显代价。
    prefetch_limit: int
    # 单次混合检索（RRF 融合后）返回多少条给上层 —— 对应「主路」和「HyDE 路」各自拿到的数量
    fused_limit: int
    # 主路 + HyDE 路合并后再做一次 RRF，保留多少条进入 rerank
    merge_top_k: int
    # rerank 之后最多保留多少条进入回答 prompt（再往后还有 token 总控兜底）
    rerank_max_topk: int


retrieval_config = RetrievalConfig(
    prefetch_limit=int(os.getenv("RETRIEVAL_PREFETCH_LIMIT", "50")),
    fused_limit=int(os.getenv("RETRIEVAL_FUSED_LIMIT", "20")),
    merge_top_k=int(os.getenv("RETRIEVAL_MERGE_TOP_K", "20")),
    rerank_max_topk=int(os.getenv("RETRIEVAL_RERANK_MAX_TOPK", "12")),
)
