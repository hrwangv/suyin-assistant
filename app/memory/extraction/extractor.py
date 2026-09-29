"""LLM Memory Extraction Pipeline。"""
from datetime import datetime
from typing import List, Optional

from langchain_core.messages import HumanMessage, SystemMessage

from app.core.tracing import llm_config
from app.llm.lm_utils import get_llm_client
from app.memory.extraction.parser import parse_json
from app.memory.extraction.prompts import (
    ADDITIVE_EXTRACTION_PROMPT,
    AGENT_CONTEXT_SUFFIX,
    generate_additive_extraction_prompt,
)


class MemoryExtractor:
    def extract(
        self,
        new_messages: List[dict],
        existing_memories: List[dict],
        last_messages: List[dict],
        summary: Optional[str] = None,
        recently_extracted_memories: Optional[List[dict]] = None,
        custom_instructions: Optional[str] = None,
    ) -> dict:
        """调用 LLM 抽取长期记忆。

        system prompt 负责角色边界、抽取规则和输出结构；
        user prompt 负责本次动态上下文。
        """
        now = datetime.now().isoformat()
        system_prompt = ADDITIVE_EXTRACTION_PROMPT + AGENT_CONTEXT_SUFFIX
        user_prompt = generate_additive_extraction_prompt(
            summary=summary,
            recently_extracted_memories=recently_extracted_memories,
            existing_memories=existing_memories,
            new_messages=new_messages,
            last_k_messages=last_messages,
            current_date=now,
            observation_date=now,
            custom_instructions=custom_instructions,
        )

        # 区分系统角色和用户角色，json_mode=True 会附加 json_object 输出约束。
        llm = get_llm_client(json_mode=True)
        response = llm.invoke(
            [
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt),
            ],
            config=llm_config(),
        )
        return parse_json(response.content)
