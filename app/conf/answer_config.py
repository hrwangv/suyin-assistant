"""回答节点的 prompt 预算与长期记忆检索条数。

这几个数决定「最终送给回答模型的内容有多大」：

    可用输入 = 输入总预算 - 输出预留 - 安全缓冲

三段来源（RAG 文档 / 短期历史 / 长期记忆）在这个可用额度内按优先级竞争，
超额时按「长期记忆 → 最旧的历史 → 最低分的文档」的顺序整条丢弃
（见 node_answer_output._fit_prompt_budget）。

为什么不把总预算设成模型窗口（1M）：真正的约束是成本、首字延迟，
以及长上下文下的注意力衰减——窗口装得下，不等于应该塞满。
"""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class AnswerConfig:
    """回答 prompt 的预算配置。"""

    # 输入总预算（token）
    input_token_budget: int
    # 给模型回答预留的输出空间（回答常带列表和表格，别留太少）
    output_reserve_tokens: int
    # 安全缓冲：吸收 tokenizer 差异（deepseek 与 cl100k 不完全一致）和模板变动
    safety_buffer_tokens: int
    # 长期记忆的检索条数（检索阶段就限住，后面还有总预算兜底）
    long_term_memory_top_k: int

    @property
    def available_input_budget(self) -> int:
        """真正可以分给三个来源的 token 数。"""
        return (
            self.input_token_budget
            - self.output_reserve_tokens
            - self.safety_buffer_tokens
        )


answer_config = AnswerConfig(
    input_token_budget=int(os.getenv("ANSWER_INPUT_TOKEN_BUDGET", "32000")),
    output_reserve_tokens=int(os.getenv("ANSWER_OUTPUT_RESERVE_TOKENS", "3000")),
    safety_buffer_tokens=int(os.getenv("ANSWER_SAFETY_BUFFER_TOKENS", "1500")),
    long_term_memory_top_k=int(os.getenv("ANSWER_LONG_TERM_MEMORY_TOP_K", "5")),
)
