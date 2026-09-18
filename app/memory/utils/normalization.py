"""文本归一化工具。

用于 Entity 精确匹配和 BM25 关键词处理。
"""
import re


STOPWORDS = {
    "a",
    "an",
    "the",
    "is",
    "are",
    "was",
    "were",
    "of",
    "to",
    "in",
    "on",
    "for",
    "and",
    "or",
    "with",
    "一个",
    "这个",
    "那个",
    "什么",
    "怎么",
}


def normalize_text(text: str) -> str:
    text = (text or "").strip().lower() # 处理空格、转小写
    text = re.sub(r"[^\w\u4e00-\u9fff]+", " ", text) # 保留有效字符、去除干扰符号
    return " ".join(text.split()) # 折叠多余空格


def tokenize(text: str) -> list[str]:
    text = normalize_text(text)
    tokens = []
    for token in text.split():
        if token and token not in STOPWORDS:
            tokens.append(token)
    return tokens


def lemmatize_token(token: str) -> str:
    """项目当前不需要重型词形还原，这里保留轻量英文规则。"""
    token = token.lower()
    if token.endswith("ing") and len(token) > 5:
        return token[:-3]
    if token.endswith("ed") and len(token) > 4:
        return token[:-2]
    return token


def normalize_entity_key(entity: str) -> str:
    return normalize_text(entity)
