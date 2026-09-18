"""token 预算控制测试。

覆盖两处：
1. 长期记忆抽取：短期上下文进模型前按 token 预算裁剪，超额丢最老的一条；
2. 最终回答 prompt：三段（docs/history/long_term）在总预算内按优先级竞争。

不依赖大模型 / Redis / MySQL / Qdrant，全部用假对象。
运行：python -m app.test.test_prompt_token_budget
"""
from __future__ import annotations

from datetime import datetime

from app.utils.token_budget import count_tokens, fit_token_budget

passed = 0
failed: list[str] = []


def check(name: str, actual, expected):
    global passed
    if actual == expected:
        passed += 1
    else:
        failed.append(f"{name}\n     期望：{expected}\n     实际：{actual}")


def test_fit_token_budget_basics():
    """工具本身：丢哪一端、丢到刚好、至少留一条。"""
    items = [f"第{i}条" * 10 for i in range(1, 11)]   # 每条约 30 token
    one = [count_tokens(items[0])]
    print

    # 预算充足 → 一条不丢
    kept, dropped = fit_token_budget(items, budget=10 ** 6, drop="oldest")
    check("预算充足不裁剪", (len(kept), dropped), (10, 0))

    # 丢最老的（历史对话用它）：保留末尾的几条
    kept, dropped = fit_token_budget(items, budget=one[0] * 3, drop="oldest")
    check("丢最老的一端", kept[-1], items[-1])
    check("保留条数正确", len(kept), 3)
    check("丢弃条数正确", dropped, 7)

    # 丢最后的（按分数降序的列表用它）：保留开头的几条
    kept, dropped = fit_token_budget(items, budget=one[0] * 3, drop="last")
    check("丢最后的一端", kept[0], items[0])
    check("保留条数正确（tail）", len(kept), 3)

    # 空输入
    check("空输入", fit_token_budget([], budget=100), ([], 0))

    # keep_one：即使单条就超预算也要留一条
    kept, dropped = fit_token_budget(items, budget=1, drop="oldest", keep_one=True)
    check("keep_one 至少留一条", len(kept), 1)
    check("keep_one 保留的是最新一条", kept[0], items[-1])


def test_extraction_context_trimmed():
    """长期记忆抽取：前导上下文超额时丢最老的，保留最新的。

    注意上下文现在由**调用方传入**（extraction_trigger 从真实会话 scope 读），
    所以这里直接构造传入，而不是靠假 recent_service。
    """
    import app.memory.coordinator as coordinator_module
    from app.memory.config import EXTRACTION_CONTEXT_MAX_TOKENS

    captured = {}

    class FakeVectorStore:
        def scroll_active(self, scope):
            return []

    class FakeExtractor:
        def extract(self, new_messages, existing_memories, last_messages):
            captured["last_messages"] = last_messages
            return {"memory": []}

    coordinator = coordinator_module.MemoryCoordinator.__new__(coordinator_module.MemoryCoordinator)
    coordinator.recent_service = None
    coordinator.vector_store = FakeVectorStore()
    coordinator.extractor = FakeExtractor()
    coordinator.history_store = None
    coordinator.entity_store = None
    coordinator.vector_store.upsert = lambda *a, **k: None

    coordinator._add_with_extraction(
        messages=[{"role": "user", "content": "本轮问题"}],
        scope="user:u1",
        user_id="u1",
        agent_id=None,
        run_id=None,
        metadata={},
        now=datetime.now(),
        # 20 条长消息，远超预算
        context=[{"role": "user", "content": f"第{i}条历史" * 60} for i in range(1, 21)],
    )

    kept = captured["last_messages"]
    total = sum(count_tokens(m["content"]) for m in kept)
    check("抽取上下文被裁剪过", len(kept) < 20, True)
    check("裁剪后不超过预算", total <= EXTRACTION_CONTEXT_MAX_TOKENS, True)
    check("保留的是最新的几条", kept[-1]["content"].startswith("第20条历史"), True)
    check("丢掉的是最老的那条", any(m["content"].startswith("第1条历史") for m in kept), False)


