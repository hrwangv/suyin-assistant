import sys
import os

from app.conf.qdrant_config import qdrant_config
from app.utils.task_utils import add_running_task,add_done_task
from app.llm.qwen_embedding_utils import generate_embeddings
from app.utils.qdrant_utils import get_qdrant_client, rrf_hybrid_search
from app.core.logger import logger
from dotenv import load_dotenv,find_dotenv
load_dotenv(find_dotenv())

def node_search_embedding(state):
    """
    节点功能：进行向量内容检索
    主要作用：根据重写的问题 去 查询 chunks切片
    达到目标：{"embedding_chunks": [chunks]}
    需要参数：
            {
               rewritten_query : 重写的问题  -》 根据他查询
               item_names : []  -》 明确的主体
            }
    """
    print("---内容检索 开始处理---")
    add_running_task(state["session_id"], sys._getframe().f_code.co_name, state.get("is_stream"))

    # 搜索假设性答案
    # 1. 先从state获取参数数据
    rewritten_query = state.get("rewritten_query")
    item_names = state.get("item_names")
    # 2. 将重写问题生成对应的向量【稠密和稀疏】
    embeddings = generate_embeddings([rewritten_query])
    # 3. 进行向量数据库的混合查询
    dense_vector = embeddings["dense"][0] # List[float]
    sparse_vector = embeddings["sparse"][0] # Dict{int: float}
    qdrant_client = get_qdrant_client()

    # 混合查询
    response = rrf_hybrid_search(
                        client=qdrant_client,
                        collection_name=qdrant_config.chunks_collection,
                        dense_vector=dense_vector,
                        sparse_vector=sparse_vector    
                        )

    # print(response.points)

    embedding_chunks=[
       {"id":p.id,"score":p.score,"payload":p.payload}
        for p in (response.points or[])
   ]


    """
       [
       
         [
            {id ,
            distance，
            entity:
               {
                  "chunk_id", "content","file_title", "title", "parent_title", "item_name"
               }
         ]
       ]
    
    """
    # ...
    add_done_task(state["session_id"], sys._getframe().f_code.co_name, state.get("is_stream"))

    print("---内容检索 处理结束---")
    return {"embedding_chunks": embedding_chunks}


if __name__ == "__main__":
    # 模拟测试数据
    test_state = {
        "session_id": "test_search_embedding_001",
        "rewritten_query": "上海电气做了什么",  # 模拟改写后的查询
        "item_names": ["经营晨报20260310"],  # 模拟已确认的商品名
        "is_stream": True
    }

    print("\n>>> 开始测试 node_search_embedding 节点...")
    try:
        # 执行节点函数
        result = node_search_embedding(test_state)
        logger.info(f"检索结果汇总：{result}")
        # 验证结果
        chunks = result.get("embedding_chunks", [])
        logger.info(f"\n>>> 测试完成！检索到 {len(chunks)} 条结果,结果为：{chunks}")

    except Exception as e:
        logger.error(f"测试运行失败: {e}", exc_info=True)