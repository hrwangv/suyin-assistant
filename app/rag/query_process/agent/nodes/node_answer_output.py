import sys

from app.utils.task_utils import add_running_task, add_done_task, set_task_result
from app.utils.sse_utils import push_to_session, SSEEvent
from app.rag.query_process.agent.state import QueryGraphState
from app.core.logger import logger
from app.core.load_prompt import load_prompt
from app.llm.lm_utils import get_llm_client
from app.memory.recent_message_service import get_recent_message_service
from app.memory.utils.scope import build_long_term_scope, build_scope, parse_scope
from app.memory.coordinator import get_memory_coordinator
from app.memory.extraction_trigger import maybe_trigger_extraction
from app.memory.config import (
    AUTO_LONG_TERM_MEMORY_ENABLED,
    MEMORY_DEFAULT_USER_ID,
)
# token 计数与预算裁剪（和记忆抽取、embedding 输入共用同一套口径）
from app.utils.token_budget import count_tokens
# 回答 prompt 的预算参数（总预算 / 输出预留 / 安全缓冲 / 长期记忆条数）
from app.conf.answer_config import answer_config
import re

_IMAGE_BLOCK_MARKER = "【图片】"

# ------------------------------------------------------------------ #
# 最终 prompt 的 token 预算
#
# 参数全部在 app/conf/answer_config.py（可用环境变量覆盖），这里只做引用。
#
# 这里是上下文的最后一道总控：
#   docs(按分数降序) / history(按时间升序) / long_term(按相关度降序)
# 三段在这个总预算内竞争，超额时按优先级整条丢弃。
#
# 为什么不直接用模型窗口（1M）当预算：真正的约束是成本、首字延迟，
# 以及长上下文下的注意力衰减（中间部分容易被忽略）。
# ------------------------------------------------------------------ #


def _available_input_budget() -> int:
    """真正可以分给三个来源的 token 数（= 总预算 − 输出预留 − 安全缓冲）。"""
    return answer_config.available_input_budget


def step_1_check_answer(state):
    # 判断第一个节点！有没有明确的answer回答 （item_name）
    # 1.获取 answer | is_stream
    answer = state.get("answer")
    is_stream = state.get("is_stream",True)
    if answer:
        # 已经有答案
        if is_stream:
            # 流式
            # 1. 推送到sse
            push_to_session(state["session_id"], SSEEvent.DELTA, {"delta": answer})
        else:
            # 非流式
            # 1. 设置任务结果
            set_task_result(state["session_id"], "answer", answer)
        # 流式
        # 非流式
        return True
    else:
        return False


def _format_doc_entry(index, doc):
    """把一条命中的文档格式化成 prompt 里的一个条目。

    除正文外，把 payload 里对回答有用的元数据也带上：来源文件（file_title）、
    日期（本地块 news_date / 联网块 date）、栏目（section）、主体（item_name/company_name）。
    这些字段在入库时就构建好了、检索阶段还用于过滤，之前没传给模型，
    结果是答案里无法标注时间和出处，也做不到溯源。

    空值直接省略，避免 prompt 里出现一堆空的 [xx=] 干扰模型。
    """
    parts = [str(index), f"source={doc.get('source')}"]
    title = doc.get("title")
    if title:
        parts.append(f"title={title}")
    file_title = doc.get("file_title")
    if file_title:
        parts.append(f"file={file_title}")
    # 本地块用 news_date（入库时已统一成 RFC3339），联网块用 date，两者取其一
    date = doc.get("news_date") or doc.get("date")
    if date:
        parts.append(f"date={date}")
    section = doc.get("section")
    if section:
        parts.append(f"section={section}")
    item_name = doc.get("item_name") or doc.get("company_name")
    if item_name:
        parts.append(f"item={item_name}")
    score = doc.get("score")
    if score is not None:
        parts.append(f"score={score}")
    return "[" + "][".join(parts) + f"]\n\n{doc.get('text')}"


