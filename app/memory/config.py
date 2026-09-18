"""Agent Memory 配置中心。

所有关键参数集中在这里，避免散落在业务代码中。
L1 Redis/MySQL 的基础连接复用 app.conf.memory_config。
"""
import os

from dotenv import load_dotenv

from app.conf.memory_config import MYSQL_URL, REDIS_URL

load_dotenv()


# L1 基础连接（复用 app.conf.memory_config）
MYSQL_URL = MYSQL_URL
REDIS_URL = REDIS_URL

# L2 向量集合名称
# - agent_memory：长期记忆
# - agent_entity：实体辅助索引
MEMORY_QDRANT_COLLECTION = os.getenv("MEMORY_QDRANT_COLLECTION", "agent_memory")
ENTITY_QDRANT_COLLECTION = os.getenv("ENTITY_QDRANT_COLLECTION", "agent_entity")

# Redis 最近消息上下文缓存 TTL（秒）
# 用于 mem:ctx:{scope}:msgs 和 summary。
REDIS_CONTEXT_TTL = int(os.getenv("REDIS_CONTEXT_TTL", "86400"))
# Redis 任务状态缓存 TTL（秒）
# 用于 mem:ctx:{scope}:task。
REDIS_TASK_TTL = int(os.getenv("REDIS_TASK_TTL", "7200"))
# Redis 实体上下文缓存 TTL（秒）
# 用于 mem:ctx:{scope}:entities。
REDIS_ENTITY_TTL = int(os.getenv("REDIS_ENTITY_TTL", "7200"))

# ------------------------------------------------------------------ #
# 短期记忆的三个「条数」配置，按存储层级区分，别再搞混：
#
#   读取层   CONTEXT_READ_LIMIT     每轮读取多少条作为上下文（app.conf.memory_config）
#   缓存层   CONTEXT_CACHE_CAPACITY Redis 里最多保留多少条（本文件）
#   兜底层   CONTEXT_MYSQL_KEEP     MySQL 里最多保留多少条（本文件）
#
# 关系：容量 ≥ 读取上限时，缓存才可能一次命中；MySQL 的保留条数
# 建议与缓存容量对齐，这样 Redis 失效回源重建才是无损的。
# 三者最终都要受 app.conf.memory_config.MAX_TOKEN_WINDOW_SIZE 的 token 预算约束。
# ------------------------------------------------------------------ #

# Redis 中最多保留的最近消息条数（缓存裁剪长度）。
# 同时也作为「Redis 未命中时回源 MySQL 读取多少条」的上限。
CONTEXT_CACHE_CAPACITY = int(os.getenv("CONTEXT_CACHE_CAPACITY", "50"))

# MySQL 兜底表 memory_recent_messages 每个 scope 最多保留多少条。
# 建议与 CONTEXT_CACHE_CAPACITY 保持一致：Redis 失效（过期/重启/写入失败）时会回源 MySQL
# 并重建缓存，两者相等才能保证回源是无损的；比它小就会在回源时丢掉一部分消息。
CONTEXT_MYSQL_KEEP = int(os.getenv("CONTEXT_MYSQL_KEEP", "50"))
# 长期记忆抽取时，附带的「前导上下文」消息条数。
# 批次有 40 条时它自带多轮语境，这里的前导只是让批次边界的衔接更自然，
# 所以不需要很多条；由 extraction_trigger 从真实会话 scope 读取后传给抽取器。
EXTRACTION_CONTEXT_MESSAGES = int(os.getenv("EXTRACTION_CONTEXT_MESSAGES", "10"))

# 长期记忆抽取时，送给模型的「历史上下文」token 预算（在条数限制之外再加一道）。
# 注意抽取用的 system prompt 本身就有约 13k token，这里的预算只针对动态内容；
# 超额时从最老的一条开始丢。
EXTRACTION_CONTEXT_MAX_TOKENS = int(os.getenv("EXTRACTION_CONTEXT_MAX_TOKENS", "4000"))
# 抽取时作为去重参考的「已有记忆」token 预算（超额丢相关度最低的尾部）。
# 这道限制防的是自我放大：已有记忆本身会随对话不断变长，不加控制会把输入撑爆。
EXTRACTION_EXISTING_MAX_TOKENS = int(os.getenv("EXTRACTION_EXISTING_MAX_TOKENS", "2000"))
# 用本批新消息去向量库召回多少条「相关已有记忆」作为去重参考。
# 取的是与当前内容最相关的，而不是最近的或随机的。
EXTRACTION_DEDUP_TOP_K = int(os.getenv("EXTRACTION_DEDUP_TOP_K", "10"))
# 拼成召回查询文本时的 token 预算（embedding 接口单条有上限，
# 而一批 40 条消息很容易超过，所以截取批次里较新的部分）。
EXTRACTION_RECALL_QUERY_MAX_TOKENS = int(
    os.getenv("EXTRACTION_RECALL_QUERY_MAX_TOKENS", "1500")
)

