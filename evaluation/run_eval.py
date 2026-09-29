"""评测主入口：按消融层级跑检索（可选生成 + 判分），产出指标与报告。

常用命令：

    # 先确认金标确实在索引里（最重要的一步，不然分数会莫名其妙接近 0）
    PYTHONPATH=. python -m evaluation.run_eval --check-gold

    # 只跑检索指标（不需要 LLM，最省时）
    PYTHONPATH=. python -m evaluation.run_eval --suite ablation_ladder --no-answer

    # 完整评测：检索 + 答案生成 + Faithfulness / Answer Relevancy
    PYTHONPATH=. python -m evaluation.run_eval --suite baseline_vs_enhanced

    # 冒烟：不联网、不调模型，只验证评测流程本身
    PYTHONPATH=. python -m evaluation.run_eval --offline-smoke --levels baseline,full
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path

from evaluation import answering, judge, metrics, report
from evaluation import tracing as eval_tracing
from evaluation.cache import cached
from evaluation.config import (
    CANDIDATE_TOP_K,
    CHUNKS_COLLECTION,
    DEFAULT_DATASET,
    EVAL_KS,
    RESULTS_DIR,
    resolve_levels,
)
from evaluation.dataset import (
    content_hash,
    load_dataset,
    match_payload,
    stratified_limit,
    summary,
    validate,
)
from evaluation.retrieval import RetrievalResult, retrieve


def _retrieval_cache_payload(level_id: str, question: str, top_k: int, offline: bool) -> dict:
    return {
        "v": 1,  # 缓存版本：改了检索实现就 +1，避免读到旧结果的脏数据
        "level": level_id,
        "question": question,
        "top_k": top_k,
        "collection": CHUNKS_COLLECTION,
        "offline": offline,
    }


def check_gold_coverage(items, collection: str = CHUNKS_COLLECTION) -> dict:
    """scroll 一遍线上 collection，统计金标有多少真的在索引里。

    这是评测前最该跑的一步：如果数据集是本地生成的、而索引里是另一批文件，
    所有指标都会接近 0，很容易被误读成"检索效果差"。
    """
    from app.utils.qdrant_utils import get_qdrant_client

    client = get_qdrant_client()
    index_hashes: set[str] = set()
    index_ids: set[str] = set()
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=collection,
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for point in points:
            payload = point.payload or {}
            content = str(payload.get("content") or "")
            if content:
                index_hashes.add(content_hash(content))
            chunk_id = payload.get("chunk_id") or point.id
            if chunk_id:
                index_ids.add(str(chunk_id))
        if offset is None:
            break

    total = covered = 0
    missed_questions: list[str] = []
    for item in items:
        hit = False
        for gold in item.gold:
            total += 1
            ok = False
            if gold.chunk_id and gold.chunk_id in index_ids:
                ok = True
            if gold.content_hash and gold.content_hash in index_hashes:
                ok = True
            if ok:
                covered += 1
                hit = True
        if not hit:
            missed_questions.append(item.id)
    return {
        "collection": collection,
        "index_points": len(index_hashes),
        "gold_total": total,
        "gold_covered": covered,
        "coverage": (covered / total) if total else 0.0,
        "questions_without_any_gold": len(missed_questions),
        "sample_missing": missed_questions[:10],
    }


def run_item(
    item,
    level,
    *,
    top_k: int,
    do_answer: bool,
    do_judge: bool,
    refresh: bool,
    offline_corpus=None,
    client=None,
) -> dict:
    """跑一道题并返回逐题记录（不含追踪，追踪在外层 run_level 包）。"""
    offline = offline_corpus is not None
    gold_keys = item.gold_keys()
    retrieval_raw = cached(
        "retrieval",
        _retrieval_cache_payload(level.level_id, item.question, top_k, offline),
        lambda: retrieve(
            item.question,
            level,
            top_k=top_k,
            client=client,
            offline_corpus=offline_corpus,
        ).to_dict(),
        refresh=refresh,
    )
    retrieval = RetrievalResult.from_dict(retrieval_raw)

    record: dict = {
        "id": item.id,
        "question": item.question,
        "question_type": item.question_type,
        "difficulty": item.difficulty,
        "requires_filter": item.requires_filter,
        "level_id": level.level_id,
        "level_name": level.name,
        "rewritten_query": retrieval.rewritten_query,
        "filters": retrieval.filters,
        "filter_applied": retrieval.filter_applied,
        "fallback_relaxed": retrieval.fallback_relaxed,
        "gold_keys": sorted(gold_keys),
        "retrieved_keys": [
            match_payload(chunk.payload, gold_keys, f"miss:{position}")
            for position, chunk in enumerate(retrieval.candidates)
        ],
        "retrieved_titles": [
            {
                "score": round(chunk.score, 4),
                "file_title": chunk.payload.get("file_title"),
                "title": chunk.payload.get("title"),
                "category": chunk.payload.get("category"),
                "domain": chunk.payload.get("domain"),
            }
            for chunk in retrieval.candidates
        ],
        "n_candidates": len(retrieval.candidates),
        "latency_ms": retrieval.latency_ms,
    }

    if do_answer:
        question_for_answer = retrieval.rewritten_query or item.question
        context = answering.build_context(retrieval.candidates)
        answer_raw = cached(
            "answer",
            {
                "v": 1,
                "level": level.level_id,
                "question": question_for_answer,
                "context": context,
            },
            lambda: answering.generate_answer(
                question_for_answer, retrieval.candidates
            ).to_dict(),
            refresh=refresh,
        )
        answer_result = answering.AnswerResult.from_dict(answer_raw)
        record["answer"] = answer_result.answer
        record["answer_latency_ms"] = answer_result.latency_ms
        record["prompt_tokens"] = answer_result.prompt_tokens

        if do_judge:
            faith = judge.judge_faithfulness(
                question_for_answer,
                answer_result.answer,
                answer_result.context,
                refresh=refresh,
            )
            relevancy = judge.judge_answer_relevancy(
                item.question,
                answer_result.answer,
                refresh=refresh,
            )
            record["faithfulness"] = faith["faithfulness"]
            record["faithfulness_detail"] = {
                "n_statements": faith["n_statements"],
                "n_supported": faith["n_supported"],
                "statements": faith["statements"],
            }
            record["answer_relevancy"] = relevancy["answer_relevancy"]
            record["relevancy_detail"] = {
                "generated_questions": relevancy["generated_questions"],
                "similarities": relevancy["similarities"],
            }

    return record


def run_level(
    level,
    items,
    *,
    top_k: int,
    do_answer: bool,
    do_judge: bool,
    refresh: bool,
    offline_corpus=None,
    client=None,
    verbose: bool = True,
    run_id: str = "",
    suite: str = "",
) -> tuple[list[dict], dict]:
    """跑一个层级的所有题目，返回 (逐题记录, 聚合指标)。

    每题包一条 Langfuse trace（未配置追踪时是 no-op），逐题指标以 score 回写。
    """
    records: list[dict] = []

    for index, item in enumerate(items, start=1):
        with eval_tracing.question_scope(
            run_id=run_id,
            suite=suite,
            level_id=level.level_id,
            question=item.question,
            item_id=item.id,
            question_type=item.question_type,
            difficulty=item.difficulty,
            requires_filter=item.requires_filter,
        ):
            record = run_item(
                item,
                level,
                top_k=top_k,
                do_answer=do_answer,
                do_judge=do_judge,
                refresh=refresh,
                offline_corpus=offline_corpus,
                client=client,
            )
            eval_tracing.score_record(record)
        records.append(record)
        if verbose and index % 10 == 0:
            print(f"  [{level.level_id}] {index}/{len(items)}")

    aggregated = metrics.aggregate(records, ks=EVAL_KS)
    overall = aggregated["overall"]
    # 端到端耗时：检索 + 回答（judge 的耗时不算进"系统耗时"）
    latencies = [r.get("latency_ms", 0) + r.get("answer_latency_ms", 0) for r in records]
    overall["avg_latency_ms"] = sum(latencies) / len(latencies) if latencies else 0.0
    return records, overall


def write_jsonl(path: Path, records: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def run_suite(
    levels,
    items,
    dataset_meta,
    *,
    out_dir: Path,
    top_k: int,
    do_answer: bool,
    do_judge: bool,
    refresh: bool,
    offline_corpus=None,
    title: str = "RAG 消融评测结果",
    extra_notes: list[str] | None = None,
    suite: str = "custom",
) -> dict:
    """跑一组实验层级，落盘逐题记录 / 指标 / 报告，返回结果对象。

    run_eval.py 和 ablation.py 共用这个函数，保证两个入口口径完全一致。

   """
    out_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    by_type: dict[str, dict] = {}
    all_metrics: dict[str, dict] = {}
    run_id = out_dir.name

    for level in levels:
        print(f"[run_suite] 开始层级：{level.name}（{level.level_id}）")
        records, overall = run_level(
            level,
            items,
            top_k=top_k,
            do_answer=do_answer,
            do_judge=do_judge,
            refresh=refresh,
            offline_corpus=offline_corpus,
            run_id=run_id,
            suite=suite,
        )
        # 层级汇总也挂一条 trace，方便在 Langfuse 里按 level 直接看总览
        eval_tracing.score_level(overall, level_id=level.level_id)
        write_jsonl(out_dir / f"records_{level.level_id}.jsonl", records)
        rows.append(report.build_level_row(level.level_id, level.name, level.note, overall))
        by_type[level.level_id] = overall.get("by_question_type", {})
        all_metrics[level.level_id] = {
            "overall": overall,
            "records_file": f"records_{level.level_id}.jsonl",
        }
        line = (
            f"    Recall@5={overall['recall@5']:.3f} "
            f"Recall@10={overall['recall@10']:.3f} "
            f"MRR@10={overall['mrr@10']:.3f}"
        )
        if overall.get("faithfulness") is not None:
            line += f" Faithfulness={overall['faithfulness']:.3f}"
        if overall.get("answer_relevancy") is not None:
            line += f" AnswerRelevancy={overall['answer_relevancy']:.3f}"
        print(line)

    notes = [
        "检索指标在「该层级最终交给答案节点的候选列表」上计算，"
        f"候选池上限 {top_k}（rerank 层会按线上断崖截断逻辑收缩）。",
        "Faithfulness = 被参考内容支持的原子陈述占比；Answer Relevancy = "
        "由答案反推的问题与原始问题的余弦相似度均值（RAGAS 风格精简实现）。",
        "判分模型建议换成与生成模型不同的模型（EVAL_JUDGE_MODEL），并人工抽检校准。",
    ]
    if offline_corpus is not None:
        notes.insert(0, "⚠️ 本次为离线冒烟模式（本地词法检索），分数不代表线上效果。")
    if extra_notes:
        notes = list(extra_notes) + notes

    markdown = report.build_markdown(
        rows,
        title=title,
        dataset_meta=dataset_meta,
        by_type=by_type,
        notes=notes,
    )
    (out_dir / "report.md").write_text(markdown, encoding="utf-8")
    (out_dir / "metrics.json").write_text(
        json.dumps(all_metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    report.write_csv(out_dir / "table.csv", rows)
    # 评测收尾：把缓冲区推给 Langfuse（未启用追踪时是 no-op）
    eval_tracing.flush()
    return {
        "rows": rows,
        "by_type": by_type,
        "metrics": all_metrics,
        "markdown": markdown,
        "out_dir": out_dir,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="RAG 消融评测")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--levels", default=None, help="逗号分隔的层级，默认全部")
    parser.add_argument("--suite", default=None, help="预设：baseline_vs_enhanced / ablation_ladder")
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="只跑 N 题（按题型分层抽样，冒烟用）",
    )
    parser.add_argument("--top-k", type=int, default=CANDIDATE_TOP_K, help="候选池大小")
    parser.add_argument("--no-answer", action="store_true", help="只算检索指标，不生成答案")
    parser.add_argument("--no-judge", action="store_true", help="生成答案但不跑 LLM Judge")
    parser.add_argument("--refresh", action="store_true", help="忽略缓存重跑")
    parser.add_argument("--offline-smoke", action="store_true", help="本地词法冒烟，不联网")
    parser.add_argument("--check-gold", action="store_true", help="只检查金标在索引里的覆盖率")
    parser.add_argument("--out", default=None, help="结果目录")
    parser.add_argument(
        "--no-trace", action="store_true", help="本次运行不上报 Langfuse（临时关闭追踪）"
    )
    args = parser.parse_args()

    if args.no_trace:
        # 在初始化客户端之前落开关，保证本次进程完全不产生跨网调用
        os.environ["LANGFUSE_ENABLED"] = "false"

    items, dataset_meta = load_dataset(args.dataset)
    problems = validate(items)
    if problems:
        print("[run_eval] 数据集校验提示：")
        for problem in problems[:5]:
            print("   -", problem)
    if args.limit:
        items = stratified_limit(items, args.limit)
        dataset_meta["subset_limit"] = args.limit
    # 始终按本次实际参与的题重算，避免"跑 20 题但报告写 100 题"的误导
    dataset_meta["summary"] = summary(items)

    if args.check_gold:
        result = check_gold_coverage(items)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result["coverage"] < 0.8:
            print(
                "\n⚠️ 金标覆盖率偏低：数据集里的证据有相当比例不在当前索引中。\n"
                "   请用 --source qdrant 重新生成数据集，或先导入对应文档，"
                "否则指标会被系统性拉低。"
            )
        return

    levels = resolve_levels(args.levels, args.suite)
    run_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}__{args.suite or 'custom'}"
    out_dir = Path(args.out) if args.out else RESULTS_DIR / run_id

    offline_corpus = None
    if args.offline_smoke:
        from evaluation.build_dataset import load_local_corpus

        offline_corpus = load_local_corpus()
        args.no_answer = True
        args.no_judge = True
        print(
            f"[run_eval] ⚠️ 离线冒烟模式：使用本地词法检索（{len(offline_corpus)} 条语料），"
            "仅验证评测流程，分数不代表线上效果"
        )

    do_answer = not args.no_answer
    result = run_suite(
        levels,
        items,
        dataset_meta,
        out_dir=out_dir,
        top_k=args.top_k,
        do_answer=do_answer,
        do_judge=do_answer and not args.no_judge,
        refresh=args.refresh,
        offline_corpus=offline_corpus,
        suite=args.suite or "custom",
    )
    print("\n" + result["markdown"])
    print(f"\n[run_eval] 结果目录：{result['out_dir']}")


if __name__ == "__main__":
    main()