def step_2_load_prompt(state):
    """
    加载模型润色答案的提示词！！
    拼接各项数据源
    :param state:
    :return:
    """

    # 数据从state中获取
    # 重写后的问题
    rewritten_query = state.get("rewritten_query") or state.get("original_query")  # question
    # 重排序之后的文档信息
    reranked_docs = state.get("reranked_docs",[])
    # 历史信息，短期记忆。
    # node_item_name_confirm 已经从 Redis/MySQL 读取短期上下文并写入 state["history"]，
    # 这里直接消费即可，不需要在 answer 节点重复读取 Redis。
    history = state.get("history", [])

    # 1. 三个来源各自整理成「条目列表」，先不拼接。
    #    保持分段是后面能按优先级整条丢弃的前提：一旦拼成一个大字符串，
    #    超预算时只能按字符硬切，会把文档切一半、把一句话切断。
    #
    # 1.1 RAG 文档（rerank 已按分数降序）
    # reranked_docs => [{text,chunk_id,score,title,source local|web,
    #                    file_title,news_date,section,item_name,category,...}]
    docs = [
        _format_doc_entry(i, doc)
        for i, doc in enumerate(reranked_docs, start=1)
    ]

    # 1.2 短期记忆（按时间升序：第一条最旧）
    history_items = []
    for message in history:
        role = message.get("role")
        text = message.get("content")
        if role == "user" and text:
            history_items.append(f"【用户】: {text}")
        elif role == "assistant" and text:
            history_items.append(f"【模型助手】: {text}")

    # 1.3 长期记忆（按相关度降序，step_2 内部已检索）
    long_term_items = step_2_load_long_term_memory(state)

    # 2. 总 token 控制：三段 + 固定开销一起算，超额按优先级丢
    docs, history_items, long_term_items = _fit_prompt_budget(
        docs=docs,
        history=history_items,
        long_term=long_term_items,
        question=rewritten_query,
    )

    # 3. 组装最终 prompt（三段都空时给占位文案，保持原来的行为）
    final_context = "\n\n".join(docs)
    history_str = "\n".join(history_items) if history_items else "没有历史对话记录！"
    long_term_memories_str = "\n\n".join(long_term_items) if long_term_items else "没有相关长期记忆。"
    answer_out_prompt = load_prompt("answer_out",
                           context=final_context, # rag的内容 
                           history=history_str, # 短期记忆上下文
                           long_term_memories=long_term_memories_str, # 长期记忆
                           question=rewritten_query)
    logger.info(
        f"已经完成了提示词生成：文档 {len(docs)} 条 / 历史 {len(history_items)} 条 / "
        f"长期记忆 {len(long_term_items)} 条 | 总 token≈{count_tokens(answer_out_prompt)}"
    )
    return answer_out_prompt


def _fit_prompt_budget(docs, history, long_term, question):
    """把三个来源裁进总 token 预算，超额时按优先级整条丢弃。

    优先级（先丢的在前）：长期记忆 → 最旧的历史 → 最低分的文档。
    · 长期记忆是提示词里定义的"背景补充"，最先牺牲
    · 历史对话决定多轮追问能否接上，从最旧的一端丢
    · 文档是答案的主要依据，从分数最低的一端丢，最后才动

    固定开销（模板 / 当前问题）不参与丢弃——它们永远要留。

    实现上每段只算一次 token，之后每丢一条做一次减法，不整体重算。
    """
    # 固定开销：模板（占位符填空后）+ 当前问题。
    # 这两部分永远保留，不参与下面的丢弃。
    empty_prompt = load_prompt(
        "answer_out", context="", history="", long_term_memories="",
        question="",
    )
    fixed_tokens = count_tokens(empty_prompt) + count_tokens(question)

    # 三段来源按「先丢」到「后丢」排列（顺序即优先级）。
    # "last" = 从末尾丢（长期记忆按相关度降序、文档按分数降序）；
    # "oldest" = 从最旧的一端丢（历史对话按时间升序）。
    sections = [
        # (名字, 条目, 丢弃端)
        ("长期记忆", long_term, "last"),
        ("历史对话", history, "oldest"),
        ("RAG文档", docs, "last"),
    ]
    # 每条 token 只算一次，后面丢的时候做减法，不整体重算
    token_lists = [[count_tokens(item) for item in items] for _, items, _ in sections]
    total = fixed_tokens + sum(sum(tokens) for tokens in token_lists)
    # 预算 = 总预算 − 输出预留 − 安全缓冲（见 app/conf/answer_config.py）
    budget = _available_input_budget()

    if total <= budget:
        return docs, history, long_term

    dropped_summary = {}
    for (name, items, drop_side), tokens in zip(sections, token_lists):
        dropped = 0
        if drop_side == "oldest":
            while items and total > budget:
                total -= tokens.pop(0)
                items.pop(0)
                dropped += 1
        else:
            while items and total > budget:
                total -= tokens.pop()
                items.pop()
                dropped += 1
        if dropped:
            dropped_summary[name] = dropped
        if total <= budget:
            break

    logger.info(
        f"prompt 超过 token 预算，已按优先级裁剪：{dropped_summary} "
        f"| 裁剪后约 {total} token（预算 {budget}）"
    )
    return docs, history, long_term


