import sys
import os
import json
import logging
from datetime import date
from typing import List, Dict, Any, Optional
from langchain_core.messages import SystemMessage, HumanMessage
#from mpmath import limit
from app.conf.llm_config import lm_config
from app.conf.qdrant_config import qdrant_config
from app.conf.retrieval_config import retrieval_config
from app.core.load_prompt import load_prompt
from app.rag.query_process.agent.state import QueryGraphState
from app.utils.task_utils import add_running_task, add_done_task

from app.llm.lm_utils import get_llm_client
from app.llm.qwen_embedding_utils import generate_embeddings
from app.utils.qdrant_utils import get_qdrant_client, rrf_hybrid_search
# 结构化检索请求：Query Analyzer 的产物定义 + 日期解析 + Filter 构造
# 三者都是纯 Python 逻辑（不依赖大模型和数据库），可以单独测试
from app.rag.query_process.retrieval.schemas import RetrievalQuery, KNOWN_CATEGORIES
from app.rag.query_process.retrieval.date_resolver import DateResolver
from app.rag.query_process.retrieval.qdrant_filter_builder import describe_filters
from app.conf.item_name_config import item_name_config
# 统一的 token 预算工具（与长期记忆抽取、回答 prompt 总控同一套方法）
from app.utils.token_budget import fit_token_budget
from app.conf.memory_config import MAX_TOKEN_WINDOW_SIZE
from app.memory.recent_message_service import get_recent_message_service
from app.memory.utils.scope import build_scope
from dotenv import load_dotenv,find_dotenv
from app.core.logger import logger
from app.core.tracing import llm_config, observe, update_current_span

load_dotenv(find_dotenv())


@observe(name="query-analyzer", as_type="chain", capture_output=True)
def step_3_analyze_query(original_query, history_chats):
    """
    Query Analyzer：一次大模型调用，同时产出两样东西
      1. rewritten_query   → 用于向量化检索的语义查询
      2. filters           → 结构化的确定性过滤条件

    改造要点：
    - 大模型只负责「理解自然语言 + 抽取条件」，不生成任何 Qdrant 查询语句；
    - 相对时间（今年上半年/最近三个月）由模型输出 date_expression，
      具体日期交给 Python 的 DateResolver 计算，避免模型算错日期；
    - 模型输出不可信，所有条件都要经过校验，非法值一律丢弃。

    :param original_query: 用户原有的提问
    :param history_chats:  聊天记录
    :return: { rewritten_query: str, filters: dict }
    """
    # 1. 准备提示词
    history_text = ""
    for chat in history_chats:
        history_text += (
            f"聊天角色：{chat['role']}，"
            f"回答内容：{chat['content']}，"
            f"时间：{chat.get('ts') or ''}\n"
        )

    # 将提问与历史记录与模型提示词结合，生成最终的重写后的问题
    # 额外注入两个变量：
    #   today      —— 让模型能理解"今年/本月"这类相对表达（它只输出表达式，不换算）
    #   categories —— 把真实的分类枚举值喂给模型，减少它自己瞎编分类
    prompt = load_prompt(
        "query_analyzer",
        history_text=history_text,
        query=original_query,
        today=date.today().isoformat(),
        categories="、".join(sorted(KNOWN_CATEGORIES)),
    )
    # 2. 模型调用
    lm_client = get_llm_client(model_type=lm_config.llm_model,json_mode=True)
    # system -> 模型的角色边界！ -> 应该是不变！  【角色，规则，格式】
    # user  ->  每次任务提示 -》 多条动态调整！  【提问/聊天】
    # 事实上，你嫌麻烦，你可以把模型的角色和边界写到user 功能也是完全一样！！
    messages = [
        HumanMessage(content=prompt)
    ]
    # config 里挂 Langfuse callback（未开启追踪时是空 dict，行为不变）
    response = lm_client.invoke(messages, config=llm_config())
    # 怎么确保，模型一定能返回格式化数据？ 【面试题】
    # json!  1.设置json格式化！   2. 提示词中明确   3. 一定要给模型参考示例  4. 做好返回格式的校验
    # 3. 结果解析
    content = response.content.strip()
    # 确保处于json，格式化输出模式，去掉 ```json 以及 ``` 代码块标记
    if content.startswith("```json"):
        content = content.replace("```json","").replace("```","")
    try:
        dict_content = json.loads(content) # 将大模型识别转成字典
    except (ValueError, TypeError) as e:
        # 模型偶尔会返回非 JSON（哪怕开了 json_mode）。这里降级处理：
        # 用原问题当语义查询、不带任何过滤条件，保证主流程不中断。
        logger.warning(f"Query Analyzer 返回内容无法解析成 JSON，降级为原问题检索：{e}；原始内容={content[:200]}")
        dict_content = {}

    # 4. 结构化解析 + 校验（模型输出不可信，统一在这里归一化）
    retrieval_query = RetrievalQuery.from_llm(dict_content, original_query=original_query)

    # 5. 相对时间 → 绝对日期区间。
    # 这是「大模型不做日期算术」这条原则的落地：模型只给"今年上半年"，
    # 具体是哪天到哪天由 Python 算，算不出来就保持不带时间条件（宁可不筛，不要筛错）。
    retrieval_query.filters = DateResolver().resolve(retrieval_query.filters)

    # 5.1 导入阶段的主体识别被关闭时，payload 里的 item_name 只是文件名兜底值，
    # 拿"欣旺达"这类主体去做等值过滤必然一条都匹配不到。这里直接丢掉这个条件，
    # 避免一次注定为空的检索；主体名仍然留在 rewritten_query 里参与语义检索。
    if not item_name_config.enabled and retrieval_query.filters.item_name:
        logger.info(
            f"主体识别已关闭，忽略 item_name 过滤条件：{retrieval_query.filters.item_name}"
        )
        retrieval_query.filters.item_name = None

    # 6. 封装返回（filters 存成 dict，方便写进 state 和日志序列化）
    result = {
        "rewritten_query": retrieval_query.rewritten_query,
        "filters": retrieval_query.filters.to_dict(),
    }
    # 7. 检索决策日志（改造文档第 20 节）：
    # 出问题时靠这一行就能区分是「模型没抽出条件」还是「条件构造错了」
    logger.info(
        f"Query Analyzer 完成！原始问题：{original_query} | 改写后：{result['rewritten_query']} "
        f"| 过滤条件：{describe_filters(result['filters'])}"
    )
    return result


