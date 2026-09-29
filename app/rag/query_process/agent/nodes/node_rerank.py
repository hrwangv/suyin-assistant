import sys
from app.utils.task_utils import *

from dotenv import load_dotenv
import sys
from app.llm.reranker_utils import text_rerank
from app.core.logger import logger
from app.core.tracing import observe, update_current_span
from app.utils.task_utils import add_running_task
from app.conf.retrieval_config import retrieval_config

load_dotenv()
# -----------------------------
# Rerank / TopK 全局常量（不从 state 读取）
# -----------------------------
# 动态 TopK 硬上限：最多取前 N 条，默认 12，走配置
# （召回阶段已经放到 20 条候选，这里再挑出最相关的若干条进 prompt）
RERANK_MAX_TOPK: int = retrieval_config.rerank_max_topk
# 最小 TopK：至少保留前 N 条（>=1，且 <= RERANK_MAX_TOPK）
RERANK_MIN_TOPK: int = 1
# 断崖阈值（相对）
RERANK_GAP_RATIO: float = 0.25  # （前分-后分）/ 前分
# 断崖阈值（绝对）
RERANK_GAP_ABS: float = 0.5 # 最大间断分值  只要两个分值差距大于0.5，就舍弃后面的


def step_1_merge_rrf_mcp(state):
    """
    进行rrf + mcp数据整合
    :param state:
    :return:
    """
    # 1. state获取不同路的数据
    rrf_chunks = state.get("rrf_chunks",[])
    web_search_docs = state.get("web_search_docs",[])
    # 2. 准备一个列表容器
    chunks_list = []
    # 3. 循环进行数据添加
    # 3.1 local rrf
    for chunk in rrf_chunks:
        # chunk {id,score,payload}
        payload = chunk.get('payload') or {}
        id = chunk.get('id')
        content = payload.get('content')
        title = payload.get('title')
        chunks_list.append({
            # 先把 payload 里的元数据整体透传（content 除外：正文走 text 字段，
            # 避免同一份内容在 dict 里存两遍）。
            # 为什么不能只挑 title/content：news_date / file_title / section / item_name
            # 这些字段在入库时就构建好了、检索阶段还用于过滤，之前在这一步被丢掉，
            # 导致回答阶段既看不到日期也看不到出处，答案无法标注时间、也无法溯源。
            **{key: value for key, value in payload.items() if key != "content"},
            "chunk_id": id,
            "text": content,
            "title": title,
            "source": "local",
            # 这里过去硬编码成 " "，而联网块带真实 category，两路信息量不对称；
            # 现在用 payload 里的真实值（没有就留空字符串）
            "category": payload.get("category") or "",
        })
    # 3.2 web   mcp
    for doc in web_search_docs:
        text = doc.get("summary")
        category  = doc.get("category")
        title = doc.get("title")
        chunks_list.append({
            # 同本地块：整体透传（summary 走 text，避免重复）。
            # 以前这里只保留 title/category，date（资讯时间）和 company_name（主体）
            # 都被丢掉了，模型因此无法判断联网资讯的时效性。
            **{key: value for key, value in doc.items() if key != "summary"},
            "chunk_id": " ",
            "text": text,
            "title": title,
            "source": "web",
            "category": category
        })

    logger.info(f"多路数据融合，最终结果为:{chunks_list}")
    return chunks_list


def step_2_rerank_doc_list(doc_list, state):
    """
    使用rerank进行精排
    :param doc_list:
    :param state:
    :return:
    """
    # 1. 获取原有的问题
    rewritten_query = state.get("rewritten_query") or state.get("original_query")
    # 2. 获取问题对应的所有答案 
    text_list = [ doc['text'] for doc in doc_list]

    # 3. 使用rerank模型,重新打分排序，两个必填入参分别是要查询的问题。以及需要重排序备选的文档
    # top_n 传「全部候选」：让召回池里每一条都拿到真实分数。
    # 为什么必须传全部：top_n 只控制返回条数，不控制打分范围（成本也是按全部文档算的）。
    # 如果只取前 N 条，池子里第 N+1 名之后会被赋 0 分沉底，
    # step_3_topk_and_gap 就会在「已打分 / 未打分」的边界上误判一次断崖，
    # 等于把「按分数分布截断」退化成「硬切 N 条」，宽召回也白做了。
    # 最终进 prompt 的条数由 step_3_topk_and_gap 的「断崖 + RERANK_MAX_TOPK 上限」决定。
    result = text_rerank(rewritten_query, text_list, top_n=len(text_list))
    '''
    "results": 
    [
    {"index": 0, "relevance_score": 0.9247129722205545, "document": {"text": "文本排序模型广泛用于搜索引擎和推荐系统中，它们根据文本相关性对候选文本进行排序"}}, 
    {"index": 2, "relevance_score": 0.7579385108464249, "document": {"text": "预训练语言模型的发展给文本排序模型带来了新的进展"}}, 
    {"index": 1, "relevance_score": 0.2772209839843891, "document": {"text": "量子计算是计算科学的一个前沿领域"}}
    ]
    '''

    # 建立索引到分数的映射
    # {index,score}
    score_map = {item.index: item.relevance_score for item in result}

    # 为每个原始文档添加分数（按其在 doc_list 中的位置索引）
    for idx, item in enumerate(doc_list):
        item['score'] = score_map.get(idx, 0.0)  # 若缺失则给0分（理论上不会）

    # 按分数从高到低排序
    doc_list_with_score = sorted(doc_list, key=lambda x: x['score'], reverse=True)
    #     [
    #      {
    #            text:内容 snippet content,
    #            chunk_id: chunk_id rrf有  mcp None,
    #            title: title ,
    #            category : rrfNone mcp url ,
    #            source: web -> mcp  || local -> rrf ,
    #            score: rerank打的分
    #      }
    #    ]
    logger.info(f"已完成排序和打分！最终结果为：{doc_list_with_score}")
    return doc_list_with_score