def step_2_load_long_term_memory(state):
    """
    检索并格式化长期记忆。

    长期记忆按「用户」聚合（跨对话共享），scope 的计算统一走
    build_long_term_scope，与 step_6 写入时完全一致，避免一边写一边读不到。

    返回**条目列表**（不是拼好的字符串）：step_1 的 token 总控需要按条丢弃，
    拼成字符串就没法整条裁剪了。没有命中时返回空列表。
    """
    query = state.get("rewritten_query") or state.get("original_query") or ""
    session_id = state.get("session_id")
    if not query or not session_id:
        return []

    try:
        scope = build_long_term_scope(
            user_id=state.get("user_id"),
            default_user_id=MEMORY_DEFAULT_USER_ID,
            run_id=session_id,
        )
        # 执行长期记忆的检索，里面涉及到实体图谱entity
        search_result = get_memory_coordinator().search(
            query=query,
            scope=scope,
            # 检索条数走配置（ANSWER_LONG_TERM_MEMORY_TOP_K，默认 5）：
            # 长期记忆在 prompt 里定位是"背景补充"，条数不放大，
            # 而且它是最先被总预算裁掉的来源。
            top_k=answer_config.long_term_memory_top_k, # 检索5条长期记忆
            # 不走 rerank（coordinator.search 的默认值也是 False）：
            # 只取 5 条背景补充，精排能改变的余地很小，
            # 却要多付一次外部 API 的延迟和成本；排序由「稠密相似度 + 实体加分」决定。
            rerank=False,
        )
        long_term_memories = search_result.get("long_term_memories", [])
    except Exception as exc:
        logger.warning(
            f"answer 节点检索长期记忆失败，session_id={session_id}：{exc}"
        )
        return []

    if not long_term_memories:
        return []

    memories = []
    for index, memory in enumerate(long_term_memories, start=1):
        memory_id = memory.get("memory_id", "")
        data = memory.get("data", "")
        memories.append(f"[{index}][memory_id={memory_id}]\n{data}")

    # 这里不再限制总字符数：长度统一交给 step_1 的 token 总控裁剪
    return memories


def step_3_create_answer(state, prompt):
    """
    使用模型生成最终的答案
    :param state:
    :param prompt:
    :return:
    """
    # 1. 获取模型对象和客户端
    model = get_llm_client()
    # 2. 获取流式状态【sse | set_result】
    is_stream = state.get("is_stream",True)
    answer = ''
    if is_stream:
        # 3. 调用模型进行生成 sse . stream  ||  set_result . invoke
        # 1 2 3 4 5 6 7
        for chunk in model.stream(prompt):
            # 3.1 推到sse
            delta = chunk.content # 1 | 2 3 | 4 | 5 6 7 |
            answer += delta #累加答案
            push_to_session(state["session_id"], SSEEvent.DELTA, {"delta": delta})
    else:
        # 4. 最终的答案赋值给state['answer'] = 答案
        response = model.invoke(prompt)
        content = response.content
        answer = content
        set_task_result(state["session_id"], "answer", content)
    # 5. 返回结果answer即可
    state['answer'] = answer
    logger.info(f"lm模型最终返回的结果：{answer}")
    return answer


def step_4_extract_images_url(state):
    """
    从local -> chunk -> text中提取
       {text:" ![](url) "}  -> url
    从web   -> url   -> 图片提取
       mcp { url:"网络搜索 关联网址 || 图片地址" }
    :param state:
    :return:
    """
    images = []  # -> 存储图片 (On) (想要先后顺序)
    set_images = set() # -> 图片重复判断  （重复时间复杂度 O1）

    # 1. 定义正则
    image_reg = re.compile(r"!\[.*?\]\((.*?)\)")
    # reranked_docs => [{text,chunk_id,score,url,title,source local | web },{}]
    # 2. 宣传处理切片 -》 从高分 -》 低分
    reranked_docs = state.get("reranked_docs",[])
    for doc in reranked_docs:
       #{text,chunk_id,score,url,title,source local | web }
       # url -> 是不是图片
       url = doc.get("url")
       if url:
           if url.endswith((".png",".jpg",".jpeg",".gif",".webp")):
               # set -> not in  O1
               if url not in set_images:
                   images.append(url)
                   set_images.add(url)

       text = doc.get("text")
       # text -> 正则提取图片
       if text:
           # 正在匹配的所有图片
           matches = image_reg.findall(text)
           for image_url in matches:
               if image_url not in set_images:
                   images.append(image_url)
                   set_images.add(image_url)
       # 不存在-》添加到images即可
    logger.info(f"已经完成图片提取。数量:{len(images)},提取内容：{images}")
    state['image_urls'] =  images
    return images


