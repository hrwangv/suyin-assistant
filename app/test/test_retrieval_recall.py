"""召回候选数量的配置与传递测试。

背景：原来 rrf_hybrid_search 写死 limit=5、prefetch 20/20，node_rrf 又砍到 5，
导致只有 5 条候选进 rerank，rerank 没有挑选余地。
现在这些数字走 app/conf/retrieval_config.py，并且由**调用方显式传入**
（函数签名不给默认值，漏传直接 TypeError），这里验证：
1. 签名里没有默认值；
2. 漏传会报错；
3. 仓库里每个调用点都显式传了三个数量；
4. 传入的数字真的透传到了 Qdrant 请求里。

rerank 的候选数量同理：text_rerank 的 top_n 不再有默认值（原来默认 10，会把池子里
第 11 名之后全赋 0 分，断崖算法在「已打分/未打分」边界上误判，等于硬切 10 条）。
调用点显式传「全部候选」，最终进 prompt 的条数由断崖算法 + RERANK_MAX_TOPK 决定。

不连真实 Qdrant，用假客户端拦截请求参数。
运行：python -m app.test.test_retrieval_recall
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

from app.conf.retrieval_config import retrieval_config
from app.llm.reranker_utils import text_rerank
from app.utils.qdrant_utils import rrf_hybrid_search
from app.rag.query_process.agent.nodes.node_rrf import step_3_reciprocal_rank_fusion
import app.rag.query_process.agent.nodes.node_rerank as rerank_node

# 项目根目录（app/test/test_retrieval_recall.py -> 上溯 3 层）
PROJECT_ROOT = Path(__file__).resolve().parents[2]
LIMIT_PARAMS = ("limit", "dense_limit", "sparse_limit")

passed = 0
failed: list[str] = []


def check(name: str, actual, expected):
    global passed
    if actual == expected:
        passed += 1
    else:
        failed.append(f"{name}\n     期望：{expected}\n     实际：{actual}")


class FakeQdrantClient:
    """只记录 query_points 收到的参数。"""

    def __init__(self):
        self.captured = None

    def query_points(self, **kwargs):
        self.captured = kwargs
        return type("R", (), {"points": []})()


def _prefetch_by_using(captured: dict) -> dict:
    """按 using 字段取 prefetch，不依赖列表顺序（函数里 sparse 排在 dense 前面）。"""
    return {item.using: item for item in captured["prefetch"]}


def _iter_call_sites(func_name: str):
    """扫描 app/ 与 evaluation/ 里对 func_name 的调用点（跳过本测试文件）。"""
    sites = []
    for folder in ("app", "evaluation"):
        for path in (PROJECT_ROOT / folder).rglob("*.py"):
            if path.name == Path(__file__).name:
                continue  # 本文件就是在测这件事，不算生产调用点
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = getattr(func, "id", None) or getattr(func, "attr", None)
                if name == func_name:
                    sites.append((path, node))
    return sites


def test_hybrid_search_forwards_call_site_limits():
    """调用方传进来的三个数量，要原样透传到 Qdrant 请求里。"""
    client = FakeQdrantClient()
    rrf_hybrid_search(
        client=client,
        collection_name="kb_chunks",
        dense_vector=[0.1, 0.2],
        sparse_vector={1: 0.5},
        limit=retrieval_config.fused_limit,
        dense_limit=retrieval_config.prefetch_limit,
        sparse_limit=retrieval_config.prefetch_limit,
    )
    kw = client.captured
    by_using = _prefetch_by_using(kw)

    check("dense 预取数量", by_using["dense"].limit, retrieval_config.prefetch_limit)
    check("sparse 预取数量", by_using["sparse"].limit, retrieval_config.prefetch_limit)
    check("融合后返回条数", kw["limit"], retrieval_config.fused_limit)
    check("prefetch 数 ≥ 融合数",
          retrieval_config.prefetch_limit >= retrieval_config.fused_limit, True)
    # 两路各自的 filter 都要挂上，否则过滤会在其中一路静默失效
    check("dense 挂了 filter", by_using["dense"].filter, kw["query_filter"])
    check("sparse 挂了 filter", by_using["sparse"].filter, kw["query_filter"])

    # 不同调用方传不同数字时，函数不应把参数吞掉或改写
    client2 = FakeQdrantClient()
    rrf_hybrid_search(
        client=client2,
        collection_name="item_name",
        dense_vector=[0.1],
        sparse_vector={1: 0.2},
        limit=3,
        dense_limit=7,
        sparse_limit=11,
    )
    by_using2 = _prefetch_by_using(client2.captured)
    check("自定义 dense 预取透传", by_using2["dense"].limit, 7)
    check("自定义 sparse 预取透传", by_using2["sparse"].limit, 11)
    check("自定义融合条数透传", client2.captured["limit"], 3)


def test_limit_params_have_no_default():
    """三个数量参数不允许有默认值，调用方必须显式传。"""
    params = inspect.signature(rrf_hybrid_search).parameters
    for name in LIMIT_PARAMS:
        check(f"{name} 必须存在", name in params, True)
        check(f"{name} 不允许有默认值",
              params[name].default if name in params else "缺失",
              inspect.Parameter.empty)

    # 漏传时应当直接报错，而不是静默用一个默认池子
    try:
        rrf_hybrid_search(
            client=FakeQdrantClient(),
            collection_name="kb_chunks",
            dense_vector=[0.1],
            sparse_vector={1: 0.5},
        )
    except TypeError:
        check("漏传 limit 直接报错", True, True)
    else:
        check("漏传 limit 直接报错", "没有报错", True)


def test_every_call_site_passes_limits():
    """扫描仓库源码，所有 rrf_hybrid_search 调用点都必须显式传三个数量。"""
    call_sites = _iter_call_sites("rrf_hybrid_search")

    check("至少扫到一个调用点", len(call_sites) > 0, True)
    for path, node in call_sites:
        where = f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}"
        passed_kwargs = {kw.arg for kw in node.keywords}
        for name in LIMIT_PARAMS:
            check(f"{where} 已显式传 {name}", name in passed_kwargs, True)
        # 数量不该在调用点写死成数字，应来自配置或上游变量
        literal = [
            kw.arg for kw in node.keywords
            if kw.arg in LIMIT_PARAMS and isinstance(kw.value, ast.Constant)
        ]
        check(f"{where} 未写死数量", literal, [])


def test_rrf_merge_keeps_enough_candidates():
    """主路 + HyDE 路合并后，要保留足够多的候选给 rerank。"""
    check("默认 top_k 来自配置",
          inspect.signature(step_3_reciprocal_rank_fusion).parameters["top_k"].default,
          retrieval_config.merge_top_k)

    # 造两路各 20 条（chunk_id 部分重叠）→ 合并后应保留 20 条
    def make(offset):
        return [
            {"id": f"c{i}", "payload": {"content": f"内容{i}"}}
            for i in range(offset, offset + 20)
        ]
    merged = step_3_reciprocal_rank_fusion([(make(0), 1.0), (make(10), 1.0)])
    check("合并后保留条数", len(merged), retrieval_config.merge_top_k)

    # 显式传 top_k 时仍然尊重调用方（向后兼容）
    check("显式 top_k 生效", len(step_3_reciprocal_rank_fusion([(make(0), 1.0)], top_k=5)), 5)


def test_rerank_output_cap_from_config():
    """rerank 的输出上限来自配置，且不小于最小条数。"""
    check("rerank 上限来自配置",
          rerank_node.RERANK_MAX_TOPK, retrieval_config.rerank_max_topk)
    check("上限大于最小条数",
          rerank_node.RERANK_MAX_TOPK > rerank_node.RERANK_MIN_TOPK, True)
    check("大模型窗口足够放下这些文档",
          retrieval_config.rerank_max_topk <= retrieval_config.merge_top_k, True)


def test_rerank_top_n_has_no_default():
    """text_rerank 的 top_n 不允许有默认值，避免调用方被静默截断。"""
    params = inspect.signature(text_rerank).parameters
    check("top_n 必须存在", "top_n" in params, True)
    check("top_n 不允许有默认值",
          params["top_n"].default if "top_n" in params else "缺失",
          inspect.Parameter.empty)
    try:
        text_rerank("问题", ["文档一"])
    except TypeError:
        check("漏传 top_n 直接报错", True, True)
    else:
        check("漏传 top_n 直接报错", "没有报错", True)


def test_rerank_call_sites_pass_top_n():
    """所有 text_rerank 调用点都必须显式传 top_n。"""
    sites = _iter_call_sites("text_rerank")
    check("至少扫到一个 text_rerank 调用点", len(sites) > 0, True)
    for path, node in sites:
        where = f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}"
        passed = {kw.arg for kw in node.keywords}
        check(f"{where} 已显式传 top_n",
              "top_n" in passed or len(node.args) >= 3, True)
        # top_n 不该写死成数字，要跟着候选池规模走
        literal = [kw.arg for kw in node.keywords
                   if kw.arg == "top_n" and isinstance(kw.value, ast.Constant)]
        check(f"{where} top_n 未写死", literal, [])


def test_final_count_decided_by_gap():
    """最终进 prompt 的条数由断崖算法 + 上限决定，不再被 rerank 的默认值硬切。"""
    real_rerank = rerank_node.text_rerank

    class Item:
        def __init__(self, index, score):
            self.index, self.relevance_score = index, score

    def run(scores, pool_size):
        seen = {}

        def stub(query, documents, top_n):
            seen["top_n"] = top_n
            seen["pool"] = len(documents)
            order = sorted(range(len(documents)), key=lambda i: scores[i], reverse=True)
            return [Item(i, scores[i]) for i in order[:top_n]]

        rerank_node.text_rerank = stub
        try:
            state = {
                "session_id": "t_rerank",
                "is_stream": False,
                "rewritten_query": "测试问题",
                "rrf_chunks": [
                    {"id": f"c{i}", "score": 0.0,
                     "payload": {"content": f"正文{i}", "title": f"标题{i}"}}
                    for i in range(pool_size)
                ],
                "web_search_docs": [],
            }
            rerank_node.node_rerank(state)
            return state["reranked_docs"], seen
        finally:
            rerank_node.text_rerank = real_rerank

    # 1) 20 条候选，真实断崖在第 6 条（0.90 → 0.30）→ 应截到 6 条
    cliff_scores = [0.95, 0.94, 0.93, 0.92, 0.91, 0.90, 0.30, 0.29] + [0.1] * 12
    docs, seen = run(cliff_scores, 20)
    check("全部候选都参与打分（top_n = 池子大小）", seen["top_n"], seen["pool"])
    check("在真实断崖处截断", len(docs), 6)

    # 2) 20 条候选、分数平缓无断崖 → 按上限截断
    flat_scores = [0.9 - 0.001 * i for i in range(20)]
    docs2, _ = run(flat_scores, 20)
    check("无明显断崖时按上限截断", len(docs2), rerank_node.RERANK_MAX_TOPK)

    # 3) 候选比上限还少 → 全部保留
    docs3, _ = run([0.9 - 0.001 * i for i in range(8)], 8)
    check("池子小于上限时全部保留", len(docs3), 8)


def test_default_values_as_agreed():
    """固化这次商定的默认值，防止以后被误改回很小的数。"""
    check("prefetch 默认", retrieval_config.prefetch_limit, 50)
    check("fused 默认", retrieval_config.fused_limit, 20)
    check("merge top_k 默认", retrieval_config.merge_top_k, 20)
    check("rerank 上限默认", retrieval_config.rerank_max_topk, 12)


def main():
    for test in (
        test_hybrid_search_forwards_call_site_limits,
        test_limit_params_have_no_default,
        test_every_call_site_passes_limits,
        test_rrf_merge_keeps_enough_candidates,
        test_rerank_output_cap_from_config,
        test_rerank_top_n_has_no_default,
        test_rerank_call_sites_pass_top_n,
        test_final_count_decided_by_gap,
        test_default_values_as_agreed,
    ):
        test()

    print(f"\n通过 {passed} 项，失败 {len(failed)} 项")
    for item in failed:
        print("  [FAIL]", item)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
