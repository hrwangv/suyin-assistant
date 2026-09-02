"""Qdrant 连接与 collection 配置。"""

import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class QdrantConfig:
    """从环境变量读取的 Qdrant 配置。"""

    url: str | None
    api_key: str | None
    chunks_collection: str
    item_name_collection: str
    dense_dimension: int


qdrant_config = QdrantConfig(
    # 例如：http://127.0.0.1:6333 或 https://<cluster>.cloud.qdrant.io
    url=os.getenv("QDRANT_URL"),
    api_key=os.getenv("QDRANT_API_KEY") or None,
    chunks_collection=os.getenv("QDRANT_CHUNKS_COLLECTION", "kb_chunks"),
    item_name_collection=os.getenv("QDRANT_ITEM_NAME_COLLECTION", "kb_item_name"),
    dense_dimension=int(os.getenv("EMBEDDING_DENSE_DIMENSION", "1024")),
)