def test_prompt_budget_priority():
    """最终 prompt：超额时先丢长期记忆，再丢最旧历史，最后才动文档。"""
    import app.rag.query_process.agent.nodes.node_answer_output as answer_node

    # 造数据：文档 10 条 × 约 1500 token，历史 20 条 × 约 800 token，长期记忆 5 条 × 约 400 token
    state = {
        "rewritten_query": "欣旺达今年有什么动态",
        "reranked_docs": [
            {"text": "文档内容" * 500, "source": "local", "title": f"标题{i}", "score": 0.9 - i * 0.01}
            for i in range(1, 11)
        ],
        "history": [
            {"role": "user" if i % 2 else "assistant", "content": "历史对话" * 260}
            for i in range(1, 21)
        ],
    }
    answer_node.step_2_load_long_term_memory = lambda s: [
        f"[{i}][memory_id=m{i}]\n长期记忆内容" * 20 for i in range(1, 6)
    ]

    prompt = answer_node.step_2_load_prompt(state)
    total = count_tokens(prompt)
    budget = answer_node._available_input_budget()

    check("总 token 不超过预算", total <= budget, True)
    check("当前问题一定保留", "欣旺达今年有什么动态" in prompt, True)
    check("模板主体保留", "【参考内容】" in prompt, True)

    # 优先级校验：长期记忆被裁空后，才开始裁历史
    long_term_left = prompt.count("[memory_id=")
    history_left = prompt.count("【用户】") + prompt.count("【助手】")
    if history_left < 20:
        check("历史被裁时长期记忆必须先裁空", long_term_left, 0)
    check("历史确实被裁过", history_left < 20, True)
    # 文档优先级最高，不应该被裁（本例中裁完记忆和历史后已够）
    check("文档未被裁剪", prompt.count("[source=local]"), 10)


def test_analyzer_history_trimmed():
    """Query Analyzer 的历史输入由调用方用 fit_token_budget 控制。"""
    from app.conf.memory_config import MAX_TOKEN_WINDOW_SIZE
    import app.rag.query_process.agent.nodes.node_item_name_confirm as analyzer

    captured = {}

    class FakeMemoryService:
        def get_messages(self, scope, limit=None):
            # 20 条长消息，远超 8000 token 预算
            return [{"role": "user", "content": "历史内容" * 200} for _ in range(20)]

        def add_messages(self, scope, messages):
            pass

    analyzer.get_recent_message_service = lambda: FakeMemoryService()
    analyzer.add_running_task = lambda *a, **k: None
    analyzer.add_done_task = lambda *a, **k: None
    analyzer.step_3_analyze_query = (
        lambda query, history: captured.update(history=history) or {"rewritten_query": query}
    )

    analyzer.node_item_name_confirm({
        "session_id": "s-1", "original_query": "它今年的表现如何", "is_stream": False,
    })

    kept = captured["history"]
    total = sum(count_tokens(m["content"]) for m in kept)
    check("Analyzer 历史被裁剪", len(kept) < 20, True)
    check("裁剪后不超过预算", total <= MAX_TOKEN_WINDOW_SIZE, True)
    check("保留的是最近的消息", len(kept) > 0, True)


def test_prompt_budget_no_trim_when_small():
    """内容不多时不做任何裁剪，且三段都进 prompt。"""
    import app.rag.query_process.agent.nodes.node_answer_output as answer_node

    state = {
        "rewritten_query": "什么是 RAG",
        "reranked_docs": [{"text": "短文档", "source": "local", "title": "t", "score": 0.8}],
        "history": [{"role": "user", "content": "你好"}, {"role": "assistant", "content": "你好呀"}],
    }
    answer_node.step_2_load_long_term_memory = lambda s: ["[1][memory_id=m1]\n一条长期记忆"]

    prompt = answer_node.step_2_load_prompt(state)
    check("文档保留", "[source=local]" in prompt, True)
    check("历史保留", "【用户】: 你好" in prompt, True)
    check("长期记忆保留", "memory_id=m1" in prompt, True)
    check("远低于预算", count_tokens(prompt) < answer_node._available_input_budget(), True)


def test_prompt_budget_empty_sources_get_placeholder():
    """三段都空时仍给出占位文案（保持原有行为）。"""
    import app.rag.query_process.agent.nodes.node_answer_output as answer_node

    state = {
        "rewritten_query": "空场景",
        "reranked_docs": [],
        "history": [],
    }
    answer_node.step_2_load_long_term_memory = lambda s: []
    prompt = answer_node.step_2_load_prompt(state)
    check("历史占位", "没有历史对话记录！" in prompt, True)
    check("长期记忆占位", "没有相关长期记忆。" in prompt, True)