def step_4_query_milvus_item_names(item_names):
    """
     查询向量数据库 进行item_name的确定
    :param item_names: 模型识别提取的item_name可能不准
    :return:
        List[ Dict1{key传入的要匹配的itemname: value查到的List[{匹配到的itemname,匹配分数},{}]}, Dict2{key: value} ]
           [{extracted:模型item_name, matches:[{item_name:xx,score:0.9...}]}]
    """
    # 明确 我们一定做的混合查询 （稠密向量 + 稀疏向量）
    final_result = []
    # 1. 获取qdrant的客户端
    qdrant_client = get_qdrant_client()
    # 2. 将item_name转成向量（稠密和稀疏）【循环】
    embeddings = generate_embeddings(item_names)
    # 3. 混合查询 通过
    for index,item_name in enumerate(item_names):
        # 1. 获取当前item_name对应的向量
        dense_vector = embeddings["dense"][index] # List[float]
        sparse_vector = embeddings["sparse"][index] # Dict{int: float}

        # 混合search查询
        response = rrf_hybrid_search(
                    client=qdrant_client,
                    collection_name=qdrant_config.item_name_collection,
                    dense_vector=dense_vector,
                    sparse_vector=sparse_vector,
                    # 主体名匹配也是「一路召回」，同样由调用方把候选数量传进去；
                    # 这里沿用和正文检索一致的池子大小，保持改造前行为不变
                    limit=retrieval_config.fused_limit,
                    dense_limit=retrieval_config.prefetch_limit,
                    sparse_limit=retrieval_config.prefetch_limit
                    )

        """
        {
            "usage": {   # 本次查询消耗的资源
                "hardware": {
                "cpu": 1,
                "payload_io_read": 1,  # 对 payload 数据的 I/O 读取次数
                "payload_io_write": 1, # 对 payload 数据的 I/O 写入次数
                "payload_index_io_read": 1,  # 对索引读取次数
                "payload_index_io_write": 1,
                "vector_io_read": 1, # 对向量数据的 I/O 读取次数
                "vector_io_write": 1
                },
                "inference": {  # 推理模型的使用情况。
                "models": {}    # 为空 {} 表示本次请求没有使用任何推理模型 
                }
            },
            "time": 0.002,
            "status": "ok",
            "result": {
                "points": [
                {
                    "id": 42,
                    "version": 3,  # 内部版本号，表示该点的更新次数
                    "score": 0.75, # 当前点与查询向量的相似度分数，范围 [0, 1]，越接近 1 表示越相似
                    "payload": {}, # 当前点的负载数据，包含自定义的字段信息 
                    "vector": {},  
                    "shard_key": "region_1",  # 该点所属的分片标识
                    "order_value": 42  # 用于排序查询的排序值（在基于范围或顺序的查询中使用）
                }
                ]
            }
        }

        返回形式如下，需要从response中提取出item_name和score，封装成如下格式：
        [   {extracted:（模型提取的item_name）, 
            matches:[
                {item_name:名字,score:0.8},
                {item_name:名字,score:0.8}
                ]
            },
            {extracted:（模型提取的item_name）, 
            matches:[
                {item_name:名字,score:0.8},
                {item_name:名字,score:0.8}
                ]
            }
        ] 
        
        """
        # 3. 格式化单个结果
        formatted = format_search_response(response, query_item_name=item_name)
        final_result.append(formatted)
    
    # 封装返回数据
    logger.info(f"查询向量数据库结果为：{final_result}")
    return final_result



