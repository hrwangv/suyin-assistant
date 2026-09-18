"""MCP 服务配置。

仅负责从环境变量读取配置，不在模块导入时建立连接。
"""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class McpConfig:
    """各类 MCP 服务的连接配置。"""

    dashscope_base_url: str | None
    dashscope_api_key: str | None
    qcc_api_key: str | None
    yjt_base_url: str | None
    yjt_api_key: str | None


mcp_config = McpConfig(
    dashscope_base_url=os.getenv("MCP_DASHSCOPE_BASE_URL_STREAMABLE"),
    dashscope_api_key=os.getenv("OPENAI_API_KEY"),
    qcc_api_key=os.getenv("QCC_API_KEY"),
    yjt_base_url=os.getenv("YJT_BASE_URL"),
    yjt_api_key=os.getenv("YJT_API_KEY"),
)
