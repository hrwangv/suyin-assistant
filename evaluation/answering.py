"""答案生成（复用线上 answer_out.prompt，但不产生副作用）。

为什么不在评测里直接跑 query_app：
    线上 node_answer_output 会写短期记忆、推 SSE 队列、触发长期记忆抽取。
    评测跑 100 题 × 6 层 = 600 次问答，如果走真实图，
    这些副作用会把线上的 MySQL / Redis 记忆污染掉（"评测数据变成用户历史"）。
    所以这里只复用**同一份 prompt 模板 + 同一个模型客户端**，
    在内存里拼上下文，不碰任何存储。

与线上的差异（写进报告里，避免夸大成"完全等价"）：
    - 不带短期历史（history 固定为空）
    - 不带长期记忆（long_term_memories 固定为空）
    - 不注入图片 URL 区块
    评测目标是"检索质量和答案忠实度"，这三项都是与检索质量无关的旁路，
    固定为空反而让不同实验层级之间的对比更干净。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.core.load_prompt import load_prompt
from app.core.tracing import llm_config, observe
from app.llm.lm_utils import get_llm_client
from app.utils.token_budget import count_tokens
from evaluation.retrieval import RetrievedChunk


@dataclass
class AnswerResult:
    question: str
    answer: str = ""
    context: str = ""
    n_docs: int = 0
    prompt_tokens: int = 0
    latency_ms: int = 0
    used_doc_keys: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "question": self.question,
            "answer": self.answer,
            "context": self.context,
            "n_docs": self.n_docs,
            "prompt_tokens": self.prompt_tokens,
            "latency_ms": self.latency_ms,
            "used_doc_keys": self.used_doc_keys,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "AnswerResult":
        return cls(
            question=raw.get("question") or "",
            answer=raw.get("answer") or "",
            context=raw.get("context") or "",
            n_docs=int(raw.get("n_docs") or 0),
            prompt_tokens=int(raw.get("prompt_tokens") or 0),
            latency_ms=int(raw.get("latency_ms") or 0),
            used_doc_keys=list(raw.get("used_doc_keys") or []),
        )


def build_context(candidates: list[RetrievedChunk], max_docs: int = 10) -> str:
    """按线上 node_answer_output 的格式拼参考内容。

    线上格式：[序号][source=..][title=..][score=..]\\n\\n正文
    保持一致的目的是让"答案是否忠实于检索内容"这个判断贴近线上真实输入。
    """
    blocks = []
    for index, chunk in enumerate(candidates[:max_docs], start=1):
        payload = chunk.payload
        blocks.append(
            f"[{index}][source=local]"
            f"[title={payload.get('title') or ''}]"
            f"[score={round(chunk.score, 4)}]\n\n{chunk.content}"
        )
    return "\n\n".join(blocks)


def doc_keys(candidates: list[RetrievedChunk], max_docs: int = 10) -> list[str]:
    """答案实际用到的文档 key（按优先级取第一个可用的）。"""
    from evaluation.dataset import payload_keys

    keys = []
    for chunk in candidates[:max_docs]:
        keys_for_chunk = payload_keys(chunk.payload)
        if keys_for_chunk:
            keys.append(keys_for_chunk[0])
    return keys


@observe(name="eval-answer", as_type="chain", capture_input=False, capture_output=False)
def generate_answer(
    question: str,
    candidates: list[RetrievedChunk],
    *,
    llm=None,
    max_docs: int = 10,
) -> AnswerResult:
    """用检索到的 chunk 生成答案（非流式，无副作用）。"""
    started = time.time()
    context = build_context(candidates, max_docs=max_docs)
    prompt = load_prompt(
        "answer_out",
        context=context,
        history="没有历史对话记录！",
        long_term_memories="没有相关长期记忆。",
        question=question,
    )
    llm = llm or get_llm_client()
    # config 里挂 Langfuse callback（未开启追踪时是空 dict，行为不变）
    response = llm.invoke(prompt, config=llm_config())
    answer = response.content if isinstance(response.content, str) else str(response.content)
    return AnswerResult(
        question=question,
        answer=answer.strip(),
        context=context,
        n_docs=min(len(candidates), max_docs),
        prompt_tokens=count_tokens(prompt),
        latency_ms=int((time.time() - started) * 1000),
        used_doc_keys=doc_keys(candidates, max_docs=max_docs),
    )
