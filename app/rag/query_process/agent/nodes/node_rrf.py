import sys
from typing import List, Dict, Any
from app.utils.task_utils import add_running_task, add_done_task
from app.core.logger import logger
from app.conf.retrieval_config import retrieval_config


def step_3_reciprocal_rank_fusion(source_with_weight, top_k: int = retrieval_config.merge_top_k):
    """
    进行同源数据排名+权重处理
    :param source_with_weight:  [(集合,权重),(集合,权重)]
    :return: [{},{},{}]  排序后的前top k个元素

    top_k 走配置（默认 20）：主路 + HyDE 路合并后要保留足够多的候选交给 rerank，
    rerank 才有挑选余地；最终进 prompt 的条数另有 token 总控兜底。
    """
    # 1. 准备两个容器 记录历史得分
    score_dict = {}  # key ：id（chunk_id）   value：计算后的得分
    # 2. 记录chunk片段
    chunk_dict = {}  # key：chunk_id    value：chunk内容   

    # source_with_weight = [
    #         (embedding_chunks,1.0),
    #         (hyde_embedding_chunks,1.0)
    #     ]
    # 3. 循环处理每个集合中的数据，进行积分计算
    for source, weight in source_with_weight: # 实际上有两个源，循环两次
        # source = 【{id: 实体的主键,distance:得分 0.8,entity:{chunk_id,content,title..}} ,{} ,{}】
        # weight = 1.0
        # 嵌套遍历，遍历具体路的数据和chunk
        for rank,chunk in enumerate(source,start=1):
            # {id: 实体的主键,distance:得分 0.8,entity:{chunk_id,content,title..}}
            # 计算当前chunk的得分
            # 获取chunk_id 
            chunk_id = chunk.get("id") or chunk.get("payload").get("chunk_id")
            # 计算得分 rrf权重版本的公式 = 1/k + rank * weight
            # rank从1开始，即为排名，传入的source已经是排名后的，第一个就是排名为1的向量块
            # 当有两个源时，第二个源会覆盖前一个，因此要将分数累加
            # 分数累加
            score_dict[chunk_id] = score_dict.get(chunk_id,0.0) + (1.0/(60 + rank)) * weight
            # chunk_dict[chunk_id] = chunk  #   新来的就覆盖前一份  保留一份
            chunk_dict.setdefault(chunk_id,chunk) # 用chunkid作为键，没有的时候才会添加，先判断有没有，有的话就不动，没有就保留
            # 效果上没有区别！ 每个chunk值保留一遍！
    # 4. 分和chunk的融合+排序
    merged = []  # 列表，里面存重排序之后的分数元组
    for chunk_id, score  in score_dict.items(): #  【key】chunk_id,【value】score
        chunk = chunk_dict.get(chunk_id) # 获取chunk内容
        merged.append((chunk,score))  # 将内容和分数放到元组里面
        # [(chunk1,score) , (chunk2,score)]
    merged.sort(key = lambda x:x[1],reverse=True) # 使用x里面的第二个元素排序也就是score
    # 5. 切指定的topk，取出前top_k个元素
    merged = merged[:top_k]
    # 6. 获取chunk的排名数据
    rank_chunks = [chunk  for chunk,score in merged]
    logger.info(f"完成了rrf排序处理完毕，结果为：{rank_chunks}")
    return rank_chunks

def node_rrf(state):
    """
    节点功能：Reciprocal Rank Fusion
    将多路召回的结果（向量、HyDE、Web、KG）进行加权融合排序。
    """
    print("---RRF---")
    add_running_task(state["session_id"], sys._getframe().f_code.co_name, state.get("is_stream"))

    # 1. 获取同源路的数据
    # 长啥样？？？？
    # milvus => [[],[]]  1. 单列查询  data = [向量1，向量2]   -> [[向量1],[向量2]]
    #                    2. 混合查询 reps = [anns....]      -> [[]]
    #                     [向量1]  =》 【{id: 实体的主键,distance:得分 0.8,entity:{chunk_id,content,title..}} ,{} ,{}】
    embedding_chunks = state.get("embedding_chunks")
    hyde_embedding_chunks = state.get("hyde_embedding_chunks")
    # 2. 数据进行整合 （捏到一起）
    # 权重后续方便动态调整！ 相同 1.0 1.0
    source_with_weight = [
        (embedding_chunks,1.0),
        (hyde_embedding_chunks,1.0)
    ]
    # 3. rrf算法进行数据排序处理
    rrf_response = step_3_reciprocal_rank_fusion(source_with_weight)
    # 4. 将排序后的数据添加到state [rrf_chunks] 属性即可
    state["rrf_chunks"] = rrf_response
    add_done_task(state['session_id'], sys._getframe().f_code.co_name, state.get("is_stream"))
    return state

# ================================
# 本地测试入口
# ================================
if __name__ == "__main__":
    print("\n" + "=" * 50)
    print(">>> 启动 node_rrf 本地测试")
    print("=" * 50)

    mock_state = {
        "session_id": "test_rrf_session",
        "is_stream": False,
        "original_query": "HAK 180 烫金机怎么操作？",
        "rewritten_query": "HAK 180 烫金机的具体操作步骤是什么？",
    }

    try:
        from app.rag.query_process.agent.nodes.node_search_embedding import node_search_embedding
        from app.rag.query_process.agent.nodes.node_search_embedding_hyde import node_search_embedding_hyde

        # 获取文档源的数据
        emb_res = node_search_embedding(mock_state)
        hyde_res = node_search_embedding_hyde(mock_state)
        # 赋值
        mock_state['embedding_chunks'] = emb_res.get("embedding_chunks") or []
        mock_state['hyde_embedding_chunks'] = hyde_res.get("hyde_embedding_chunks") or []

        result = node_rrf(mock_state)
        rrf_chunks = result.get("rrf_chunks", [])

        emb_cnt = len(mock_state.get("embedding_chunks") or [])
        hyde_cnt = len(mock_state.get("hyde_embedding_chunks") or [])

        print("\n" + "=" * 50)
        print(">>> 测试结果摘要:")
        print(f"输入数量: Embedding={emb_cnt}, HyDE={hyde_cnt}")
        print(f"输出数量: {len(rrf_chunks)}")
        print("-" * 30)

        print("最终排名:")
        for i, doc in enumerate(rrf_chunks, 1):
            doc_id = doc.get("chunk_id") or doc.get("id")
            content = (doc.get("content") or "")[:20]
            print(f"Rank {i}: ID={doc_id}, Content={content}...")

        print("=" * 50)

    except Exception as e:
        logger.exception(f"测试运行期间发生未捕获异常: {e}")
