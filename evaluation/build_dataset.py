"""生成评测数据集（默认 100 条中文测试问题）。

为什么用「反向出题」：从**已经入库的真实 Chunk** 反推问题，
每条问题的金标证据天然就是那条 Chunk，不需要人工先写问题再去找答案，
标注成本几乎为零，而且完全贴合真实语料。

数据来源两选一：

    --source local   扫描 output/**/chunks.json（离线可用，默认）
                     —— 注意：本地 chunks.json 是**入 Qdrant 之前**的备份，
                        拿不到 chunk_id，所以金标用「内容哈希 + 结构三元组」匹配
    --source qdrant 直接 scroll kb_chunks（推荐用于正式评测）
                     —— 金标直接带 chunk_id，且能保证金标一定在索引里

题型设计（每类都对应消融阶梯里某一层的增益点）：

    entity_fact       实体事实   —— 考察基础召回
    colloquial_entity 口语化实体 —— 考察 Query Rewrite（"最近有啥新动静"）
    file_category     文件+分类  —— 考察 Metadata Filter（category）
    domain_facet      文件+领域  —— 考察 Metadata Filter（domain）
    time_category     时间+分类  —— 考察相对/绝对时间解析 + 范围过滤
    file_overview     整期概览   —— 考察多主题覆盖

运行：
    PYTHONPATH=. python -m evaluation.build_dataset
    PYTHONPATH=. python -m evaluation.build_dataset --source qdrant --n 100
"""
from __future__ import annotations

import argparse
import glob
import json
import random
import re
from calendar import monthrange
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from evaluation.config import (
    CHUNKS_COLLECTION,
    DEFAULT_DATASET,
    LOCAL_CHUNK_GLOB,
    PROJECT_ROOT,
)
from evaluation.dataset import EvalItem, GoldChunk, content_hash, save_dataset, summary, validate

# 与 node_document_split 的 CATEGORY_KEYWORDS 保持一致
KNOWN_CATEGORIES = (
    "重点要闻",
    "重点客户",
    "行业动态",
    "同业资讯",
    "头部及域内企业要闻",
    "宏观动态",
)

# 领域过滤时排除的噪声值（"其他"不具区分度；带"："的是解析异常产物）
DOMAIN_BLOCKLIST = {"其他", "无", "None", "null"}

# 标题里的"新闻体"词：出现这些说明冒号左边是事件标题，不是主体名
HEADLINE_WORDS = (
    "公示", "发布", "印发", "出台", "生效", "结果", "名单", "汇总", "通知", "方案",
    "政策", "公告", "披露", "签约", "中标", "获批", "投产", "开工", "关于", "拟",
    "计划", "规划", "领域", "动态", "要闻", "资讯", "日报", "晨报", "行业", "市场",
    "全国", "我国", "全省", "全市", "观点", "解读", "分析", "盘点", "速览",
)

# 过于泛化、不具区分度的"伪实体"
GENERIC_ENTITIES = {"公司", "集团", "银行", "金融", "科技", "行业", "市场", "政策", "会议"}

# 标题前的列表符号 / 修饰符
_LEADING_NOISE_RE = re.compile(r"^[\s·・•∙\-—–—、*#>]+")
_TRAILING_NOISE_RE = re.compile(r"[\s·・•∙\-—–—、*#>：:]+$")

_TITLE_MARK_RE = re.compile(r"^\s*#{1,6}\s*")
_ENTITY_SPLIT_RE = re.compile(r"[:：]")
_MONTH_RE = re.compile(r"(\d{4})\D{0,3}(\d{1,2})\s*月")


@dataclass
class CorpusChunk:
    """本地 / Qdrant 统一后的语料条目。"""

    content: str
    file_title: str = ""
    title: str = ""
    parent_title: str = ""
    part: int = 0
    category: str = ""
    domain: str = ""
    news_date: str = ""      # 统一成 YYYY-MM-DD
    section: str = ""
    chunk_id: str = ""
    source_file: str = ""

    @property
    def hash(self) -> str:
        return content_hash(self.content)

    def to_gold(self) -> GoldChunk:
        return GoldChunk(
            content_hash=self.hash,
            chunk_id=self.chunk_id,
            file_title=self.file_title,
            title=self.title,
            part=self.part,
            snippet=self.content[:90].replace("\n", " "),
        )


