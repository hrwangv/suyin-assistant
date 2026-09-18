# HyDE节点
import sys

from langchain_core.messages import HumanMessage

from app.utils.task_utils import add_running_task, add_done_task
from app.llm.lm_utils import *
from app.llm.qwen_embedding_utils import *
from app.utils.qdrant_utils import *
from app.conf.retrieval_config import retrieval_config
from app.core.logger import logger
from app.core.load_prompt import load_prompt
# 结构化过滤条件 → Qdrant Filter（由 Python 构造，见 retrieval 包）
from app.rag.query_process.retrieval.qdrant_filter_builder import (
    build_qdrant_filter,
    describe_filters,
)
from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv())


def step_1_create_hyde_doc(rewritten_query):
    """
    调用模型根据问题，生成一份答案
    :param rewritten_query:  问题
    :return: 答案字符串
    """
    llm = get_llm_client()

    # 加载提示词
    hyde_prompt = load_prompt("hyde_prompt",rewritten_query = rewritten_query)

    messages = [
        HumanMessage(content=hyde_prompt)
    ]
    # 发起请求
    response = llm.invoke(messages)
    hyde_doc = response.content
    logger.info(f"使用模型生成假设性答案，问题：{rewritten_query},答案：{hyde_doc}")
    return hyde_doc


def step_2_search_embedding_hyde(rewritten_query, hyde_doc, query_filter=None):
    """
    根据问题+假设性答案查询向量数据库，进行混合查询
    :param rewritten_query:
    :param hyde_doc:
    :param query_filter: 由 Query Analyzer 的结构化条件构造出来的 Qdrant Filter；
                         必须和主检索路用同一个，否则两路候选集口径不一致，RRF 融合会失真
    :return: [[] -> 结果  id 分数 实体列信息 ]
    """
    # 1.拼接重写问题 + lm生成的假设性答案
    query_str = rewritten_query + hyde_doc
    # 2.拼接查询字符串生成对应向量
    embeddings = generate_embeddings([query_str])

    # 3. 进行向量数据库的混合查询
    dense_vector = embeddings["dense"][0] # List[float]
    sparse_vector = embeddings["sparse"][0] # Dict{int: float}
    qdrant_client = get_qdrant_client()

    # 混合查询
    response = rrf_hybrid_search(
                        client=qdrant_client,
                        collection_name=qdrant_config.chunks_collection,
                        dense_vector=dense_vector,
                        sparse_vector=sparse_vector,
                        # 召回池大小由调用方决定，取值统一来自 retrieval_config
                        limit=retrieval_config.fused_limit,
                        dense_limit=retrieval_config.prefetch_limit,
                        sparse_limit=retrieval_config.prefetch_limit,
                        query_filter=query_filter,
                        )

    # 兜底：带过滤条件召回为空时去掉过滤重查（与主检索路保持一致的策略）
    if not (response.points or []) and query_filter is not None:
        logger.warning("[HyDE 检索] 带过滤条件检索为空，放宽条件重查")
        response = rrf_hybrid_search(
                            client=qdrant_client,
                            collection_name=qdrant_config.chunks_collection,
                            dense_vector=dense_vector,
                            sparse_vector=sparse_vector,
                            limit=retrieval_config.fused_limit,
                            dense_limit=retrieval_config.prefetch_limit,
                            sparse_limit=retrieval_config.prefetch_limit,
                            )

    result=[
           {"id":p.id,"score":p.score,"payload":p.payload}
            for p in (response.points or[])
       ]
    
    # 5.处理返回结果
    logger.info(f"假设性问题检索结果：{result}")
    return result

def node_search_embedding_hyde(state):
    """
    假设性答案 ： 问题 -》 lm -> 给一个假设性答案  -》 问题+假设性答案 -》 搜索

    节点功能：HyDE (Hypothetical Document Embedding)
    先让 LLM 生成假设性答案，再对答案进行向量检索，提高召回率。
    """
    print("---HyDE 开始处理---")
    add_running_task(state["session_id"], sys._getframe().f_code.co_name, state.get("is_stream"))

    # 1. 提取参数
    rewritten_query = state.get("rewritten_query")
    # 1.1 结构化过滤条件（与主检索路同一份，保证两路口径一致）
    retrieval_filters = state.get("retrieval_filters") or {}
    query_filter = build_qdrant_filter(retrieval_filters)
    logger.info(f"[HyDE 检索] 结构化过滤条件：{describe_filters(retrieval_filters)}")
    # 2. 调用 LLM 生成假设性答案 rewritten_query
    hyde_doc = step_1_create_hyde_doc(rewritten_query)
    # 3. 问题+答案，进行向量检索（混合检索）
    resp = step_2_search_embedding_hyde(rewritten_query, hyde_doc, query_filter=query_filter)
    # 4. 赋值和返回结果  hyde_embedding_chunks
    # ...
    add_done_task(state["session_id"], sys._getframe().f_code.co_name, state.get("is_stream"))

    print("---HyDE 处理结束---")
    return {"hyde_embedding_chunks":resp}


if __name__ == "__main__":
    # 本地测试代码
    print("\n" + "=" * 50)
    print(">>> 启动 node_search_embedding_hyde 本地测试")
    print("=" * 50)

    # 模拟输入状态
    mock_state = {
        "session_id": "test_hyde_session_001",
        "original_query": "上海电气做了什么",
        "rewritten_query": "上海电气做了什么",
        "is_stream": False
    }

    try:
        # 运行节点
        result = node_search_embedding_hyde(mock_state)

        print(result)

    except Exception as e:
        logger.exception(f"测试运行期间发生未捕获异常: {e}")