def step_5_write_history(state):
    """
    将对话存储到 Session Memory
    每次对话 对应2条history
       我们问  -》 user  ->  question -> text
       查询到  -》 assistant -> answer -> text

    写失败一律降级、不上抛：这个函数在答案已经推给前端之后执行，
    抛出去会把「已经答完」的一轮对话标记成 failed（还跳过 add_done_task）。
    代价是本轮短期记忆丢失，这个代价可以接受。
    :param state:
    :return:
    """
    session_id = state.get("session_id")
    answer = state.get("answer")

    if not answer:
        logger.info("没有可写入的 assistant 回答，跳过记录存储")
        return

    try:
        memory_service = get_recent_message_service()
        scope = build_scope(run_id=session_id)
        memory_service.add_messages(
            scope,
            [
                {
                    "role": "assistant",
                    "content": answer,
                }
            ],
        )
    except Exception as exc:
        logger.error(
            f"写入助手回答失败（忽略，答案已返回用户）：session_id={session_id}, err={exc}"
        )
        return
    logger.info("完成了本次对话的记录存储")


def step_6_extract_long_term_memory(state):
    """
    长期记忆抽取的触发点（在每轮问答结束时调用）。

    注意这里**不是每轮都抽**：真正的判断在水位逻辑里（app/memory/extraction_trigger.py）——
    距上次抽取新增的消息数达到阈值（默认 40 条）才触发一次批量抽取。
    没到阈值时这个函数什么都不做，只打一条日志。

    scope 分工：
    - run_scope（会话级）：水位与抽取批次以它为维度，因为批次来自本会话的短期消息；
    - long_term_scope（用户级）：抽取结果写这里，与 step_2 读取时用同一个 scope，
      保证跨对话能读到。
    """
    if not AUTO_LONG_TERM_MEMORY_ENABLED:
        return

    session_id = state.get("session_id")
    if not session_id:
        return

    run_scope = build_scope(run_id=session_id)
    long_term_scope = build_long_term_scope(
        user_id=state.get("user_id"),
        default_user_id=MEMORY_DEFAULT_USER_ID,
        run_id=session_id,
    )
    if not parse_scope(long_term_scope).get("user_id"):
        logger.warning(
            f"长期记忆当前为会话级隔离（未提供 user_id 且未配置 "
            f"MEMORY_DEFAULT_USER_ID），新对话读不到本次记忆：session_id={session_id}"
        )
    try:
        # 到阈值才真正触发；不够阈值直接返回，最多打一条日志
        maybe_trigger_extraction(run_scope, long_term_scope)
    except Exception as exc:
        logger.warning(f"长期记忆抽取触发失败，session_id={session_id}：{exc}")


def node_answer_output(state):
    """
    宏观：将最终topk -> 大模型 -> 润色 -> 结果 -> 【 【流式】 sse -》 前端 （push_to_session）  【非流式】set_task_result】
       1. 先检查state中是否存在answer回答  【item_name (1.明确 【 2.不确定 3.没有】 answer -> state)】 有可以直接写回答案
       2. 生成对应的润色的提示词 prompt
       3. 使用模型润色答案 -》 结果 -> 文本
       4. 提取原来topklist中的图片地址，单独返回【see】
       5. 对话的聊天记录（用户 user/助手 assistant）
       6. sse-final->返回图片
    节点功能：进行过处理可以是流式输出可以整体输出！
    """
    print("---node_answer_output 节点处理开始---")
    add_running_task(state["session_id"], sys._getframe().f_code.co_name, state.get("is_stream"))
    # 1. 检查state中是否存在answer回答  
    # 【item_name (1.明确 【 2.不确定 3.没有】 answer -> state)】
    answer_exists = step_1_check_answer(state)
    if not answer_exists:
        # 2. 成对应的润色的提示词 prompt
        prompt = step_2_load_prompt(state)
        # 3. 使用模型润色答案 -》 结果 -> 文本
        answer = step_3_create_answer(state,prompt)
        # 4. 提取原来topklist中的图片地址，单独返回【see】
        images_url = step_4_extract_images_url(state)
        # 6. sse-final->返回图片
        if images_url:
            # 不管流 和 非流都需要返回图片
            push_to_session(state["session_id"],
                            SSEEvent.FINAL,
                            {"answer":answer,
                                   "status":"completed",
                                      "image_urls": images_url})
    # 数据都已经推送完毕了
    # 5. 添加聊天记录
    step_5_write_history(state)
    # 6. 自动触发长期记忆抽取，使用后台线程，不阻塞本轮对话结束
    step_6_extract_long_term_memory(state)
    add_done_task(state['session_id'], sys._getframe().f_code.co_name, state.get("is_stream"))
    print("---node_answer_output 节点处理结束---")
    return state