# ------------------------------------------------------------------ #
# 语料加载
# ------------------------------------------------------------------ #
def _clean_text(value) -> str:
    return ("" if value is None else str(value)).strip()


def _clean_title(title: str) -> str:
    return _TITLE_MARK_RE.sub("", _clean_text(title)).strip()


def normalize_date(value) -> str:
    """把 20260410 / 2026-04-10 / RFC3339 统一成 YYYY-MM-DD；识别不了返回空串。"""
    text = _clean_text(value)
    if not text:
        return ""
    digits = re.sub(r"\D", "", text)
    if len(digits) >= 8:
        compact = digits[:8]
        try:
            return datetime.strptime(compact, "%Y%m%d").strftime("%Y-%m-%d")
        except ValueError:
            return ""
    return ""


def load_local_corpus(pattern: str = LOCAL_CHUNK_GLOB) -> list[CorpusChunk]:
    """扫描本地 chunks.json，按内容去重。"""
    seen: set[str] = set()
    corpus: list[CorpusChunk] = []
    for path in sorted(glob.glob(str(PROJECT_ROOT / pattern), recursive=True)):
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(raw, list):
            continue
        for item in raw:
            if not isinstance(item, dict):
                continue
            content = _clean_text(item.get("content"))
            if len(content) < 30:
                continue
            key = content_hash(content)
            if key in seen:
                continue
            seen.add(key)
            corpus.append(
                CorpusChunk(
                    content=content,
                    file_title=_clean_text(item.get("file_title")),
                    title=_clean_title(item.get("title")),
                    parent_title=_clean_title(item.get("parent_title")),
                    part=int(item.get("part") or 0),
                    category=_clean_text(item.get("category")),
                    domain=_clean_text(item.get("domain")),
                    news_date=normalize_date(item.get("news_date")),
                    section=_clean_text(item.get("section")),
                    source_file=str(Path(path).relative_to(PROJECT_ROOT)),
                )
            )
    return corpus


