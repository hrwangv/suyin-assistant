"""Memory Coordinator：统一编排 L1/L2/L3 与检索、生命周期。"""
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.core.logger import logger
from app.memory.cache.context_cache import ContextCache
from app.memory.config import (
    DEFAULT_TOP_K,
    ENTITY_DEDUP_THRESHOLD,
    ENTITY_MAX_QUERY,
    ENTITY_SEARCH_THRESHOLD,
    EXTRACTION_CONTEXT_MAX_TOKENS,
    EXTRACTION_DEDUP_TOP_K,
    EXTRACTION_EXISTING_MAX_TOKENS,
    EXTRACTION_RECALL_QUERY_MAX_TOKENS,
    SEMANTIC_INTERNAL_LIMIT,
)
from app.utils.token_budget import fit_token_budget
from app.memory.embeddings import embed_texts
from app.memory.extraction.entity_extractor import extract_entities
from app.memory.extraction.extractor import MemoryExtractor
from app.memory.lifecycle.consolidation import ConsolidationLock, should_consolidate
from app.memory.lifecycle.decay import compute_strength, determine_lifecycle
from app.memory.models import (
    EntityRecord,
    HistoryRecord,
    Memory,
)
from app.memory.retrieval.entity import entity_boost, entity_search
from app.memory.storage.entity_store import EntityStore
from app.memory.storage.history_store import HistoryStore
from app.memory.recent_message_service import (
    RecentMessageService,
    get_recent_message_service,
)
from app.memory.storage.vector_store import MemoryVectorStore
from app.memory.utils.hashing import md5_text
from app.memory.utils.normalization import normalize_entity_key, normalize_text
from app.memory.utils.scope import build_scope, normalize_scope, parse_scope
from app.memory.utils.time import utcnow