if __name__ == "__main__":
    print("\n" + "=" * 50)
    print(">>> 启动 node_answer_output 本地测试")
    print("=" * 50)

    # 1. 构造模拟数据
    # 模拟重排序后的文档列表 (reranked_docs)
    # 包含：本地文档（带Markdown图片）、联网结果（带URL字段）、纯文本文档
    mock_reranked_docs = [
        {
            "chunk_id": "local_101",
            "source": "local",
            "title": "HAK 180 烫金机操作手册_v2.pdf",
            "score": 0.95,
            "text": """
            HAK 180 烫金机的操作面板位于机器正前方。
            开启电源后，您需要先设置温度，默认建议设置在 110℃ 左右。
            具体的操作面板布局请参考下图：
            ![操作面板布局图](http://www.baidu.com/img/bd_logo.png)

            如果是进行局部烫金，请调节侧面的旋钮。
            ![侧面旋钮细节](http://local-server/images/knob_detail.png)
            """
        },
        {
            "chunk_id": None,
            "source": "web",
            "title": "HAK 180 常见故障排除 - 官网",
            "score": 0.88,
            "url": "http://example.com/hak180_troubleshooting.jpeg",  # 这是一个直接指向图片的URL（虽然少见，但用于测试提取）
            "text": "如果机器无法加热，请检查保险丝是否熔断..."
        },
        {
            "chunk_id": "local_102",
            "source": "local",
            "title": "安全注意事项",
            "score": 0.82,
            "text": "操作时请务必佩戴隔热手套，避免高温烫伤。"
        }
    ]

    # 模拟历史记录
    mock_history = [
        {"role": "user", "text": "你好，这款机器怎么用？"},
        {"role": "assistant", "text": "您好！请问您具体指的是哪一款机器？"},
        {"role": "user", "text": "HAK 180 烫金机"}
    ]

    # 模拟输入状态
    mock_state = {
        "session_id": "test_answer_session_001",
        "original_query": "HAK 180 烫金机怎么操作？",
        "rewritten_query": "HAK 180 烫金机的具体操作步骤和面板设置方法",
        "history": mock_history,
        "reranked_docs": mock_reranked_docs,
        "is_stream": False,  # 测试非流式
        # "is_stream": True, # 若要测试流式，需确保 SSE 环境或 mock 相关函数
        "answer": None  # 初始无答案
    }

    try:
        # 运行节点
        result = node_answer_output(mock_state)

        print("\n" + "=" * 50)
        print(">>> 测试结果摘要:")

        # 1. 验证 Prompt 构建
        if "prompt" in result:
            print(f"[PASS] Prompt 构建成功 (长度: {len(result['prompt'])})")
            # print(f"Prompt 预览:\n{result['prompt'][:200]}...")
        else:
            print("[FAIL] Prompt 未构建")

        # 2. 验证答案生成
        answer = result.get("answer")
        if answer and len(answer) > 10:
            print(f"[PASS] 答案生成成功 (长度: {len(answer)})")
            print(f"答案预览: {answer[:50]}...")
        else:
            print(f"[WARN] 答案生成可能异常 (Content: {answer})")

        # 3. 验证图片提取
        # 我们期望提取到 3 张图片：
        # 1. http://local-server/images/panel_view.jpg (来自 local_101)
        # 2. http://local-server/images/knob_detail.png (来自 local_101)
        # 3. http://example.com/hak180_troubleshooting.jpeg (来自 web 结果的 url 字段)

        # 注意：这里我们没办法直接从 result state 里拿到 image_urls，因为它是作为 SSE 推送出去的，或者存库了
        # 但我们可以通过日志观察 _extract_images_from_docs 的输出
        # 如果需要验证，可以临时修改 node_answer_output 返回 image_urls
        print("\n[INFO] 请检查上方日志中是否包含 '图片提取完成' 及以下 URL:")
        print(" - http://local-server/images/panel_view.jpg")
        print(" - http://local-server/images/knob_detail.png")
        print(" - http://example.com/hak180_troubleshooting.jpeg")

        print("=" * 50)

    except Exception as e:
        logger.exception(f"测试运行期间发生未捕获异常: {e}")