def load_qdrant_corpus(collection: str = CHUNKS_COLLECTION, limit: int = 5000) -> list[CorpusChunk]:
    """从 Qdrant scroll 出真实入库的 payload（推荐用于正式评测）。"""
    from app.utils.qdrant_utils import get_qdrant_client

    client = get_qdrant_client()
    corpus: list[CorpusChunk] = []
    offset = None
    while len(corpus) < limit:
        points, offset = client.scroll(
            collection_name=collection,
            limit=min(256, limit - len(corpus)),
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for point in points:
            payload = point.payload or {}
            content = _clean_text(payload.get("content"))
            if len(content) < 30:
                continue
            corpus.append(
                CorpusChunk(
                    content=content,
                    file_title=_clean_text(payload.get("file_title")),
                    title=_clean_title(payload.get("title")),
                    parent_title=_clean_title(payload.get("parent_title")),
                    part=int(payload.get("part") or 0),
                    category=_clean_text(payload.get("category")),
                    domain=_clean_text(payload.get("domain")),
                    news_date=normalize_date(payload.get("news_date")),
                    section=_clean_text(payload.get("section")),
                    chunk_id=_clean_text(payload.get("chunk_id")) or str(point.id),
                )
            )
        if offset is None:
            break
    return corpus


# ------------------------------------------------------------------ #
# 题型生成器
# ------------------------------------------------------------------ #
def _entity_of(chunk: CorpusChunk) -> str:
    """从"协创数据：2026年拟申请800亿融资租赁额度"这种标题里取出实体名。

    冒号左边不一定是主体名（"湖南公示新能源竞价结果："这种是新闻标题），
    所以这里要过三道筛：去列表符号 → 长度区间 → 命中新闻体词/泛化词就丢弃。
    """
    title = chunk.title
    if not title or not _ENTITY_SPLIT_RE.search(title):
        return ""
    head = _ENTITY_SPLIT_RE.split(title, maxsplit=1)[0]
    head = _LEADING_NOISE_RE.sub("", head)
    head = _TRAILING_NOISE_RE.sub("", head).strip()

    if not (2 <= len(head) <= 12):
        return ""
    if head in KNOWN_CATEGORIES or head in GENERIC_ENTITIES:
        return ""
    if head.isdigit():
        return ""
    if any(word in head for word in HEADLINE_WORDS):
        return ""
    # 纯描述性短语（含标点/空格）不算主体名
    if re.search(r"[\s，,。.；;！!？?]", head):
        return ""
    return head


def _entity_pool(corpus: list[CorpusChunk]) -> dict[str, list[CorpusChunk]]:
    """实体 → 提到该实体的 chunk（标题或正文包含）。"""
    names: dict[str, set[str]] = {}
    for chunk in corpus:
        name = _entity_of(chunk)
        if name:
            names.setdefault(name, set()).add(chunk.hash)

    pool: dict[str, list[CorpusChunk]] = {}
    for name in names:
        hits = [c for c in corpus if name in c.content]
        if hits:
            # 金标排序：标题里点名该实体的优先（最相关），其次内容短的（信息密度高）
            hits.sort(key=lambda c: (name not in c.title, len(c.content), c.hash))
            # 金标集合控制在 1~5 条，保证 Recall@5 有意义
            pool[name] = hits[:5]
    return pool


def _make_item_id(index: int) -> str:
    return f"q{index:04d}"


def generate_items(corpus: list[CorpusChunk], n: int = 100, seed: int = 20260915) -> list[EvalItem]:
    """按题型配额生成 n 条问题。"""
    rng = random.Random(seed)
    items: list[EvalItem] = []
    used_questions: set[str] = set()

    def add(question: str, **kwargs) -> bool:
        if question in used_questions:
            return False
        used_questions.add(question)
        items.append(EvalItem(id=_make_item_id(len(items) + 1), question=question, **kwargs))
        return True

    # ---------- 1. 实体事实（目标 30 条） ----------
    entity_pool = _entity_pool(corpus)
    entity_names = sorted(entity_pool, key=lambda name: (len(entity_pool[name]), name))
    rng.shuffle(entity_names)
    entity_quota = int(n * 0.30)
    used_entities: set[str] = set()
    entity_templates = (
        "经营晨报里关于「{name}」的信息有哪些？",
        "「{name}」在经营晨报中有哪些相关报道？",
        "我想了解「{name}」的情况，经营晨报里有哪些相关内容？",
        "关于「{name}」，经营晨报是怎么报道的？",
    )
    entity_index = 0
    for name in entity_names:
        if len([i for i in items if i.question_type == "entity_fact"]) >= entity_quota:
            break
        gold = entity_pool[name]
        template = entity_templates[entity_index % len(entity_templates)]
        entity_index += 1
        if add(
            template.format(name=name),
            question_type="entity_fact",
            difficulty="medium",
            gold=[c.to_gold() for c in gold],
            raw_gold_size=len([c for c in corpus if name in c.content]),
            source_files=sorted({c.source_file for c in gold if c.source_file}),
            notes=f"实体={name}",
        ):
            used_entities.add(name)

    # ---------- 2. 口语化实体（目标 10 条，考察 Query Rewrite） ----------
    colloquial_quota = int(n * 0.10)
    for name in entity_names:
        if len([i for i in items if i.question_type == "colloquial_entity"]) >= colloquial_quota:
            break
        if name in used_entities:
            continue
        gold = entity_pool[name]
        if add(
            f"{name}最近有啥新动静？",
            question_type="colloquial_entity",
            difficulty="medium",
            gold=[c.to_gold() for c in gold],
            raw_gold_size=len([c for c in corpus if name in c.content]),
            source_files=sorted({c.source_file for c in gold if c.source_file}),
            notes=f"实体={name}；口语化提问，考察问题改写",
        ):
            used_entities.add(name)

    # ---------- 3. 文件 + 分类（目标 20 条，考察 Metadata Filter） ----------
    by_file_category: dict[tuple[str, str], list[CorpusChunk]] = {}
    for chunk in corpus:
        if chunk.file_title and chunk.category in KNOWN_CATEGORIES:
            by_file_category.setdefault((chunk.file_title, chunk.category), []).append(chunk)
    file_cat_keys = [
        key for key, chunks in by_file_category.items() if 1 <= len(chunks) <= 8
    ]
    rng.shuffle(file_cat_keys)
    file_cat_quota = int(n * 0.20)
    for file_title, category in file_cat_keys:
        if len([i for i in items if i.question_type == "file_category"]) >= file_cat_quota:
            break
        gold = by_file_category[(file_title, category)]
        add(
            f"《{file_title}》的「{category}」部分讲了什么？",
            question_type="file_category",
            difficulty="easy",
            requires_filter=True,
            gold=[c.to_gold() for c in gold],
            raw_gold_size=len(gold),
            gold_meta={"file_title": file_title, "category": category},
            source_files=sorted({c.source_file for c in gold if c.source_file}),
            notes=f"分类={category}",
        )

    # ---------- 4. 文件 + 领域（目标 15 条） ----------
    by_file_domain: dict[tuple[str, str], list[CorpusChunk]] = {}
    for chunk in corpus:
        domain = chunk.domain
        if not chunk.file_title or not domain:
            continue
        if domain in DOMAIN_BLOCKLIST or len(domain) > 8 or "：" in domain:
            continue
        by_file_domain.setdefault((chunk.file_title, domain), []).append(chunk)
    file_domain_keys = [
        key for key, chunks in by_file_domain.items() if 1 <= len(chunks) <= 8
    ]
    rng.shuffle(file_domain_keys)
    domain_quota = int(n * 0.15)
    for file_title, domain in file_domain_keys:
        if len([i for i in items if i.question_type == "domain_facet"]) >= domain_quota:
            break
        gold = by_file_domain[(file_title, domain)]
        add(
            f"《{file_title}》里{domain}领域有哪些动态？",
            question_type="domain_facet",
            difficulty="medium",
            requires_filter=True,
            gold=[c.to_gold() for c in gold],
            raw_gold_size=len(gold),
            gold_meta={"file_title": file_title, "domain": domain},
            source_files=sorted({c.source_file for c in gold if c.source_file}),
            notes=f"领域={domain}",
        )

    # ---------- 5. 时间 + 分类（目标 15 条，考察时间范围过滤） ----------
    by_month_category: dict[tuple[str, str], list[CorpusChunk]] = {}
    for chunk in corpus:
        if chunk.news_date and chunk.category in KNOWN_CATEGORIES:
            month = chunk.news_date[:7]
            by_month_category.setdefault((month, chunk.category), []).append(chunk)
    # 一个月的某个分类往往有十几条，这里放宽到"≥2 条即可"，
    # 金标取 8 条（按 file_title + title 稳定排序），raw_gold_size 记录真实规模。
    month_cat_keys = [key for key, chunks in by_month_category.items() if len(chunks) >= 2]
    rng.shuffle(month_cat_keys)
    time_quota = int(n * 0.15)
    max_time_gold = 8
    for month, category in month_cat_keys:
        if len([i for i in items if i.question_type == "time_category"]) >= time_quota:
            break
        full = by_month_category[(month, category)]
        gold = sorted(full, key=lambda c: (c.file_title, c.title))[:max_time_gold]
        year, mon = month.split("-")
        last_day = monthrange(int(year), int(mon))[1]
        add(
            f"{int(year)}年{int(mon)}月的经营晨报里，{category}方面有哪些值得关注的？",
            question_type="time_category",
            difficulty="hard",
            requires_filter=True,
            gold=[c.to_gold() for c in gold],
            raw_gold_size=len(full),
            gold_meta={
                "category": category,
                "date_from": f"{month}-01",
                "date_to": f"{month}-{last_day:02d}",
            },
            source_files=sorted({c.source_file for c in gold if c.source_file}),
            notes=f"月份={month}，分类={category}",
        )

    # ---------- 6. 整期概览（目标 10 条） ----------
    by_file: dict[str, list[CorpusChunk]] = {}
    for chunk in corpus:
        if chunk.file_title:
            by_file.setdefault(chunk.file_title, []).append(chunk)
    file_keys = sorted(by_file, key=lambda name: (-len(by_file[name]), name))
    rng.shuffle(file_keys)
    overview_quota = int(n * 0.10)
    for file_title in file_keys:
        if len([i for i in items if i.question_type == "file_overview"]) >= overview_quota:
            break
        chunks = by_file[file_title]
        # 代表性取样：每个分类取一条，不足再按顺序补
        picked: list[CorpusChunk] = []
        seen_cat: set[str] = set()
        for chunk in chunks:
            if chunk.category and chunk.category not in seen_cat:
                picked.append(chunk)
                seen_cat.add(chunk.category)
            if len(picked) >= 5:
                break
        for chunk in chunks:
            if len(picked) >= 5:
                break
            if chunk not in picked:
                picked.append(chunk)
        add(
            f"《{file_title}》这一期主要讲了哪些内容？",
            question_type="file_overview",
            difficulty="easy",
            gold=[c.to_gold() for c in picked],
            raw_gold_size=len(chunks),
            gold_meta={"file_title": file_title},
            source_files=sorted({c.source_file for c in picked if c.source_file}),
        )

    # ---------- 兜底：配额没打满就继续补实体题 ----------
    if len(items) < n:
        for name in entity_names:
            if len(items) >= n:
                break
            gold = entity_pool[name]
            add(
                f"关于「{name}」，经营晨报里有哪些相关报道？",
                question_type="entity_fact",
                difficulty="medium",
                gold=[c.to_gold() for c in gold],
                raw_gold_size=len([c for c in corpus if name in c.content]),
                source_files=sorted({c.source_file for c in gold if c.source_file}),
                notes=f"实体={name}（补足配额）",
            )

    return items[:n]


def main() -> None:
    parser = argparse.ArgumentParser(description="生成 RAG 评测数据集")
    parser.add_argument("--source", choices=("local", "qdrant"), default="local")
    parser.add_argument("--collection", default=CHUNKS_COLLECTION)
    parser.add_argument("--n", type=int, default=100)
    parser.add_argument("--seed", type=int, default=20260915)
    parser.add_argument("--out", default=str(DEFAULT_DATASET))
    args = parser.parse_args()

    print(f"[build_dataset] 语料来源：{args.source}")
    if args.source == "qdrant":
        corpus = load_qdrant_corpus(args.collection)
    else:
        corpus = load_local_corpus()
    print(f"[build_dataset] 去重后语料条数：{len(corpus)}")
    if not corpus:
        raise SystemExit("没有读到任何语料，请检查 output/**/chunks.json 或 Qdrant 连接")

    items = generate_items(corpus, n=args.n, seed=args.seed)
    problems = validate(items)
    if problems:
        print("[build_dataset] 数据集校验发现问题：")
        for problem in problems[:10]:
            print("   -", problem)

    meta = {
        "source": args.source,
        "collection": args.collection if args.source == "qdrant" else None,
        "seed": args.seed,
        "corpus_size": len(corpus),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "summary": summary(items),
    }
    path = save_dataset(args.out, items, meta)
    print(f"[build_dataset] 已写入：{path}")
    print(json.dumps(meta["summary"], ensure_ascii=False, indent=2))
    print(
        "\n下一步：先确认金标确实在索引里（本地 chunks.json 生成的语料不一定已入库）：\n"
        "    PYTHONPATH=. python -m evaluation.run_eval --check-gold"
    )


if __name__ == "__main__":
    main()
