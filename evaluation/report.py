"""结果汇总与出表（Markdown / CSV / JSON）。

最终产出两张表：

  表 1  主表：每个消融层级的 Recall@5 / Recall@10 / MRR@10 / Faithfulness /
        Answer Relevancy / 相对 baseline 的增益 —— 可直接贴进 PPT 或简历
  表 2  分题型 Recall@10：看每一层到底帮了哪一类问题（最能说明"为什么改"）
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

MAIN_COLUMNS = (
    ("recall@5", "Recall@5"),
    ("recall@10", "Recall@10"),
    ("mrr@10", "MRR@10"),
    ("faithfulness", "Faithfulness"),
    ("answer_relevancy", "Answer Relevancy"),
)


def _pct(value) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.1f}%"


def _delta(value, base) -> str:
    if value is None or base is None:
        return "—"
    return f"{(value - base) * 100:+.1f}pt"


def build_level_row(level_id: str, level_name: str, note: str, overall: dict) -> dict:
    return {
        "level_id": level_id,
        "level_name": level_name,
        "note": note,
        "n_questions": overall.get("n_questions", 0),
        "avg_candidates": overall.get("avg_candidates", 0.0),
        "avg_latency_ms": overall.get("avg_latency_ms"),
        **{key: overall.get(key) for key, _ in MAIN_COLUMNS},
    }


def build_markdown(
    rows: list[dict],
    *,
    title: str = "RAG 消融评测结果",
    dataset_meta: dict | None = None,
    by_type: dict[str, dict] | None = None,
    notes: list[str] | None = None,
) -> str:
    lines: list[str] = [f"# {title}", ""]

    if dataset_meta:
        summary = dataset_meta.get("summary") or {}
        lines += ["## 数据集", ""]
        lines.append(f"- 题目数量：{summary.get('total', dataset_meta.get('total', '—'))}")
        source = f"- 语料来源：{dataset_meta.get('source', '—')}"
        if dataset_meta.get("collection"):
            source += f"（collection={dataset_meta['collection']}）"
        lines.append(source)
        lines.append(
            f"- 题型分布：{json.dumps(summary.get('by_question_type', {}), ensure_ascii=False)}"
        )
        lines.append(f"- 依赖元数据过滤的题数：{summary.get('requires_metadata_filter', '—')}")
        lines.append(f"- 平均金标条数：{round(summary.get('avg_gold_size', 0) or 0, 2)}")
        lines.append("")

    lines += ["## 主表", ""]
    header = ["层级"] + [label for _, label in MAIN_COLUMNS] + [
        "Δ Recall@10",
        "平均候选数",
        "平均耗时(ms)",
    ]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "---|" * len(header))

    baseline = rows[0] if rows else {}
    base_recall10 = baseline.get("recall@10")
    for row in rows:
        cells = [row["level_name"]]
        cells += [_pct(row.get(key)) for key, _ in MAIN_COLUMNS]
        cells.append("基准" if row is baseline else _delta(row.get("recall@10"), base_recall10))
        cells.append(f"{row.get('avg_candidates') or 0:.1f}")
        latency = row.get("avg_latency_ms")
        cells.append(f"{latency:.0f}" if latency else "—")
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")

    if by_type:
        lines += ["## 分题型 Recall@10（看每一层帮了哪类问题）", ""]
        type_names = sorted({name for stats in by_type.values() for name in stats})
        header = ["层级"] + type_names
        lines.append("| " + " | ".join(header) + " |")
        lines.append("|" + "---|" * len(header))
        for row in rows:
            stats = by_type.get(row["level_id"], {})
            cells = [row["level_name"]]
            cells += [_pct((stats.get(name) or {}).get("recall@10")) for name in type_names]
            lines.append("| " + " | ".join(cells) + " |")
        lines.append("")

    if notes:
        lines += ["## 说明", ""]
        lines += [f"- {note}" for note in notes]
        lines.append("")

    return "\n".join(lines)


def write_csv(path: str | Path, rows: list[dict]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = (
        ["level_id", "level_name"]
        + [key for key, _ in MAIN_COLUMNS]
        + ["n_questions", "avg_candidates", "avg_latency_ms"]
    )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in columns})
    return path