# ---- 长期记忆抽取的触发控制 ----
# 距上次抽取「新增」多少条消息才触发一次抽取。
# 为什么不是每轮都抽：长期记忆的消费场景是跨对话，当前对话由短期窗口覆盖；
# 每轮都抽既浪费大模型调用，抽出来的也多是碎片事实。
# 这个值必须小于 CONTEXT_MYSQL_KEEP（默认 50）：抽取是异步的，
# 触发到真正读取之间还会写入几条消息，留不出余量就会丢掉最老的那批。
EXTRACT_TRIGGER_NEW_MESSAGES = int(os.getenv("EXTRACT_TRIGGER_NEW_MESSAGES", "40"))
# 最小批量：至少一问一答才值得抽（只有提问没回答，说明那一轮失败了）
EXTRACT_MIN_BATCH = int(os.getenv("EXTRACT_MIN_BATCH", "2"))
# 单次批处理上限（水位滞后太多时分批处理，避免一次喂进去太多消息）
EXTRACT_BATCH_LIMIT = int(os.getenv("EXTRACT_BATCH_LIMIT", "60"))
# 抽取水位的 TTL（秒）。水位丢失的后果是「重复抽取」而不是「漏抽」，
# 重复内容会被 hash 去重挡掉，所以给个较长的过期时间即可，不必永久保存。
EXTRACT_WATERMARK_TTL = int(os.getenv("EXTRACT_WATERMARK_TTL", str(7 * 24 * 60 * 60)))

# Consolidation 分布式锁 TTL（秒）
# 避免多个 worker 同时巩固同一 scope。
CONSOLIDATION_LOCK_TTL = int(os.getenv("CONSOLIDATION_LOCK_TTL", "300"))

# 生命周期阈值
# 强度低于该值时归档为 ARCHIVE。
ARCHIVE_STRENGTH_THRESHOLD = float(
    os.getenv("ARCHIVE_STRENGTH_THRESHOLD", "0.1")
)
# 强度低于该值但未达到归档阈值时标记为 WEAK。
WEAK_STRENGTH_THRESHOLD = float(os.getenv("WEAK_STRENGTH_THRESHOLD", "0.3"))
# 访问次数在强度计算中的增益系数。
# 越大，访问次数对记忆强度的影响越明显。
DECAY_ALPHA = float(os.getenv("DECAY_ALPHA", "0.1"))

# 实体检索相似度阈值
# 查询实体与已有实体相似度低于该值时不参与 Entity Boost。
ENTITY_SEARCH_THRESHOLD = float(os.getenv("ENTITY_SEARCH_THRESHOLD", "0.5"))
# 实体命中的最大加分（boost = 相似度 × 本系数 × 稀释度）。
# 这个系数决定「实体信号最多能把一条记忆提前多少」——
# 换算成 dense 余弦相似度的尺度：一条 dense 0.60 + 满额加分的记忆，
#   系数 0.5 → 1.10，能压过 dense 0.90 的高相关记忆（实体说啥是啥，偏强）
#   系数 0.2 → 0.80，能压过 dense 0.75 的无关记忆，但压不过 0.90 的高相关记忆（推荐）
# 实测 dense 相似度范围约 0.35~0.96，boost 要与它同量级，加法才有意义。
ENTITY_BOOST_MAX = float(os.getenv("ENTITY_BOOST_MAX", "0.5"))
# 实体去重阈值
# 语义相似度达到该值时，认为是同一个实体。
ENTITY_DEDUP_THRESHOLD = float(os.getenv("ENTITY_DEDUP_THRESHOLD", "0.95"))
# 查询时最多提取多少个实体。
ENTITY_MAX_QUERY = int(os.getenv("ENTITY_MAX_QUERY", "8"))

# 长期记忆粗召回阶段的最大候选数量。
SEMANTIC_INTERNAL_LIMIT = int(os.getenv("SEMANTIC_INTERNAL_LIMIT", "60"))
# 长期记忆最终返回条数默认值。
DEFAULT_TOP_K = int(os.getenv("DEFAULT_TOP_K", "10"))

# 是否在查询流程中自动抽取长期记忆
AUTO_LONG_TERM_MEMORY_ENABLED = os.getenv(
    "AUTO_LONG_TERM_MEMORY_ENABLED",
    "True",
).lower() in {"1", "true", "yes", "on"}

# 当前查询流程还没有真实用户体系，先用这个默认 user_id。
# 如果为空，则只使用 run_id=session_id 做会话级隔离。
MEMORY_DEFAULT_USER_ID = os.getenv("MEMORY_DEFAULT_USER_ID") or None
