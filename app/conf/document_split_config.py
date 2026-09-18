"""文档切分配置。

仅负责从环境变量读取配置，不在模块导入时执行切分逻辑。
"""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class DocumentSplitConfig:
    """文档切分相关阈值。"""

    # 单个Chunk最大字符长度（不是token）：超过则触发二次切分（适配大模型上下文窗口）
    max_content_length: int
    # 短Chunk合并阈值：同父标题的短Chunk会被合并，减少碎片化
    min_content_length: int


document_split_config = DocumentSplitConfig(
    max_content_length=int(os.getenv("DOC_SPLIT_MAX_CONTENT_LENGTH", "2000")),
    min_content_length=int(os.getenv("DOC_SPLIT_MIN_CONTENT_LENGTH", "500")),
)
