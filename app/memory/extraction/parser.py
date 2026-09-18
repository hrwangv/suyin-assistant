"""LLM 返回 JSON 解析。"""
import json
from typing import Any


def parse_json(content: str) -> dict[str, Any]:
    content = (content or "").strip()
    if content.startswith("```json"):
        content = content[len("```json"):]
    if content.startswith("```"):
        content = content[3:]
    if content.endswith("```"):
        content = content[:-3]
    content = content.strip()
    return json.loads(content)