class MemoryCoordinator:
    """Agent Memory 系统的统一入口。"""

    def __init__(
        self,
        vector_store: MemoryVectorStore = None,
        entity_store: EntityStore = None,
        history_store: HistoryStore = None,
        recent_service: RecentMessageService = None,
        context_cache: ContextCache = None,
    ):
        self.vector_store = vector_store or MemoryVectorStore()
        self.entity_store = entity_store or EntityStore()
        self.history_store = history_store or HistoryStore()
        self.recent_service = recent_service or get_recent_message_service()
        self.context_cache = context_cache or ContextCache()
        self.extractor = MemoryExtractor()
        self.consolidation_lock = ConsolidationLock()

    # ------------------------------------------------------------ #
    # 基础工具
    # ------------------------------------------------------------ #
    '''
    创建单一的scope
    '''
    def _scope(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> str:
        
        return build_scope(user_id=user_id, agent_id=agent_id, run_id=run_id)

    # 标准化message，将消息转化成role和content 的格式
    def _normalize_messages(self, messages: Any) -> List[dict]:
        if isinstance(messages, str):
            messages = [{"role": "user", "content": messages}]
        elif isinstance(messages, dict):
            messages = [messages]
        elif not isinstance(messages, list):
            raise ValueError("messages 只支持 str / dict / list")

        result = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = (message.get("role") or "user").strip()
            content = (message.get("content") or "").strip()
            if role == "system" or not content:
                continue
            result.append({"role": role, "content": content})
        return result

    # 写历史记录数据库表
    def _write_history(self, record: HistoryRecord) -> None:
        self.history_store.add(record)

    def _associate_entities(
        self,
        memory: Memory,
        scope: str,
    ) -> None:
        """Entity 是辅助索引，失败不能影响主 Memory 写入。"""
        try:
            # 提取记忆的实体。返回[{"text": "...", "type": "..."}]
            entity_items = extract_entities(memory.data)
            if not entity_items:
                return
            # 写入阶段通过大模型总结抽取出的实体列表，每个元素包含 text 和 type。
            entity_names = [item["text"] for item in entity_items]
            # 实体信息向量化
            dense_vectors, sparse_vectors = embed_texts(entity_names)
            # 通过提取到的实体名字，去实体向量数据库里面搜索符合标准的EntityRecord
            # 已经存在于向量数据库里的实体向量
            existing = self.entity_store.get_by_keys( 
                [normalize_entity_key(name) for name in entity_names],
                scope,
            )
            # key: Entity类
            existing_map = {entity.entity_key: entity for entity in existing}

            for index, item in enumerate(entity_items):
                name = item["text"] # 提取到的实体名
                key = normalize_entity_key(name) # 清理后的实体名称用作存向量的key
                if key in existing_map: # 如果存在于已经存在实体向量里的
                    # 若归一化键已存在，直接获取实体记录，追加当前 memory.id（防重复）。
                    entity = existing_map[key] # 通过相同的key找到实体类
                    if memory.id not in entity.linked_memory_ids: # 如果当前记忆id没通过实体类建立连接
                        entity.linked_memory_ids.append(memory.id) # 通过实体类建立连接
                else:
                    # 精确匹配没命中，意味着当前提取的实体不在已存在中
                    # 做语义匹配搜索
                    semantic_matches = self.entity_store.search_semantic(
                        dense_vectors[index], 
                        scope,
                        limit=1,
                        threshold=ENTITY_DEDUP_THRESHOLD, # 控制相似度，这里需要比较高
                    )
                    if semantic_matches: # 语义匹配上了
                        entity = semantic_matches[0][0] # 语义匹配成功，实体类赋值
                        if memory.id not in entity.linked_memory_ids:
                            entity.linked_memory_ids.append(memory.id) # 通过以及关联
                    else: # 语义匹配也没匹配上，新建实体
                        entity = EntityRecord(
                            id=str(uuid.uuid4()),
                            data=name,
                            linked_memory_ids=[memory.id],
                            user_id=memory.user_id,
                            agent_id=memory.agent_id,
                            run_id=memory.run_id,
                            entity_key=key,
                        )
                # 实体插入向量数据库
                self.entity_store.upsert(
                    entity,
                    dense_vectors[index],
                    sparse_vectors[index],
                    scope,
                )
        except Exception as exc:
            logger.warning(f"Entity 关联失败，忽略：{exc}")

    def _recall_related_memories(self, scope: str, messages: List[dict]) -> List[dict]:
        """用这批新消息去向量库里召回最相关的已有记忆（供 LLM 判断重复与关联）。

        思路：
          1. 把批次拼成一段查询文本
          2. 生成稠密向量
          3. 在 agent_memory 里按 scope 检索 top_k 条

        为什么要限制查询文本长度：embedding 接口单条有 token 上限，
        而批次可能是 40 条消息（中文轻松过万 token）。这里按预算截取，
        保留批次里较新的内容（后面的消息通常更能代表当前话题）。

        失败时返回空列表 —— 去重参考只是"锦上添花"，不能因为召回失败就中断整次抽取。
        """
        if not messages:
            return []
        try:
            query_messages, _ = fit_token_budget(
                messages,
                EXTRACTION_RECALL_QUERY_MAX_TOKENS,
                # 按「拼接后的真实形态」计费：每条后面会接一个换行，
                # 不计进去的话最终文本会略微超预算（分词边界也会吃 token）
                text_of=lambda m: (m.get("content") or "") + "\n",
                drop="oldest",
                # 至少留一条：批次里可能单条就超过预算（比如一条很长的回答），
                # 不留这一条的话整批会被丢光，查询文本变空、召回直接失效。
                keep_one=True,
            )
            query_text = "\n".join(
                (m.get("content") or "").strip() for m in query_messages
            ).strip()
            if not query_text:
                return []
            dense_vectors, _ = embed_texts([query_text])
            hits = self.vector_store.search(
                dense_vectors[0], scope, EXTRACTION_DEDUP_TOP_K
            )
            return [{"id": memory.id, "text": memory.data} for memory, _ in hits]
        except Exception as exc:
            logger.warning(f"召回已有相关记忆失败，本次不带去重参考：{exc}")
            return []

    # ------------------------------------------------------------ #
    # add
    # ------------------------------------------------------------ #
    def add(
        self,
        messages,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        infer: bool = True,
        context: Optional[List[dict]] = None,
    ) -> List[Memory]:
        """新增记忆。

        :param context: 抽取时附带的「前导上下文」消息（可选）。
            由**调用方**提供，而不是这里自己去读短期记忆 —— 因为这里拿到的是
            长期记忆的 scope（user:xxx），而真实会话消息存在 run:{session_id} 下，
            自己读会读错来源（曾经因此把别的对话的内容当成"最近对话"混进抽取）。
            谁需要上下文，谁从真实会话存储读好了传进来。
        """
        scope = self._scope(user_id, agent_id, run_id)
        parsed_messages = self._normalize_messages(messages)
        if not parsed_messages:
            raise ValueError("没有可写入的消息")

        metadata = metadata or {}
        now = utcnow()

        if not infer:
            memories = self._add_raw(parsed_messages, scope, user_id, agent_id, run_id, metadata, now)
        else:
            memories = self._add_with_extraction(
                parsed_messages, scope, user_id, agent_id, run_id, metadata, now,
                context=context,
            )
        # 注意：这里不再把消息回写到短期记忆表。
        # 早期会写一份"影子副本"到长期记忆的 scope 下，导致同一份对话存两份、
        # 还会被抽取当成"最近对话"读到（跨对话串味）。短期记忆只由会话链路负责写入。
        return memories


# 非提取模式，不调用大模型总结，直接存
    def _add_raw(
        self,
        messages: List[dict],
        scope: str,
        user_id: Optional[str],
        agent_id: Optional[str],
        run_id: Optional[str],
        metadata: Dict[str, Any],
        now: datetime,
    ) -> List[Memory]:
        texts = [m["content"] for m in messages]
        dense_vectors, sparse_vectors = embed_texts(texts)
        memories = []

        for index, message in enumerate(messages):
            memory = Memory(
                id=str(uuid.uuid4()),
                data=message["content"],
                hash=md5_text(message["content"]),
                text_lemmatized=normalize_text(message["content"]),
                created_at=now,
                updated_at=now,
                user_id=user_id,
                agent_id=agent_id,
                run_id=run_id,
                role=message.get("role"),
                attributed_to=message.get("role", "user"),
                metadata=metadata,
                importance=0.5,
                strength_score=0.5,
                last_accessed_at=now,
                access_count=0,
                lifecycle_state="ACTIVE",
                scope=scope,
            )
            # 插入向量数据库
            self.vector_store.upsert(
                memory,
                dense_vectors[index],
                sparse_vectors[index],
                scope,
            )
            # 写入历史记录表
            self._write_history(
                HistoryRecord(
                    memory_id=memory.id,
                    scope=scope,
                    event="ADD",
                    new_memory=memory.data,
                )
            )
            # 将记忆写入实体向量数据库
            self._associate_entities(memory, scope)
            memories.append(memory)

        return memories

    def _add_with_extraction(
        self,
        messages: List[dict],
        scope: str,
        user_id: Optional[str],
        agent_id: Optional[str],
        run_id: Optional[str],
        metadata: Dict[str, Any],
        now: datetime,
        context: Optional[List[dict]] = None,
    ) -> List[Memory]:
        # Phase 0：前导上下文。
        # 由调用方传入（trigger 从真实会话的 run scope 读），这里不再自己去读短期记忆 ——
        # 本函数的 scope 是长期记忆的 user:xxx，用它去读会读到别的来源（曾经读到影子副本，
        # 导致跨对话串味）。批次本身已含多轮语境，这里的前导只是衔接批次边界。
        context = self._normalize_messages(context or [])

        # Phase 1：召回「与这批新消息相关」的已有记忆，作为去重参考。
        #
        # 用向量检索而不是全量遍历：全量遍历既拿不全（scroll 默认只返回一页），
        # 顺序也不确定，参考集可能是跟当前内容毫不相干的几条。
        # 改成"用本批内容去检索最相关的 top_k 条"，给模型看的才是真正需要比对的对象。
        dedup_reference = self._recall_related_memories(scope, messages)

        # 这里原先还会 scroll_active(scope) 全量拉取本 scope 的所有 ACTIVE 记忆，
        # 唯一用途是给 md5 精确去重提供 hash 集合。已移除，原因：
        # 1. 成本与收益倒挂：全量翻页 + 逐条构造 Memory + 解析 payload 的代价
        #    随记忆总量线性增长，挡住的却只有"文本一字不差"这一种重复
        #    （模型改写一个词就绕过去了）；
        # 2. 去重责任已经前移到模型侧——本函数用 dedup_reference 把「最相关的已有记忆」
        #    喂给模型，并要求它避免重复、给相关记忆做关联。
        # 如果以后确实需要硬去重，正确做法是复用下面本来就要算的向量做相似度判定
        # （先例见 app/memory/config.py 里实体库的 ENTITY_DEDUP_THRESHOLD），
        # 而不是再引入一次全量扫描。

        # Phase 1.5：送进模型前做 token 预算。
        # 条数限制（10 条 / 20 条）挡不住"单条很长"的情况，这里按 token 再兜一道：
        # - 历史上下文：超额从最老的一条开始丢，保留最近的对话
        # 
        context, dropped_context = fit_token_budget(
            context, EXTRACTION_CONTEXT_MAX_TOKENS, drop="oldest"
        )
        if dropped_context:
            logger.info(f"抽取长期记忆：历史上下文超过 token 预算，丢弃最老的 {dropped_context} 条")
        # - 去重参考：超额丢尾部（少几条不影响主流程）
        dedup_reference, dropped_reference = fit_token_budget(
            dedup_reference, EXTRACTION_EXISTING_MAX_TOKENS,
            text_of=lambda item: item.get("text", ""), drop="last",
        )
        if dropped_reference:
            logger.info(f"抽取长期记忆：去重参考超过 token 预算，丢弃 {dropped_reference} 条")

        # Phase 2/2.5：LLM 抽取与重要性评分。
        extraction = self.extractor.extract(
            new_messages=messages, # 最新消息
            existing_memories=dedup_reference, # 给模型的去重参考（不是全部长期记忆）
            last_messages=context, # 前导上下文
        )
        extracted_memories = extraction.get("memory", [])

        accepted: List[dict] = [] # 准备入库的记忆值

        for item in extracted_memories:
            text = (item.get("text") or "").strip() # 文本清理，非空校验
            if not text:
                continue
            accepted.append(    # 当前记忆添加到入库记忆 
                {
                    "text": text,
                    # 当前阶段不做 LLM 重要性评分，先保证已抽取记忆全部入库，默认都设为0.5。
                    "importance": 0.5,
                    "attributed_to": item.get("attributed_to", "user"),
                }
            )

        if not accepted:
            return []

        # 总结出的记忆向量化
        texts = [item["text"] for item in accepted]
        dense_vectors, sparse_vectors = embed_texts(texts)
        memories = []

        
        for index, item in enumerate(accepted):
            memory = Memory(
                id=str(uuid.uuid4()),
                data=item["text"],
                hash=md5_text(item["text"]),
                text_lemmatized=normalize_text(item["text"]),
                created_at=now,
                updated_at=now,
                user_id=user_id,
                agent_id=agent_id,
                run_id=run_id,
                attributed_to=item.get("attributed_to"),
                metadata=metadata,
                # 统一初始重要性为 0.5，后续由时间、访问等信号动态衰减。
                importance=0.5,
                strength_score=0.5,
                last_accessed_at=now,
                access_count=0,
                lifecycle_state="ACTIVE",
                scope=scope,
            )
            # 存向量数据库
            self.vector_store.upsert(
                memory,
                dense_vectors[index],
                sparse_vectors[index],
                scope,
            )
            # # 写入历史记录表
            self._write_history(
                HistoryRecord(
                    memory_id=memory.id,
                    scope=scope,
                    event="ADD",
                    new_memory=memory.data,
                )
            )
            # 实体关联表
            self._associate_entities(memory, scope)
            memories.append(memory)

        return memories

    # ------------------------------------------------------------ #
    # search
    # ------------------------------------------------------------ #
    def search(
        self,
        query: str,
        scope: str,
        top_k: int = DEFAULT_TOP_K,
        rerank: bool = False,
        explain: bool = False,
    ) -> dict:
        """长期记忆检索（只做长期，不碰短期记忆）。

        早期这里会顺带读一次短期上下文返回给调用方，但：
        1. 主链路（回答节点）只消费 long_term_memories，那份短期数据直接丢弃；
        2. 更糟的是 scope 语义不同 —— 短期消息写在 run:{session_id} 下，
           而这里收到的是长期记忆的 user:{user_id} scope，读出来恒为空。

        所以短期记忆统一由需要它的地方自己读（例如 Query Analyzer 节点读一次、
        存进 state["history"] 全链路复用），这里专注长期记忆。

        **rerank 默认关闭**，理由：
        - 长期记忆在 prompt 里是"背景补充"，只取 top 几条，重排能改变的余地很小；
        - rerank 是一次额外的外部 API 调用（每次提问都要付成本 + 延迟）；
        - 排序已经由「稠密相似度 + 实体加分」决定（0.35~0.96 与 0~ENTITY_BOOST_MAX 同量级），
          不需要再叠一层精排。
        需要时可以由调用方显式传 rerank=True 打开（HTTP 接口 /memory/search 也支持）。
        """
        scope = normalize_scope(scope)
        query = (query or "").strip()
        if not query:
            raise ValueError("query 不能为空")

        # 长期记忆混合检索
        dense_vectors, sparse_vectors = embed_texts([query])
        # 稠密向量检索（不再走 dense+sparse 的 RRF 混合）。
        # 为什么不用 RRF：RRF 会丢掉原始分数、只保留排名，把基础分压到 0.008~0.033，
        # 而 entity_boost 是 0~0.5 的绝对加分 —— 两者差一个数量级，
        # "相似度 + 加分"这种 mem0 式的加法语义就失效了。
        # 现在统一用余弦相似度（实测 0.35~0.96），与 boost 同量级，相加才有意义。
        dense_hits = self.vector_store.search(
            dense_vectors[0],
            scope,
            SEMANTIC_INTERNAL_LIMIT,  # 粗召回候选数60
        )
        entity_by_id = {}
        entity_entities = []
        entity_similarities = {}

        # Entity 召回与 Boost。
        # 查询阶段需要先抽 query 实体，再逐个去 Entity Store 匹配。
        try:
            # 提取要查询问题中的实体
            query_entities = extract_entities(query, max_entities=ENTITY_MAX_QUERY)
            # 
            entity_texts = [item["text"] for item in query_entities]
            # 提取出的实体文本向量化，只要稠密向量
            entity_dense_vectors, _ = embed_texts(entity_texts)
            for index, item in enumerate(query_entities):
                # 提取出原问题的实体文本向量，去实体向量数据库里搜索，返回一个列表，列表里是元组（EntityRecord, 相似度）
                # 获取到hit元组列表（EntityRecord, 相似度分数）
                hits = entity_search(
                    self.entity_store,
                    entity_dense_vectors[index],
                    scope,
                    ENTITY_SEARCH_THRESHOLD, # 相似度得分阈值设置，目前默认0.5
                    limit=2, # 每个查询实体最多链接到库内实体的数量
                )
                for entity, similarity in hits:
                    entity_by_id[entity.id] = entity # 键是entity.id，值是entity对象
                    entity_similarities[entity.id] = max( # 键是entity.id，值是相似度分数
                        entity_similarities.get(entity.id, 0.0),
                        similarity, # 实体存储向量和搜索的之间的相似度值
                    )
            entity_entities = list(entity_by_id.values()) # 获取entity
        except Exception as exc:
            logger.warning(f"查询阶段实体抽取失败，跳过 Entity Boost：{exc}")
            entity_similarities = {}
            entity_entities = []
        # 实体加成分数
        # 返回一个字典 boosts，
        # 键为 memory.id，值为该条记忆因实体匹配获得的额外浮点数分数（通常是 0~1 之间的增益）。
        boosts = entity_boost(entity_similarities, entity_entities)

        candidates = {}
        for memory, dense_score in dense_hits:
            boost = boosts.get(memory.id, 0.0)
            # 直接相加：dense_score 是余弦相似度。最大值1
            # boost 是实体加分（0~ENTITY_BOOST_MAX），两者同量级，加法有意义。最大值0.5
            # 这也是 mem0 的设计：相似度 + 额外加分，而不是先做排名融合。
            combined = dense_score + boost
            candidates[memory.id] = {
                "memory": memory,
                "dense_score": dense_score,
                "entity_boost": boost,
                "combined_score": combined,
            }

        # 按照组合分由高到低排序，取前topk
        ranked = sorted(
            candidates.values(),
            key=lambda item: item["combined_score"],
            reverse=True, 
        )[:top_k]

        if rerank and ranked:
            try:
                from app.memory.retrieval.reranker import rerank as rerank_fn

                # 重排序之后的列表
                reranked = rerank_fn(
                    query,
                    [
                        {
                            "memory_id": item["memory"].id,
                            "data": item["memory"].data,
                        }
                        for item in ranked
                    ],
                    top_k,
                )
                ranked = []
                for item in reranked:
                    memory_id = item.get("memory_id")
                    if memory_id not in candidates:
                        continue
                    candidate = dict(candidates[memory_id])
                    # 获取重排序之后的分数
                    rerank_score = item.get("rerank_score", 0.0)
                    candidate["rerank_score"] = rerank_score
                    # 精排后的最终分数使用 rerank_score，避免旧粗排分与新顺序不一致。
                    candidate["combined_score"] = rerank_score
                    ranked.append(candidate)
            except Exception as exc:
                logger.warning(f"Reranker 失败，返回混合检索原结果：{exc}")

        self._record_access_and_backfill(scope, ranked)
        long_term_results = self._format_search_result(ranked, explain)
        return {"long_term_memories": long_term_results}

    def _record_access_and_backfill(
        self,
        scope: str,
        ranked: List[dict],
    ) -> None:
        for item in ranked:
            memory = item["memory"]
            # 访问次数+1 封顶100
            access_count = min(memory.access_count + 1, 100)
            try:
                # 更新记忆
                self.vector_store.update_stats(
                    memory.id,
                    scope,
                    access_count,
                    utcnow(),
                )
            except Exception as exc:
                logger.warning(f"访问统计更新失败，忽略：{exc}")

    def _format_search_result(
        self,
        ranked: List[dict],
        explain: bool,
    ) -> List[dict]:
        results = []
        for item in ranked:
            memory = item["memory"]
            result = {
                "memory_id": memory.id,
                "data": memory.data,
                "importance": memory.importance,
                "strength_score": memory.strength_score,
                "access_count": memory.access_count,
            }
            if explain:
                result.update(
                    {
                        "dense_score": item["dense_score"],
                        "entity_boost": item["entity_boost"],
                        "combined_score": item["combined_score"],
                    }
                )
                if "rerank_score" in item:
                    result["rerank_score"] = item["rerank_score"]
            results.append(result)
        return results

    # ------------------------------------------------------------ #
    # get / update / delete / history
    # ------------------------------------------------------------ #
    def get(self, memory_id: str, scope: str) -> Optional[Memory]:
        scope = normalize_scope(scope)
        memory = self.vector_store.get(memory_id)
        if not memory or memory.scope != scope:
            return None
        return memory

    def update(
        self,
        memory_id: str,
        scope: str,
        data: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Memory]:
        scope = normalize_scope(scope)
        old = self.get(memory_id, scope)
        if not old:
            return None

        now = utcnow()
        new_text = (data or old.data).strip()
        if not new_text:
            raise ValueError("data 不能为空")

        new_metadata = dict(old.metadata)
        if metadata is not None:
            new_metadata.update(metadata)

        dense_vectors, sparse_vectors = embed_texts([new_text])
        new_memory = Memory(
            id=old.id,
            data=new_text,
            hash=md5_text(new_text),
            text_lemmatized=normalize_text(new_text),
            created_at=old.created_at,
            updated_at=now,
            user_id=old.user_id,
            agent_id=old.agent_id,
            run_id=old.run_id,
            role=old.role,
            actor_id=old.actor_id,
            attributed_to=old.attributed_to,
            metadata=new_metadata,
            importance=old.importance,
            strength_score=old.strength_score,
            last_accessed_at=old.last_accessed_at,
            access_count=old.access_count,
            expiration_date=old.expiration_date,
            lifecycle_state=old.lifecycle_state,
            scope=old.scope,
        )
        self.vector_store.upsert(
            new_memory,
            dense_vectors[0],
            sparse_vectors[0],
            scope,
        )

        self._write_history(
            HistoryRecord(
                memory_id=memory_id,
                scope=scope,
                event="UPDATE",
                old_memory=old.data,
                new_memory=new_text,
            )
        )
        return new_memory

    def delete(self, memory_id: str, scope: str) -> bool:
        scope = normalize_scope(scope)
        memory = self.get(memory_id, scope)
        if not memory:
            return False

        self.vector_store.delete(memory_id)

        self._write_history(
            HistoryRecord(
                memory_id=memory_id,
                scope=scope,
                event="DELETE",
                old_memory=memory.data,
                new_memory=None,
                is_deleted=1,
            )
        )
        return True

    def history(self, memory_id: str, scope: str) -> List[HistoryRecord]:
        return self.history_store.history(memory_id, normalize_scope(scope))

    # ------------------------------------------------------------ #
    # Lifecycle / Consolidation
    # ------------------------------------------------------------ #
    def run_lifecycle(self, scope: str) -> dict:
        scope = normalize_scope(scope)
        active_memories = self.vector_store.scroll_active(scope)
        updated_count = 0

        for memory in active_memories:
            # 计算时间衰减度
            strength = compute_strength(
                memory.importance,
                memory.last_accessed_at,
                memory.access_count,
            )
            expired = bool(
                memory.expiration_date and memory.expiration_date < utcnow()
            )
            state = determine_lifecycle(strength, expired)
            if state != memory.lifecycle_state:
                memory.lifecycle_state = state
                dense_vectors, sparse_vectors = embed_texts([memory.data])
                self.vector_store.upsert(
                    memory,
                    dense_vectors[0],
                    sparse_vectors[0],
                    scope,
                )
                updated_count += 1

        return {"updated": updated_count}

    def consolidate(self, scope: str) -> int:
        scope = normalize_scope(scope)
        context = self.recent_service.get_messages(scope, 100)
        if not context:
            return 0

        if not should_consolidate(len(context), None):
            return 0

        if not self.consolidation_lock.acquire(scope):
            return 0

        try:
            parts = parse_scope(scope)
            memories = self.add(context[-20:], infer=True, **parts)
            return len(memories)
        finally:
            self.consolidation_lock.release(scope)


_coordinator: Optional[MemoryCoordinator] = None


def get_memory_coordinator() -> MemoryCoordinator:
    """全局懒加载 MemoryCoordinator 单例。"""
    global _coordinator
    if _coordinator is None:
        _coordinator = MemoryCoordinator()
    return _coordinator