def format_search_response(raw_response: Dict[str, Any], query_item_name: str) -> Dict[str, Any]:
    """
    将 Qdrant 查询响应的字典结果转换为目标格式
    :param raw_response: Qdrant 返回的原始字典（包含 usage, time, status, result）
    :param query_item_name: 本次查询的物品名称（对应 extracted 字段）
    :return: {"extracted": query_item_name, "matches": [{"item_name": ..., "score": ...}]}
    """
    # 注意：raw_response 可能是 QueryResponse 对象，转换为字典以便通用处理
    if hasattr(raw_response, "dict"): # 如果不是字典格式对象转化一下
        raw_response = raw_response.dict()
    # 安全提取 points
    points = raw_response.get("result", {}).get("points", [])
    matches = []
    
    for point in points:
        # 1. 提取得分（float）
        score = point.get("score")
        # 2. 从 payload 中提取 item_name
        payload = point.get("payload", {})
        # 根据您的 payload 结构，直接取 "item_name"
        item_name = payload.get("item_name")
        # 如果 payload 中没有 item_name，则降级使用点 id
        if item_name is None:
            item_name = point.get("id")  # 可能是整数或字符串
        
        matches.append({
            "item_name": item_name,
            "score": score
        })
    
    return {
        "extracted": query_item_name, 
        "matches": matches
    }



def step_5_confirmed_and_optional_item_name(query_milvus_results):
    """
    通过向量数据库查询的item_name,根据分数归纳出确定和可选的item_name列表
    :param query_milvus_results: 元数据 
    [{extracted:item_name,matches:[{item_name: , score:},{}]}
                                       ,{extracted:item_name,matches:[{item_name: , score:},{}]}]
    :return:
          {
             confirmed_item_names:[确定item_name], 分高
             options_item_names:[可选item_name]  分低
          }
    评分规则：
          确定item_name
          0.85 【根据权重和数据分析进行调整】
          可选item_name
          0.60
          忽略
    思路： 1. 循环处理每个item_name列表和分  
    2. 高分 只要1个  可选 可以要2个   
    3. 不区分extracted:item_name。将他们都装到对应的 确认或者可选集合中


    """
    #1. 准备两个列表 确认 可选的
    confirmed_item_names = [] #确定
    options_item_names = [] #可选
    #2. 循环处理元数据 query_milvus_results
    for item_name_meta in query_milvus_results:
        extracted_name = item_name_meta.get("extracted")
        matches = item_name_meta.get("matches",[]) 
        #3. 进行分数排序（倒序） || 列表推导式 提取0.85 || 0.6
        # matches [{score: xx , item_name="" }]
        # 从列表里取分数，排序，倒序
        matches.sort(key=lambda x:x.get("score",0),reverse=True)
        # >= 0.8 [{item_name: , score:},{}]
        # 处理 -》 先处理高分 -》 有 -》 正常执行 || 如果没有 -》 才处理低分
        # 处理高分
        high_score_matches = [ x for x in matches if x.get("score",0) >= 0.85 ]
        # 处理低分
        middle_score_matches = [ x for x in matches if x.get("score",0) >= 0.6]
        #4. 处理高分的列表 只有一个1  获取一个1  ||  多个【item_name = extracted 】 or 获取最高分的1个
        # 4.1 只有一个，获取一个
        if len(high_score_matches) ==1:
            confirmed_item_names.append(high_score_matches[0].get("item_name"))
            continue
        # 4.2 有多个高分
        if len(high_score_matches) >1:
            # 同一个名 = 分不是1 也可能不是最高！！
            # 优先考虑名字相同
            same_name_item = None
            for item in high_score_matches:
                if item.get("item_name") == extracted_name:
                    same_name_item = item
                    break
            if not same_name_item:
               same_name_item = high_score_matches[0] #获取分数最高的
            confirmed_item_names.append(same_name_item.get("item_name"))
            continue
            # 没有相同获取分数最高的
        #5. 处理可选分数列表 给用户返回提示，可以多带几个
        if len(middle_score_matches) > 0:
            for item in middle_score_matches[:2]: # 最多取2个
                options_item_names.append(item.get("item_name"))
            continue
        logger.info(f"没有匹配的item_name，忽略：{extracted_name}")
    #6. 处理返回结果即可(去重复)
    result = {
        "confirmed_item_names":list(set(confirmed_item_names)),
        "options_item_names":list(set(options_item_names))
    }
    logger.info(f"处理结果为：{result}")
    return result