def step_3_topk_and_gap(rerank_score_list):
    """
    对rerank模型打分以后得有序集合进行再次算法筛选！
    取出动态的topk元素即可

    问题稠密和稀疏向量           =   （问题进行向量混合搜索）
                                                                    =》 rrf(同源的排序 rank + weight ) => rrf
    问题+假设性答案稠密和稀疏向量  =   （问题+假设性答案进行向量混合搜索）                                                => rerank => 算法 =》 topk
                                                                                                     => mcp
    :param rerank_score_list:
    :return:
    """

    max_topk  = RERANK_MAX_TOPK   # 至多获取的元素的数量
    min_topk  = RERANK_MIN_TOPK   # 至少获取的元素数量，怎么都要获取，防断崖也要获取
    gap_abs   = RERANK_GAP_ABS    # 绝对差阈值：断崖的分差    0.9  0.64 =》 0.26 （分）
    gap_ratio = RERANK_GAP_RATIO  # 绝对比阈值：断崖的百分比  （1-2）/ 1  =》 0.25 保留
    # 思路： 两个对比 1 2    2 3  3 4  4 5 （双指针）
    # 1.思考最大截取数量
    # topk不应该大于列表长度
    topk = min(max_topk, len(rerank_score_list))
    # 2.循环处理数据列表，进行双指针处理和比较，比较分值
    if topk > min_topk:  # 正常情况，要得到的topk 大于最小要求，也就是需要防断崖处理
        # min-1 , topk -1
        for index in range(min_topk - 1, topk - 1): # 只需要在最小输出条数和最大输出条数之间循环即可
            # 双指针 【前，后】
            score_1 = rerank_score_list[index].get("score",0.0) # 当前分数
            score_2 = rerank_score_list[index+1].get("score",0.0) # 下一条分数
            # 算分数的差值 gap
            gap = score_1 - score_2 # 当前分数减去后一条分数
            # 0.9 0.8  0.1 / 0.9
            # 除法 分母不能为 为0  1e-6 防止为 0
            #  score_1 = -0.5  score_2 = - 0.8
            #  0.3 / -0.5 =  -rel
            #  abs = rel 正值
            rel = gap / (abs(score_1) + 1e-6)
            if gap >= gap_abs or rel >= gap_ratio: # 如果两者之间的值大于断崖阈值/大于断崖百分比
                # 触发断崖
                logger.info(f"数据集合{index}和{index+1}的位置发生了断崖，结束循环！！")
                topk = index + 1 # index下标从0开始 topk对应的截取长度从1
                break # 跳出循环
    #else:
        # min_topk = topk  不用管，正好数量对上
        # min_topk 3 > topk 0  list 0 

    # 3.截取确定的数量topk，断崖后，取出topK的数量大大减少
    topk_doc_list = rerank_score_list[:topk]
    # 4.打印日志处理
    logger.info(f"最终截取的长度：{topk},截取的内容:{topk_doc_list}")
    # 5.返回结果
    return topk_doc_list

