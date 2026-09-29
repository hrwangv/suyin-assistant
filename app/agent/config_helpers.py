"""Agent 内部的小工具（避免各模块重复读配置）。"""
from app.conf.agent_config import agent_config
from app.conf.llm_config import lm_config


def agent_model_name() -> str:
    """Main Agent 使用的模型：AGENT_LLM_MODEL_ID 优先，否则复用项目默认模型。"""
    return agent_config.llm_model or lm_config.llm_model