def step_6_deal_list(state,item_results, history_chats,rewritten_query):
    """
    根据集合类型中数据，判定是否要赋值answer内容
    :param item_results:   # result = {
        #         "confirmed_item_names":list(set(confirmed_item_names)),
        #         "options_item_names":list(set(options_item_names))
        #     }
    :param history_chats:
           [
           ]
    :return:
    """
    # 1. 先获取两个集合 （确认 | 可选的）
    confirmed_item_names = item_results.get("confirmed_item_names",[])
    options_item_names = item_results.get("options_item_names",[])
    # 2. 确认集合有数据 （处理）
    if len(confirmed_item_names) > 0:
        # 2.1 更新下聊天记录 -》 item_names - > confirmed_item_names (空着)
        # 2.2 修改和存储state状态
        state['item_names'] = confirmed_item_names
        state['rewritten_query'] =rewritten_query
        state['history'] = history_chats
        if "answer" in state:
            del state['answer']
        logger.info(f"有确定的item_name:{confirmed_item_names}")
        return state
    # 3. 确认集合没数据，处理可选集合
    if len(options_item_names) > 0:
        option_names = '、'.join(options_item_names)
        answer = f"您是想咨询以下哪个主体：{option_names}，请尝试给我们更多信息"
        state['answer'] = answer
        logger.info(f"有可选的item_name:{options_item_names}")
        return state
    # 4. 确认和可选集合都没数据 （处理）
    answer = "没有获取匹配信息，请重新提问"
    state['answer'] = answer
    logger.info(f"没有匹配的的item_name")
    return state