@observe(name="node:rerank", as_type="span", capture_input=False, capture_output=False)
def node_rerank(state):
    """
    节点作用： rrf + mcp -> 精排序 rerank -> chunk - 打分  -> 算法 -> top k
    算法理解： 算法 （最多10条 最少1条  相对0.25 绝对 0.5）
             0.93 0.91  0.90 [断崖]  0.39  -》 top 3
             0.6 0.5 0.4 .....  [归一化 0 -1 ] -》 top 10
             0.4 (0.1) / 0.4    ||  0.3 (0.1/0.3)  0.2   -》 top 2
    节点功能：使用 Cross-Encoder 模型对 RRF 后的结果进行精确打分重排。
    """
    print("---Rerank处理---")
    add_running_task(state["session_id"], sys._getframe().f_code.co_name, state.get("is_stream"))
    update_current_span(
        input={
            "rewritten_query": state.get("rewritten_query"),
            "rrf_chunks": len(state.get("rrf_chunks") or []),
            "web_docs": len(state.get("web_search_docs") or []),
        }
    )
    
    """
    约定返回值
      [
         rrf = {id:chunk_id,score:0.x,payload:{chunk_id,content,title}}
         mcp = [{date: 日期, category: 类别, title:标题, summary:摘要, }]
         {
            text:内容 summary/ content,
            chunk_id: chunk_id rrf有  mcp None,
            title: title ,
            category : rrfNone mcp url , ?
            source: web -> mcp  || local -> rrf 
         }
      ]
    
    """
    # 1. 非同源路的结果合并 （rrf + mcp） 捏到一个集合中,形成原始的chunk集
    doc_list = step_1_merge_rrf_mcp(state)
    # 2. 启用rerank进行精排 （数据和分）
    """
    [
      {
            text:内容 snippet content,
            chunk_id: chunk_id rrf有  mcp None,
            title: title ,
            url : rrfNone mcp url ,
            source: web -> mcp  || local -> rrf ,
            score: rerank打的分 
      }
    ]
    """
    rerank_score_list = step_2_rerank_doc_list(doc_list,state)
    # 3. 启动算法进行放断崖以及topk处理  0.9  0.89  0.35
    # 切割后的数据列表
    final_doc_list = step_3_topk_and_gap(rerank_score_list)
    # 4. 结果装到state中即可
    state["reranked_docs"] = final_doc_list
    update_current_span(
        output={
            "reranked_docs": len(final_doc_list),
            "top": [
                {
                    "score": round(float(doc.get("score") or 0), 4),
                    "source": doc.get("source") or "",
                    "title": doc.get("title") or "",
                }
                for doc in final_doc_list[:5]
            ],
        }
    )
    add_done_task(state['session_id'], sys._getframe().f_code.co_name, state.get("is_stream"))
    return state


if __name__ == "__main__":
    print("\n" + "=" * 50)
    print(">>> 启动 node_rerank 本地测试")
    print("=" * 50)

    # 1. 模拟数据
    # 1.1 RRF 本地文档数据
    mock_rrf_chunks = [
        {"entity":{"chunk_id": "local_1", "content": "RRF是一种倒数排名融合算法", "title": "算法介绍", "score": 0.9}},
        {"entity":{"chunk_id": "local_2", "content": "BGE是一个强大的重排序模型", "title": "模型介绍", "score": 0.8}},
        {"entity":{"chunk_id": "local_3", "content": "无关的测试文档内容", "title": "测试文档", "score": 0.1}}  # 预期低分
    ]

    # 1.2 MCP 联网搜索数据
    mock_web_docs = [
        {"title": "Rerank技术详解", "url": "http://web.com/1", "snippet": "Rerank即重排序，常用于RAG系统的第二阶段"},
        {"title": "无关网页", "url": "http://web.com/2", "snippet": "今天天气不错，适合出去游玩"}  # 预期低分
    ]

    mock_state = {
        "session_id": "test_rerank_session",
        "rewritten_query": "什么是RRF和Rerank？",  # 查询意图：想了解这两个算法
        "rrf_chunks": mock_rrf_chunks,
        "web_search_docs": mock_web_docs,
        "is_stream": False
    }

    try:
        # 运行节点
        result = node_rerank(mock_state)
        reranked = result.get("reranked_docs", [])

        print("\n" + "=" * 50)
        print(">>> 测试结果摘要:")
        print(f"输入文档总数: {len(mock_rrf_chunks) + len(mock_web_docs)}")
        print(f"输出文档总数: {len(reranked)}")
        print("-" * 30)

        print("最终排名:")
        for i, doc in enumerate(reranked, 1):
            print(f"Rank {i}: Source={doc.get('source')}, Score={doc.get('score'):.4f}, Text={doc.get('text')[:20]}...")

        # 验证逻辑：
        # 预期 "local_1", "local_2", "Rerank技术详解" 分数较高
        # 预期 "local_3", "无关网页" 分数较低，可能被截断或排在最后

        top1_score = reranked[0].get("score")
        if top1_score > 0:
            print("\n[PASS] Rerank 打分正常")
        else:
            print("\n[FAIL] Rerank 打分异常 (均为0或负数)")

        print("=" * 50)

    except Exception as e:
        logger.exception(f"测试运行期间发生未捕获异常: {e}")
