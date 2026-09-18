"""item_name（商品/主体名称）识别配置。

仅负责从环境变量读取配置，不在模块导入时调用大模型。
"""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class ItemNameConfig:
    """大模型识别 item_name 时构造上下文的参数。"""

    # 是否启用大模型主体识别（item_name 提取）。
    # 关闭后：导入阶段不再调用大模型，也不再写 kb_item_name 向量库，
    # 直接用文件名兜底，保证「按 item_name 幂等删除/写入」这条链路不受影响。
    enabled: bool
    # 大模型识别商品名称的上下文切片数：取前5个切片，避免上下文过长导致大模型输入超限
    item_name_chunk_k: int
    # 单个切片内容截断长度：防止单切片内容过长，占满大模型上下文
    single_chunk_content_max_len: int
    # 大模型上下文总字符数上限：适配主流大模型输入限制，默认2500
    context_total_max_chars: int


item_name_config = ItemNameConfig(
    # 默认关闭：经营晨报这类一份文档多主体的资料，文档级 item_name 意义不大，
    # 开着只会平白多一次大模型调用 + 一次向量写库
    enabled=os.getenv("ITEM_NAME_RECOGNITION_ENABLED", "false").strip().lower()
    in ("1", "true", "yes", "on"),
    item_name_chunk_k=int(os.getenv("ITEM_NAME_CHUNK_K", "5")),
    single_chunk_content_max_len=int(os.getenv("ITEM_NAME_SINGLE_CHUNK_MAX_LEN", "800")),
    context_total_max_chars=int(os.getenv("ITEM_NAME_CONTEXT_TOTAL_MAX_CHARS", "2500")),
)
