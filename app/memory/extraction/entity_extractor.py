"""LLM 实体抽取器。

按你的要求，这里不使用 spaCy，而是让 LLM 从记忆文本中抽取实体。
"""
from typing import List

from langchain_core.messages import HumanMessage

from app.core.tracing import llm_config
from app.llm.lm_utils import get_llm_client
from app.memory.extraction.parser import parse_json


ENTITY_EXTRACTION_PROMPT = """
你是一个实体抽取器。请从下面这段文本中抽取重要实体。

记忆文本：
{text}

要求：
1. 只抽取人名、公司名、产品名、品牌、地点、技术名称、项目名等有价值实体。
2. 不要把普通动词、形容词、问候语当作实体。
3. 每个实体尽量是规范、完整、可独立理解的名称。
4. 最多返回 {max_entities} 个实体，允许文本中没有足够的实体。

请严格输出 JSON：
{{
  "entities": [
    {{"text": "实体A"}},
    {{"text": "实体B"}}
  ]
}}
"""


def extract_entities(text: str, max_entities: int = 8) -> List[dict]:
    """调用 LLM 抽取实体，返回 [{"text": "..."}]。"""
    text = (text or "").strip()
    if not text:
        return []

    prompt = ENTITY_EXTRACTION_PROMPT.format(
        text=text,
        max_entities=max_entities,
    )
    llm = get_llm_client(json_mode=True)
    response = llm.invoke([HumanMessage(content=prompt)], config=llm_config())
    result = parse_json(response.content)
    entities = result.get("entities", [])
    if not isinstance(entities, list):
        return []

    normalized = []
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        text = str(entity.get("text", "")).strip()
        if not text:
            continue
        normalized.append({"text": text})
    return normalized
