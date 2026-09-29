"""消融实验入口：一次跑完整阶梯，产出可直接进 PPT / 简历的表。

这是给"作品集 / 面试"用的入口，和 run_eval.py 的区别只有两点：
  1. 默认跑完整阶梯（Baseline → +Rewrite → +HyDE → +RRF → +Filter → +Rerank）；
  2. 额外生成一份 `portfolio_summary.md`，把实测数字自动填进一段
     可直接粘贴到简历的句式里（**不编数字，全部来自本次运行**）。

运行：
    PYTHONPATH=. python -m evaluation.ablation                     # 完整（含生成与判分）
    PYTHONPATH=. python -m evaluation.ablation --no-answer         # 只跑检索指标
    PYTHONPATH=. python -m evaluation.ablation --limit 20          # 先拿 20 题试通
"""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from evaluation.config import (
    CANDIDATE_TOP_K,
    DEFAULT_DATASET,
    RESULTS_DIR,
    resolve_levels,
)
from evaluation.dataset import load_dataset, stratified_limit, summary
from evaluation.run_eval import run_suite


def _pct(value) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def build_portfolio_summary(
    rows: list[dict],
    n_questions: int,
    offline: bool,
    n_types: int = 0,
) -> str:
    """把实测数字填进可直接粘贴的简历句式（数字全部来自本次运行）。"""
    if not rows:
        return "# 作品集摘要\n\n（没有结果）\n"
    baseline = rows[0]
    final = rows[-1]

    def delta_text(key: str, label: str) -> str:
        base, last = baseline.get(key), final.get(key)
        if base is None or last is None:
            return f"{label}（本次未测）"
        return f"{label} 从 {_pct(base)} 提升至 {_pct(last)}（{(last - base) * 100:+.1f}pt）"

    ladder = " → ".join(row["level_name"] for row in rows)
    # 层级名带 "+" 前缀（"+ HyDE"），拼进句子里会变成"通过+ HyDE、+ RRF"，
    # 这里去掉前缀只保留能力名。
    ability_names = "、".join(
        row["level_name"].lstrip("+").strip() for row in rows[1:]
    )
    lines = [
        "# 作品集摘要（数字来自本次实测）",
        "",
        f"- 评测集：{n_questions} 条问题，覆盖实体事实 / 口语化提问 / 分类 / 领域 / 时间 / 整期概览题型",
        f"- 实验阶梯：{ladder}",
        f"- {delta_text('recall@5', 'Recall@5')}",
        f"- {delta_text('recall@10', 'Recall@10')}",
        f"- {delta_text('mrr@10', 'MRR@10')}",
    ]
    if final.get("faithfulness") is not None:
        lines.append(f"- {delta_text('faithfulness', 'Faithfulness')}")
    if final.get("answer_relevancy") is not None:
        lines.append(f"- {delta_text('answer_relevancy', 'Answer Relevancy')}")
    lines.append("")
    lines.append("## 可直接使用的简历句式（按实测替换前确认）")
    lines.append("")
    lines.append(
        f"> 构建覆盖 {n_types or 'N'} 类场景的 {n_questions} 题分层评测集，通过"
        f" {ability_names} 的逐层消融验证，"
        f"将检索 {delta_text('recall@10', 'Recall@10')}，"
        + (
            f"答案忠实度 {_pct(baseline.get('faithfulness'))} → {_pct(final.get('faithfulness'))}，"
            if final.get("faithfulness") is not None
            else ""
        )
        + f"MRR@10 {_pct(baseline.get('mrr@10'))} → {_pct(final.get('mrr@10'))}。"
    )
    if offline:
        lines += ["", "⚠️ 本次为离线冒烟模式，数字不可写入简历。"]
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="RAG 消融阶梯实验")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--suite", default="ablation_ladder")
    parser.add_argument("--levels", default=None)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--top-k", type=int, default=CANDIDATE_TOP_K)
    parser.add_argument("--no-answer", action="store_true")
    parser.add_argument("--no-judge", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--offline-smoke", action="store_true")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    items, dataset_meta = load_dataset(args.dataset)
    if args.limit:
        items = stratified_limit(items, args.limit)
        dataset_meta["subset_limit"] = args.limit
    dataset_meta["summary"] = summary(items)
    levels = resolve_levels(args.levels, args.suite)

    offline_corpus = None
    if args.offline_smoke:
        from evaluation.build_dataset import load_local_corpus

        offline_corpus = load_local_corpus()
        args.no_answer = True
        args.no_judge = True
        print(
            f"[ablation] ⚠️ 离线冒烟模式：本地词法检索（{len(offline_corpus)} 条语料），"
            "数字不代表线上效果"
        )

    run_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}__ablation"
    out_dir = Path(args.out) if args.out else RESULTS_DIR / run_id
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
        title="RAG 消融阶梯：Baseline → 优化方案",
        suite="ablation_ladder",
    )

    portfolio = build_portfolio_summary(
        result["rows"],
        len(items),
        offline=offline_corpus is not None,
        n_types=len(dataset_meta["summary"].get("by_question_type", {})),
    )
    (result["out_dir"] / "portfolio_summary.md").write_text(portfolio, encoding="utf-8")

    print("\n" + result["markdown"])
    print("\n" + portfolio)
    print(f"\n[ablation] 结果目录：{result['out_dir']}")


if __name__ == "__main__":
    main()
