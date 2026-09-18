"""Session Memory 配置。

这里只负责读取环境变量，不建立任何数据库或缓存连接。
这样 memory 模块可以被导入和做单元测试，而不会在导入时触发连接。
"""
import os

from dotenv import load_dotenv

load_dotenv()


MYSQL_URL = os.getenv(
    "MYSQL_URL",
    "mysql+pymysql://root:password@127.0.0.1:3306/suyin-memory?charset=utf8mb4",
)

REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")

# redis最大缓存数量
MAX_CACHED_MESSAGES = int(os.getenv("MAX_CACHED_MESSAGES", "100"))
# redis缓存过期时间，单位秒，默认7天
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", str(7 * 24 * 60 * 60)))

# 每一轮对话「读取」多少条最近消息作为上下文（读上限）。
# 注意：它和缓存里存了多少条无关，缓存容量是 app.memory.config.CONTEXT_CACHE_CAPACITY。
# 实际送进 prompt 的条数还要再受 MAX_TOKEN_WINDOW_SIZE 约束。
CONTEXT_READ_LIMIT = int(os.getenv("CONTEXT_READ_LIMIT", "50"))
MAX_TOKEN_WINDOW_SIZE = int(os.getenv("MAX_TOKEN_WINDOW_SIZE", "8000"))

TOKENIZER_ENCODING = os.getenv("TOKENIZER_ENCODING", "cl100k_base")
