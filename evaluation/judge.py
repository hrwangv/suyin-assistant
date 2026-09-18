"""LLM Judge：Faithfulness（忠实度）与 Answer Relevancy（答案相关性）。

两个指标的算法都是 **RAGAS 风格的精简实现**，但都把"打分"从模型手里拿回来，
只让模型做它擅长的判断，数值由 Python 计算 —— 这样分数可复现、可追溯：

    Faithfulness
        1) 让模型把答案拆成原子陈述，并逐条判断"能否被参考内容支持"
        2) 分数 = 被支持的陈述数 / 总陈述数（代码里算，不取模型给的分数）

    Answer Relevancy
        1) 让模型根据答案反推 N 个"这个答案能回答的问题"
        2) 分数 = 这些问题与原始问题的向量余弦相似度均值
           （用项目自己的 embedding，避免再引入一个模型）

两个已知偏差，用之前必须知道：
    · 如果 Judge 模型和生成答案是同一个模型，会系统性偏高 ——
      用环境变量 EVAL_JUDGE_MODEL 换一个模型，并人工抽检 20~30% 校准。
    · Faithfulness 只判断"有没有依据"，不判断"有没有答到点上"，
      所以它必须和 Answer Relevancy 一起看：前者低=可能编，后者低=可能答非所问。
"""
from __future__ import annotations

import json
import math
import re

from app.core.logger import logger
from app.llm.lm_utils import get_llm_client
from app.llm.qwen_embedding_utils import generate_embeddings
from evaluation.cache import cached
from evaluation.config import JUDGE_MODEL, RELEVANCY_N_QUESTIONS

_FENCE_RE = re.compile(r"^```(?:json)?|```$", re.MULTILINE)


def _parse_json(text: str) -> dict:
    """容错解析模型返回的 JSON（去掉 ```json 围栏）。"""
    cleaned = _FENCE_RE.sub("", (text or "").strip()).strip()
    try:
        return json.loads(cleaned)
    except Exception:
        # 模型偶尔会带解释文字，退一步只截取第一个大括号块
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(cleaned[start : end + 1])
            except Exception:
                pass
    logger.warning(f"[judge] 无法解析模型输出为 JSON：{cleaned[:200]}")
    return {}


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


FAITHFULNESS_PROMPT = """你是一个严格的 RAG 评测员。请判断【回答】中的每一条事实性陈述是否被【参考内容】支持。

要求：
1. 把【回答】拆成若干条**原子事实陈述**（一条只讲一件事；不含客套话、过渡句、纯格式内容）。
2. 逐条判断能否在【参考内容】中找到依据：
   - supported=true：参考内容中明确写到，或可由参考内容直接推出；
   - supported=false：参考内容中没有依据，或与参考内容矛盾（编号错、数字错、主体错）。
3. 不要使用你自己的外部知识，只依据【参考内容】判断。
4. 如果【回答】完全是"没有找到相关信息"这类拒答，则 statements 返回空数组。

只输出 JSON，不要输出解释或 Markdown 代码块：
{"statements": [{"statement": "……", "supported": true, "reason": "参考内容第X段提到……"}]}

【参考内容】
{context}

【回答】
{answer}
"""

RELEVANCY_PROMPT = """你是一个 RAG 评测员。请根据【回答】反推 {n} 个**这个回答能够回答的问题**。

要求：
1. 问题要尽量贴近用户真实会问的说法，不要照抄回答里的句子。
2. 问题必须只依据【回答】的内容就答得出来。
3. 不要参考任何外部信息。

只输出 JSON，不要输出解释或 Markdown 代码块：
{{"questions": ["问题1", "问题2", "问题3"]}}

【回答】
{answer}
"""


def judge_faithfulness(
    question: str,
    answer: str,
    context: str,
    *,
    refresh: bool = False,
) -> dict:
    """忠实度：被参考内容支持的陈述占比。

    :return: {"faithfulness": float, "statements": [...], "n_statements": int, "n_supported": int}
    """
    answer = (answer or "").strip()
    if not answer:
        return {"faithfulness": 0.0, "statements": [], "n_statements": 0, "n_supported": 0}
    if not (context or "").strip():
        # 没有参考内容却给出了答案 —— 无法被支持
        return {"faithfulness": 0.0, "statements": [], "n_statements": 0, "n_supported": 0}

    prompt = FAITHFULNESS_PROMPT.format(context=context, answer=answer)

    def _call() -> dict:
        llm = get_llm_client(json_mode=True, model=JUDGE_MODEL)
        response = llm.invoke(prompt)
        return _parse_json(getattr(response, "content", "") or "")

    payload = cached(
        "judge_faithfulness",
        {"model": JUDGE_MODEL or "default", "prompt": prompt},
        _call,
        refresh=refresh,
    )
    statements = payload.get("statements") if isinstance(payload, dict) else []
    statements = [item for item in (statements or []) if isinstance(item, dict)]
    n_supported = sum(1 for item in statements if item.get("supported") is True)
    n_statements = len(statements)
    return {
        "faithfulness": (n_supported / n_statements) if n_statements else 0.0,
        "statements": statements,
        "n_statements": n_statements,
        "n_supported": n_supported,
    }


def judge_answer_relevancy(
    question: str,
    answer: str,
    *,
    refresh: bool = False,
    n_questions: int = RELEVANCY_N_QUESTIONS,
) -> dict:
    """答案相关性：由答案反推的问题与原始问题的余弦相似度均值。"""
    answer = (answer or "").strip()
    if not answer:
        return {"answer_relevancy": 0.0, "generated_questions": [], "similarities": []}

    prompt = RELEVANCY_PROMPT.format(n=n_questions, answer=answer)

    def _call() -> dict:
        llm = get_llm_client(json_mode=True, model=JUDGE_MODEL)
        response = llm.invoke(prompt)
        return _parse_json(getattr(response, "content", "") or "")

    payload = cached(
        "judge_relevancy",
        {"model": JUDGE_MODEL or "default", "prompt": prompt},
        _call,
        refresh=refresh,
    )
    generated = [
        str(item).strip()
        for item in (payload.get("questions") or [])
        if str(item).strip()
    ][:n_questions]
    if not generated:
        return {"answer_relevancy": 0.0, "generated_questions": [], "similarities": []}

    vectors = generate_embeddings([question] + generated)
    base = vectors["dense"][0]
    similarities = [_cosine(base, vec) for vec in vectors["dense"][1:]]
    return {
        "answer_relevancy": sum(similarities) / len(similarities) if similarities else 0.0,
        "generated_questions": generated,
        "similarities": similarities,
    }
