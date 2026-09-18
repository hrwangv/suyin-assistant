"""长期记忆抽取的触发控制（水位 + 阈值 + 锁）。

为什么要有这一层：早期是「每轮问答都抽一次」，问题有两个——
1. 浪费：长期记忆的消费场景是跨对话，当前对话由短期窗口覆盖，每轮抽没有收益；
2. 质量差：一次只喂 [本轮问题 + 本轮回答]，模型只能提炼出碎片事实，
   看不到跨轮的来龙去脉。

改成「按水位批量抽取」后：攒够阈值才抽一次，输入是一批消息（含多轮上下文），
成本降到约 1/N，抽出来的记忆也更完整。

三个关键机制：
- **水位**：记录「已抽取到哪条消息 id」，保证每条消息只被抽一次；
- **阈值**：距上次抽取新增 ≥ EXTRACT_TRIGGER_NEW_MESSAGES 才触发（省钱）；
- **锁**：防止多个触发点并发跑同一批（防重复）。

水位只在**抽取成功后**推进：失败时停在原地，下次触发会自动重试，
重复内容由 _add_with_extraction 里的 hash 去重兜底。
"""
from __future__ import annotations

import threading
from typing import List, Optional

from app.core.logger import logger
from app.memory.cache.context_cache import ContextCache
from app.memory.config import (
    EXTRACT_BATCH_LIMIT,
    EXTRACT_MIN_BATCH,
    EXTRACT_TRIGGER_NEW_MESSAGES,
    EXTRACTION_CONTEXT_MESSAGES,
)
from app.memory.lifecycle.consolidation import ConsolidationLock
from app.memory.storage.recent_message_store import RecentMessageStore
from app.memory.utils.scope import parse_scope


# 懒加载单例：模块导入时不建立数据库/Redis 连接（方便测试和离线导入）
_store: Optional[RecentMessageStore] = None
_cache: Optional[ContextCache] = None
_lock: Optional[ConsolidationLock] = None


def _get_store() -> RecentMessageStore:
    global _store
    if _store is None:
        _store = RecentMessageStore()
    return _store


def _get_cache() -> ContextCache:
    global _cache
    if _cache is None:
        _cache = ContextCache()
    return _cache


def _get_lock() -> ConsolidationLock:
    global _lock
    if _lock is None:
        _lock = ConsolidationLock()
    return _lock


def _to_messages(rows) -> List[dict]:
    """把消息记录转成抽取器需要的格式。"""
    return [
        {"role": row.role, "content": row.content, "name": row.name}
        for row in rows
    ]


def maybe_trigger_extraction(
    run_scope: str,
    long_term_scope: str,
    force: bool = False,
) -> bool:
    """检查水位，够条件才触发一次长期记忆抽取。

    :param run_scope:       会话级 scope（如 run:{session_id}）——水位与批次都以它为维度
    :param long_term_scope: 长期记忆归属的 scope（如 user:{user_id}）——抽取结果写这里
    :param force:           True 时忽略「新增条数」阈值（用于「新建对话」时给上个会话收尾），
                            但仍然要求残余 ≥ EXTRACT_MIN_BATCH
    :return: 是否真的触发了抽取
    """
    store, cache, lock = _get_store(), _get_cache(), _get_lock()

    # 1. 距上次抽取新增了多少条，
    # redis键是 run_scope ，值是mysql里 id > 水位 的消息数
    watermark = cache.get_extract_watermark(run_scope)
    # 计算「水位之后的消息数」：按 id > 水位取区间，而不是「取最新 N 条」——
    # 从msql中读取，消息条数
    pending = store.count_since(run_scope, watermark)

    # 残余太少（0 条，或只有提问没有回答）→ 不值得调模型
    if pending < EXTRACT_MIN_BATCH:  
        logger.info(f"长期记忆抽取：待抽取仅 {pending} 条（水位={watermark}），跳过")
        return False

    # 没到阈值就不抽 —— 这是「不浪费」的关键
    if not force and pending < EXTRACT_TRIGGER_NEW_MESSAGES: # 小于40条
        logger.info(
            f"长期记忆抽取：新增 {pending} 条 < 阈值 {EXTRACT_TRIGGER_NEW_MESSAGES}，跳过"
        )
        return False

    # 2. 加锁：防多个触发点 / 连续点击并发跑同一批
    if not lock.acquire(long_term_scope):
        logger.info("长期记忆抽取：已有任务在执行，本次跳过")
        return False

    try:
        # 3. 快照这一批：按「id > 水位」取区间，而不是「取最新 N 条」——
        #    否则异步执行期间新增的消息会和下一批重叠、重复抽取
        # rows 就是“从上次抽取位置之后，到本次位置为止”的一段消息区间
        rows = store.get_since(run_scope, watermark, EXTRACT_BATCH_LIMIT)
        # 校验，如果实际取到的条数太少（比如期间消息被删了，或并发下状态变了），就不值得抽。
        if len(rows) < EXTRACT_MIN_BATCH:
            lock.release(long_term_scope) # 释放锁
            return False
        batch = _to_messages(rows)
        # 取这一批最后一条消息的 id，作为本批次的结束水位
        end_id = rows[-1].id
        # 前导上下文：从**真实会话的 run scope** 取批次之前的一小段，
        # 让批次与上一批的边界衔接更自然。
        # 注意不能从长期记忆的 scope 读——那样会读到别的对话的内容（曾经就是这么错的）。
        # 取 watermark + 1 之前的消息：水位那条（上一批的最后一条）紧邻本批，应当包含进来。
        lead_in = (
            _to_messages(store.get_before(run_scope, watermark + 1, EXTRACTION_CONTEXT_MESSAGES))
            if watermark > 0
            else []
        )
        logger.info(
            f"长期记忆抽取：触发一批 {len(batch)} 条（水位 {watermark} → {end_id}）"
            f"，前导上下文 {len(lead_in)} 条"
        )
        # 4. 异步执行，不阻塞本轮问答
        threading.Thread(
            target=_run_extraction,
            args=(long_term_scope, batch, end_id, run_scope, lead_in),
            daemon=True,
        ).start()
        return True
    except Exception:
        # 启动线程之前出错，锁要还回去，否则得等 TTL 过期
        lock.release(long_term_scope)
        raise


def _run_extraction(
    long_term_scope: str,
    batch: List[dict],
    end_id: int,
    run_scope: str,
    context: List[dict] | None = None,
) -> None:
    """在线程里执行抽取；成功后推进水位，失败则不推进（下次重试）。"""
    lock = _get_lock()
    try:
        from app.memory.coordinator import get_memory_coordinator

        # 抽取结果写到长期记忆的 scope（user:xxx），批次来自会话 scope（run:xxx）
        scope_kwargs = {
            key: value for key, value in parse_scope(long_term_scope).items() if value
        }
        # 前导上下文由这里传进去（而不是让 coordinator 自己去读短期记忆）：
        # 只有这里知道真实的会话 scope（run:xxx）。
        memories = get_memory_coordinator().add(
            batch, **scope_kwargs, infer=True, context=context,
        )
        # 关键：只有成功才推进水位
        _get_cache().set_extract_watermark(run_scope, end_id)
        logger.info(
            f"长期记忆抽取完成：{len(batch)} 条消息 → 新增 {len(memories)} 条记忆，"
            f"水位推进到 {end_id}"
        )
    except Exception as exc:
        logger.warning(f"长期记忆抽取失败，水位不推进（下次会重试）：{exc}")
    finally:
        lock.release(long_term_scope)