def test_doc_entry_carries_source_metadata():
    """参考条目要带上 payload 元数据（来源文件/日期/栏目/主体），供模型标注时间与出处。"""
    import app.rag.query_process.agent.nodes.node_answer_output as answer_node

    local_doc = {
        "text": "协创数据拟申请800亿融资租赁额度",
        "source": "local", "title": "## 重点客户", "score": 0.93,
        "file_title": "经营晨报 20260410", "news_date": "2026-04-10T00:00:00Z",
        "section": "经营晨报", "item_name": "协创数据", "category": "重点客户",
    }
    web_doc = {
        "text": "协创数据获大额授信",
        "source": "web", "title": "协创数据获大额授信", "score": 0.88,
        "date": "2026-04-08", "category": "公司经营", "company_name": "协创数据",
    }
    state = {
        "rewritten_query": "协创数据最近有什么动静",
        "reranked_docs": [local_doc, web_doc],
        "history": [],
    }
    answer_node.step_2_load_long_term_memory = lambda s: []
    prompt = answer_node.step_2_load_prompt(state)

    check("本地块带来源文件", "file=经营晨报 20260410" in prompt, True)
    check("本地块带日期", "date=2026-04-10T00:00:00Z" in prompt, True)
    check("本地块带栏目", "section=经营晨报" in prompt, True)
    check("本地块带主体", "item=协创数据" in prompt, True)
    check("联网块带日期", "date=2026-04-08" in prompt, True)
    check("联网块主体回退到 company_name", "[item=协创数据]" in prompt, True)
    check("空字段不留空占位", "[file=][date=]" in prompt, False)
    check("正文仍然保留", "协创数据拟申请800亿融资租赁额度" in prompt, True)
    check("来源标注仍在（兼容既有断言）", prompt.count("[source="), 2)


def test_merge_keeps_payload_metadata():
    """RRF 结果合并进 rerank 时，payload 元数据必须透传，不能只挑 4 个字段。"""
    import app.rag.query_process.agent.nodes.node_rerank as rerank_node

    payload = {
        "chunk_id": "c1", "content": "正文内容", "title": "## 重点客户",
        "file_title": "经营晨报 20260410", "news_date": "2026-04-10T00:00:00Z",
        "section": "经营晨报", "item_name": "协创数据", "category": "重点客户",
        "domain": None, "parent_title": "## 重点客户", "part": 1,
        "file_id": "f1", "task_id": "t1",
    }
    merged = rerank_node.step_1_merge_rrf_mcp(
        {
            "rrf_chunks": [{"id": "c1", "score": 0.031, "payload": payload}],
            "web_search_docs": [{
                "date": "2026-04-08", "category": "公司经营",
                "title": "协创数据获大额授信", "summary": "4月8日披露的融资租赁额度",
                "company_name": "协创数据",
            }],
        }
    )
    doc = merged[0]

    check("标注来源为 local", doc["source"], "local")
    check("正文进了 text", doc["text"], "正文内容")
    check("日期透传", doc.get("news_date"), "2026-04-10T00:00:00Z")
    check("来源文件透传", doc.get("file_title"), "经营晨报 20260410")
    check("栏目透传", doc.get("section"), "经营晨报")
    check("主体透传", doc.get("item_name"), "协创数据")
    check("category 不再是硬编码空串", doc.get("category"), "重点客户")
    check("content 不再重复占一份", "content" in doc, False)
    check("chunk_id 保留", doc.get("chunk_id"), "c1")

    # 联网块同样要透传：以前只留 title/category，date 和 company_name 都丢了
    web = merged[1]
    check("联网块标注 source=web", web["source"], "web")
    check("联网块正文走 text", web["text"], "4月8日披露的融资租赁额度")
    check("联网块日期透传", web.get("date"), "2026-04-08")
    check("联网块主体透传", web.get("company_name"), "协创数据")
    check("联网块 summary 不重复占一份", "summary" in web, False)


def main():
    for test in (
        test_fit_token_budget_basics,
        test_extraction_context_trimmed,
        test_prompt_budget_priority,
        test_analyzer_history_trimmed,
        test_prompt_budget_no_trim_when_small,
        test_prompt_budget_empty_sources_get_placeholder,
        test_doc_entry_carries_source_metadata,
        test_merge_keeps_payload_metadata,
    ):
        test()

    print(f"\n通过 {passed} 项，失败 {len(failed)} 项")
    for item in failed:
        print("  [FAIL]", item)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
