"""评测数据集：数据结构、读写、金标匹配。

金标为什么不用 chunk_id 做唯一标识？
    本地 chunks.json 是导入节点在 **写入 Qdrant 之前** 落盘的备份，
    那时 chunk_id（UUID）还没生成，所以本地数据集拿不到 chunk_id。
    因此这里支持三种 key，按优先级匹配，谁在就用谁：

        cid:  <chunk_id>                     从 Qdrant 直接生成的数据集用这个，最准
        hash: <md5(strip(content))>          内容哈希，跨导入批次稳定
        ttl:  <file_title>|<title>|<part>    结构三元组，兜底

    检索结果的 payload 里同时有 chunk_id / content / file_title / title / part，
    所以三种 key 都能算出来，匹配时取第一个命中的 gold key，
    这样"召回一条 = 覆盖一个金标"不会被重复计数。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable


def content_hash(text: str) -> str:
    """与数据集、检索结果两侧共用的内容哈希（strip 后取 md5）。"""
    return hashlib.md5((text or "").strip().encode("utf-8")).hexdigest()


def _norm(text: Any) -> str:
    return ("" if text is None else str(text)).strip()


@dataclass
class GoldChunk:
    """一条金标证据（对应一个 Chunk）。"""

    content_hash: str = ""
    chunk_id: str = ""
    file_title: str = ""
    title: str = ""
    part: int = 0
    snippet: str = ""

    @classmethod
    def from_payload(cls, payload: dict) -> "GoldChunk":
        content = _norm(payload.get("content"))
        return cls(
            content_hash=content_hash(content),
            chunk_id=_norm(payload.get("chunk_id")),
            file_title=_norm(payload.get("file_title")),
            title=_norm(payload.get("title")),
            part=int(payload.get("part") or 0),
            snippet=content[:90].replace("\n", " "),
        )

    def keys(self) -> list[str]:
        keys: list[str] = []
        if self.chunk_id:
            keys.append(f"cid:{self.chunk_id}")
        if self.content_hash:
            keys.append(f"hash:{self.content_hash}")
        if self.file_title and self.title:
            keys.append(f"ttl:{self.file_title}|{self.title}|{self.part}")
        return keys

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> "GoldChunk":
        return cls(
            content_hash=_norm(raw.get("content_hash")),
            chunk_id=_norm(raw.get("chunk_id")),
            file_title=_norm(raw.get("file_title")),
            title=_norm(raw.get("title")),
            part=int(raw.get("part") or 0),
            snippet=_norm(raw.get("snippet")),
        )


@dataclass
class EvalItem:
    """一条测试问题。"""

    id: str
    question: str
    question_type: str = "unknown"
    difficulty: str = "medium"
    requires_filter: bool = False
    gold: list[GoldChunk] = field(default_factory=list)
    gold_meta: dict = field(default_factory=dict)
    raw_gold_size: int = 0
    source_files: list[str] = field(default_factory=list)
    notes: str = ""

    def gold_keys(self) -> set[str]:
        keys: set[str] = set()
        for gold in self.gold:
            keys.update(gold.keys())
        return keys

    def to_dict(self) -> dict:
        data = asdict(self)
        data["gold"] = [gold.to_dict() for gold in self.gold]
        return data

    @classmethod
    def from_dict(cls, raw: dict) -> "EvalItem":
        return cls(
            id=_norm(raw.get("id")),
            question=_norm(raw.get("question")),
            question_type=_norm(raw.get("question_type")) or "unknown",
            difficulty=_norm(raw.get("difficulty")) or "medium",
            requires_filter=bool(raw.get("requires_filter")),
            gold=[GoldChunk.from_dict(item) for item in raw.get("gold") or []],
            gold_meta=raw.get("gold_meta") or {},
            raw_gold_size=int(raw.get("raw_gold_size") or 0),
            source_files=list(raw.get("source_files") or []),
            notes=_norm(raw.get("notes")),
        )


def payload_keys(payload: dict) -> list[str]:
    """把检索结果的 payload 转成与 GoldChunk 同构的 key 列表（优先级同上）。"""
    keys: list[str] = []
    chunk_id = _norm(payload.get("chunk_id"))
    if chunk_id:
        keys.append(f"cid:{chunk_id}")
    content = _norm(payload.get("content"))
    if content:
        keys.append(f"hash:{content_hash(content)}")
    file_title = _norm(payload.get("file_title"))
    title = _norm(payload.get("title"))
    if file_title and title:
        part = int(payload.get("part") or 0)
        keys.append(f"ttl:{file_title}|{title}|{part}")
    return keys


def match_payload(payload: dict, gold_keys: set[str], miss_marker: str) -> str:
    """返回该检索结果命中的 gold key；没命中则返回一个唯一的占位符。

    占位符必须唯一，这样指标里的「去重后取前 K 条」不会把未命中项误合并。
    """
    for key in payload_keys(payload):
        if key in gold_keys:
            return key
    return miss_marker


def load_dataset(path: str | Path) -> tuple[list[EvalItem], dict]:
    """读取数据集，返回 (items, meta)。"""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"数据集不存在：{path}\n"
            f"先运行：PYTHONPATH=. python -m evaluation.build_dataset"
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return [EvalItem.from_dict(item) for item in raw], {}
    meta = raw.get("meta") or {}
    items = [EvalItem.from_dict(item) for item in raw.get("items") or []]
    return items, meta


def save_dataset(path: str | Path, items: Iterable[EvalItem], meta: dict | None = None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"meta": meta or {}, "items": [item.to_dict() for item in items]}
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def validate(items: list[EvalItem]) -> list[str]:
    """返回问题清单（不抛异常，交给调用方决定是否中止）。"""
    problems: list[str] = []
    seen_ids: set[str] = set()
    seen_questions: set[str] = set()
    for item in items:
        if not item.id:
            problems.append("存在缺少 id 的条目")
        if item.id in seen_ids:
            problems.append(f"重复的 id：{item.id}")
        seen_ids.add(item.id)
        if not item.question:
            problems.append(f"{item.id} 缺少 question")
        if item.question in seen_questions:
            problems.append(f"重复的问题：{item.question}")
        seen_questions.add(item.question)
        if not item.gold:
            problems.append(f"{item.id} 没有金标 chunk")
        if any(not gold.keys() for gold in item.gold):
            problems.append(f"{item.id} 存在无法生成 key 的金标（缺 hash 与结构字段）")
    return problems


def summary(items: list[EvalItem]) -> dict:
    """数据集概览：题型分布、是否依赖元数据过滤、金标规模。"""
    by_type: dict[str, int] = {}
    by_difficulty: dict[str, int] = {}
    gold_sizes: list[int] = []
    need_filter = 0
    for item in items:
        by_type[item.question_type] = by_type.get(item.question_type, 0) + 1
        by_difficulty[item.difficulty] = by_difficulty.get(item.difficulty, 0) + 1
        gold_sizes.append(len(item.gold) or len(item.gold_keys()))
        if item.requires_filter:
            need_filter += 1
    return {
        "total": len(items),
        "by_question_type": dict(sorted(by_type.items())),
        "by_difficulty": dict(sorted(by_difficulty.items())),
        "requires_metadata_filter": need_filter,
        "avg_gold_size": (sum(gold_sizes) / len(gold_sizes)) if gold_sizes else 0.0,
    }


def stratified_limit(items: list[EvalItem], n: int) -> list[EvalItem]:
    """按题型轮流取样，避免 --limit 只取到排在前面的某一类问题。

    例如数据集里前 30 条都是 entity_fact，直接取前 10 条会得到一份
    "只有实体题"的冒烟集，看不多路召回/过滤的真实差异。
    """
    if n <= 0 or n >= len(items):
        return list(items)
    buckets: dict[str, list[EvalItem]] = {}
    for item in items:
        buckets.setdefault(item.question_type, []).append(item)
    ordered_types = sorted(buckets)
    picked: list[EvalItem] = []
    cursor = 0
    while len(picked) < n:
        advanced = False
        for name in ordered_types:
            bucket = buckets[name]
            if cursor < len(bucket):
                picked.append(bucket[cursor])
                advanced = True
                if len(picked) >= n:
                    break
        if not advanced:
            break
        cursor += 1
    return sorted(picked, key=lambda item: item.id)