@observe(name="node:item_name_confirm", as_type="span", capture_input=False, capture_output=False)
def node_item_name_confirm(state):
    """
    节点功能：Query Analyzer —— 提问后的第一个大模型节点。
    # 核心目标： 1. 重写用户问题（补全指代、去掉口语，提高检索召回）
    #           2. 抽取结构化过滤条件（时间/分类/领域/主体）
    # 核心参数： state['original_query' -> 用户的原问题 ]  ||  session_id
    # 响应数据： rewritten_query: str  # 改写后的问题
    #          retrieval_filters: dict  # 结构化过滤条件
    #          history: list  # 历史对话记录
    # 当前策略：
    # 不再用 item_name 向量检索分数作为“是否继续检索”的门槛，
    # 也不在入口区分“确认/可选/置信度”。是否值得回答、是否需要用户澄清，
    # 统一交给后续检索、rerank 和答案生成节点处理。
    #
    # 原来的 step_4/step_5/step_6 仍保留在文件中作为备份，但主流程不再调用。
    """
    print(f"---node_item_name_confirm---开始处理")
    # 记录任务开始
    add_running_task(state["session_id"], sys._getframe().f_code.co_name,state["is_stream"])
    update_current_span(
        input={
            "original_query": state.get("original_query") or state.get("request_text") or "",
            "history": len(state.get("history") or []),
        }
    )

    #  1. 统一用 scope 作为最近消息维度，取出最近的对话
    memory_service = get_recent_message_service() # 获取短期记忆
    scope = build_scope(run_id=state["session_id"])
    #  2. 自己做 token 预算控制（不再依赖 get_context 那套自带裁剪）：
    #     Analyzer 这次调用的输入就是「历史 + 问题」，历史必须在这里卡住上限，
    #     因为后面回答节点的总控管的是另一次调用，兜不到这里。
    #     超额从最老的一条开始丢，保留最近的对话。
    #     读失败（MySQL 回源挂了之类）降级成「无历史」继续，不能让短期记忆
    #     拖垮整个问答：Redis 是 L1，MySQL 只是回源兜底。
    try:
        history_chats, dropped_history = fit_token_budget(
            memory_service.get_messages(scope),
            MAX_TOKEN_WINDOW_SIZE,
            drop="oldest",
        )
    except Exception as exc:
        logger.warning(f"读取短期记忆失败，本轮按无历史继续：{exc}")
        history_chats, dropped_history = [], 0
    if dropped_history:
        logger.info(
            f"Query Analyzer 历史超预算，已丢弃最老的 {dropped_history} 条"
            f"（预算 {MAX_TOKEN_WINDOW_SIZE} token）"
        )
    # print(history_chats)
    #  3. 利用模型lm -> 
    # 1. 重写提问内容  2. 抽取结构化过滤条件
    #  参数： state["original_query"] || history_chats
    #  响应： { rewritten_query : str, filters : {...} }
    #  1. 为啥问题要重写？需要去意图识别【面试tips】
    """          
       1. 消除指代歧义    他 Ta 它 不明确！  明确查询主体 item_name
       2. 补全上下文     他的问题需要有历史记录支持！ 
       3. 去掉口语和冗余  同学们 同志们  为啥 咋弄 完犊子了 
       4. 润色问题增加召回率  模型查询的时候也会更精准
    """
    analysis = step_3_analyze_query(state["original_query"], history_chats)
    rewritten_query = analysis.get("rewritten_query", "")
    # 结构化过滤条件（Query Analyzer 产出，已经过 Python 校验和日期解析）。
    # 这里只负责挂在 state 上，真正的 Qdrant Filter 由检索节点用
    # QdrantFilterBuilder 构造 —— 保持「LLM 抽条件 / Python 造查询」的边界。
    retrieval_filters = analysis.get("filters") or {}
    state['rewritten_query'] = rewritten_query
    state['retrieval_filters'] = retrieval_filters
    state['history'] = history_chats
    # 出参：改写后的问题 + 抽出来的结构化过滤条件，是排查「检索为什么跑偏」的第一现场
    update_current_span(
        output={
            "rewritten_query": rewritten_query,
            "retrieval_filters": retrieval_filters,
            "history_used": len(history_chats),
            "history_dropped": dropped_history,
        }
    )
    # 确保不会因为历史 state 残留 answer 而跳过检索。
    if "answer" in state:
        del state['answer']

    # =====================================================================
    # 备份逻辑：原先的“查询向量库 -> 确认/可选分档 -> 提前生成澄清答案”。
    # 主流程暂不调用，后续如需恢复可取消下面注释。
    # 注：这套逻辑依赖 Query Analyzer 额外产出 item_names，现在已不再提取；
    # 若日后恢复实体对齐，需要先把 item_names 的抽取加回去。
    # =====================================================================
    # item_results = {}
    #7. 记录本次的聊天对话 （answer回答）
    #   写失败只记 error 不上抛：短期记忆是增强能力，不能因为它写不进去就
    #   让用户拿不到答案（service 内部已降级，这里再兜一层，保证契约显式）
    try:
        memory_service.add_messages(
            scope,
            [
                {
                    "role": "user",
                    "content": state["original_query"],
                }
            ],
        )
    except Exception as exc:
        logger.error(f"写入用户消息失败（忽略，不影响本轮问答）：{exc}")
    # 记录任务结束
    add_done_task(state["session_id"], sys._getframe().f_code.co_name,state["is_stream"])
    print(f"---node_item_name_confirm---处理结束")

    return state



if __name__ == "__main__":
    # 模拟输入状态
    mock_state = {
        "session_id": "test_session_001",
        "original_query": "经营晨报",
        "is_stream": True 
    }

    print(">>> 开始测试 node_item_name_confirm...")
    try:
        # 运行节点
        result_state = node_item_name_confirm(mock_state)

        print("\n>>> 测试完成！最终状态:")
        # 遇到不认识的对象，直接把它转成字符串输出！
        print(json.dumps(result_state, indent=2, ensure_ascii=False,default=str))
        # 简单验证
        if result_state.get("rewritten_query"):
            print(f"\n[PASS] 成功改写问题: {result_state['rewritten_query']}")
        else:
            print("\n[WARN] 未得到改写后的问题")

    except Exception as e:
        logger.exception("==========")
