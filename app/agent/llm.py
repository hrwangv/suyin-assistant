"""Agent 里与大模型打交道的两个薄封装。

为什么不用 LangChain 的 with_structured_output / tool calling：
项目里的模型走的是 OpenAI 兼容网关（DeepSeek / 千问），function calling
的支持程度不一致；而现有链路（Query Analyzer）已经验证了
「json_mode + 提示词约束 + Pydantic 校验」这条路，Agent 沿用同一套，
保持调用口径一致、失败时可降级。
"""
import json
from typing import Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

from app.agent.config_helpers import agent_model_name
from app.core.logger import logger
from app.core.tracing import llm_config
from app.llm.lm_utils import ModelType, get_llm_client

T = TypeVar("T", bound=BaseModel)


def _strip_code_fence(content: str) -> str:
    """去掉模型偶尔带上的 ```json 包裹。"""
    text = (content or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1] if "\n" in text else text
        if text.endswith("```"):
            text = text[:-3]
        text = text.replace("```json", "", 1).strip()
    return text


def json_completion(
    schema: Type[T],
    prompt: str,
    model: Optional[str] = None,
    fallback: Optional[T] = None,
    tag: str = "agent",
) -> Optional[T]:
    """调用模型并把返回解析成 Pydantic 模型。

    :param schema:   目标结构
    :param prompt:   已经渲染好的提示词
    :param fallback: 解析失败时的返回值（默认 None，由调用方决定降级策略）
    :param tag:      日志标识，用来区分是哪个节点解析失败
    """
    client = get_llm_client(
        model_type=ModelType.LLM,
        model=model or agent_model_name(),
        json_mode=True,
    )
    try:
        # config 里挂 Langfuse callback：未启用追踪时是空 dict，行为与之前完全一致
        response = client.invoke(prompt, config=llm_config())
    except Exception as exc:
        logger.error(f"[{tag}] 模型调用失败：{exc}")
        return fallback

    # 安全地读取 response 对象的 content 属性
    content = getattr(response, "content", "") or ""
    if isinstance(content, list):  # 少数网关会返回分段内容
        content = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part) for part in content
        )
    text = _strip_code_fence(content)
    try:
        payload = json.loads(text)
    except (ValueError, TypeError) as exc:
        logger.warning(f"[{tag}] 模型返回无法解析成 JSON：{exc}；原始内容={text[:200]}")
        return fallback

    try:
        # Validate a pydantic model instance
        # 返回content的json格式
        return schema.model_validate(payload)
    except ValidationError as exc:
        logger.warning(f"[{tag}] 模型返回不符合 {schema.__name__}：{exc}")
        return fallback


def text_completion(prompt: str, model: Optional[str] = None, tag: str = "agent") -> str:
    """普通文本补全（非流式），失败返回空串。"""
    client = get_llm_client(model_type=ModelType.LLM, model=model or agent_model_name())
    try:
        response = client.invoke(prompt, config=llm_config())
    except Exception as exc:
        logger.error(f"[{tag}] 模型调用失败：{exc}")
        return ""
    content = getattr(response, "content", "") or ""
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part) for part in content
        )
    return content.strip()
