import sys
import os
import json
import logging
from typing import List, Dict, Any, Optional
from langchain_core.messages import SystemMessage, HumanMessage
#from mpmath import limit
from app.conf.llm_config import lm_config
from app.conf.qdrant_config import qdrant_config
from app.core.load_prompt import load_prompt
from app.rag.query_process.agent.state import QueryGraphState
from app.utils.task_utils import add_running_task, add_done_task
from app.utils.mongo_history_utils import get_recent_messages, save_chat_message, update_message_item_names
from app.llm.lm_utils import get_llm_client
from app.llm.qwen_embedding_utils import generate_embeddings
from app.utils.qdrant_utils import get_qdrant_client, rrf_hybrid_search
from dotenv import load_dotenv,find_dotenv
from app.core.logger import logger

load_dotenv(find_dotenv())


def step_3_llm_item_name_and_rewrite_query(original_query, history_chats):
    """
    根据历史记录 通过调模型
    -》 识别item_names 和 重写问题
    :param original_query: 用户原有的提问
    :param history_chats:  聊天记录
    :return:  {  item_name = [] , rewritten_query:问题
              }
    """
    # 1. 准备提示词
    history_text = ""
    for chat in history_chats:
        history_text += f"聊天角色：{chat['role']}，回答内容： {chat['text']}，重写问题： {chat['rewritten_query']}，关联主体： {','.join(chat.get('item_names',[]))},时间： {chat['ts']}\n"

    # 将提问与历史记录与模型提示词结合，生成最终的重写后的问题
    prompt = load_prompt("rewritten_query_and_itemnames",history_text=history_text,query= original_query)
    # 2. 模型调用
    lm_client = get_llm_client(model_type=lm_config.llm_model,json_mode=True)
    # system -> 模型的角色边界！ -> 应该是不变！  【角色，规则，格式】
    # user  ->  每次任务提示 -》 多条动态调整！  【提问/聊天】
    # 事实上，你嫌麻烦，你可以把模型的角色和边界写到user 功能也是完全一样！！
    messages = [
        HumanMessage(content=prompt)
    ]
    response = lm_client.invoke(messages)
    # 怎么确保，模型一定能返回格式化数据？ 【面试题】
    # json!  1.设置json格式化！   2. 提示词中明确   3. 一定要给模型参考示例  4. 做好返回格式的校验
    # 3. 结果解析
    content = response.content
    # 确保处于json，格式化输出模式，去掉 ```json 以及 ``` 代码块标记
    if content.startswith("```json"):
        content = content.replace("```json","").replace("```","")
    dict_content = json.loads(content) # 将大模型识别转成字典
    print (dict_content)

    if "item_names" not in dict_content:
        dict_content["item_names"] = []
    if "rewritten_query" not in dict_content:
        dict_content["rewritten_query"] = original_query # 原提问
    # 4. 封装返回
    logger.info(f"已经完成问题的重写和item_name的提取！ 结果为：{dict_content}")
    return  dict_content


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

        response = rrf_hybrid_search(
                    client=qdrant_client,
                    collection_name=qdrant_config.item_name_collection,
                    dense_vector=dense_vector,
                    sparse_vector=sparse_vector    
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

def node_item_name_confirm(state):
    """
    节点功能：确认用户问题中的核心商品名称。
    # 核心目标： 1. 提取【 item_name 】 （大模型从历史对话 + 本次提问 提取  -》 item_name -> 向量库搜索 ->  打分 -》 ABC）
               2. 利用模型重写用户的问题，确保后续查询召回率更高！！！
    # 核心参数： state['original_query' -> 用户的原问题 ]  ||  session_id
    # 响应数据： item_names: List[str]  # 提取出的商品名称
    #          rewritten_query: str  # 改写后的问题
    #          history: list  # 历史对话记录
    #          answer : 可选的答案
        1. 获取历史条件记录（作为依据）
        2. 保存当前次的聊天记录
        3. 利用模型lm -> 1. 提取item_names  2.重写提问内容
        4. 进行item_name的向量数据库查询
        5. 对item_name结果进行打分分类处理 A 【确认集合】  B【可选集合】
        6. 处理确认和可选集合！ 有确认 =》 继续下个节点执行  || 有可选 or 没有item_names -> answer赋值结果
        7. 补充state状态 item_names rewritten_query  history
    """
    print(f"---node_item_name_confirm---开始处理")
    # 记录任务开始
    add_running_task(state["session_id"], sys._getframe().f_code.co_name,state["is_stream"])

    #  1. 获取历史条件记录（作为依据）、最大限制10条
    history_chats = get_recent_messages(session_id=state["session_id"],limit=10)
    print(history_chats)
    #  3. 利用模型lm -> 
    # 1. 提取item_names  2.重写提问内容
    #  参数： state["original_query"] || history_chats
    #  响应： { item_names : [华为 p60]  默认根据历史聊天记录给我们的！！ , rewritten_query : str }
    #  1. 为啥问题要重写？需要去意图识别【面试tips】
    """          
       1. 消除指代歧义    他 Ta 它 不明确！  明确查询主体 item_name
       2. 补全上下文     他的问题需要有历史记录支持！ 
       3. 去掉口语和冗余  同学们 同志们  为啥 咋弄 完犊子了 
       4. 润色问题增加召回率  模型查询的时候也会更精准
    """
    item_names_and_rewritten_query = step_3_llm_item_name_and_rewrite_query(state["original_query"],history_chats)
    item_names = item_names_and_rewritten_query.get("item_names",[])
    rewritten_query = item_names_and_rewritten_query.get("rewritten_query","")
    # 向量数据库查询 item_name
    item_results = {} 
    if len(item_names) > 0 :
        
        # 4. 根据大模型识别出的itemname
        # 向量查询 item_names -》 模型提取 不一定跟我们向量数据库的完全相同(华为手机 P60)
        # 参数： item_names = [1,2,3,4]  这里的1，2，3都是重写提取识别的item_name，可能有1个，也可能多个
        #  1 -> 向量数据库中item_names (向量查询) 
        #  2 -> 向量数据库中item_names (向量查询)
        #  返回：[ { extracted:（模型提取的item_name）, matches:[{item_name:名字,score:0.8},{item_name:名字,score:0.8}]  }，
    #            { extracted:（模型提取的item_name）, matches:[{item_name:名字,score:0.8},{item_name:名字,score:0.8}]  }，]
    #          
        query_milvus_results = step_4_query_milvus_item_names(item_names)
        # 5. 查询结果进行处理 区分 确定的item_name 以及可选的item_name  -》  没有对应的item_name
        # 参数： query_milvus_results
        # 返回： {确定item_name:[x,x,x,x,x] ,可选的item_name:[x,x,x,x,x]}
        # result = {
        #         "confirmed_item_names":list(set(confirmed_item_names)),
        #         "options_item_names":list(set(options_item_names))
        #     }
        item_results = step_5_confirmed_and_optional_item_name(query_milvus_results)

    #6. 根据item_name确定的集合进行用户反馈结果的处理 -》 answer赋值结果
    # 参数： item_results （两个集合） || 修改历史聊天记录对应item_names history_chats
    state = step_6_deal_list(state,item_results,history_chats,rewritten_query)
    #7. 记录本次的聊天对话 （answer回答）
    save_chat_message(
        session_id=state["session_id"],
        role="user",
        text=state["original_query"],
        rewritten_query=state.get("rewritten_query", ""),
        item_names=state.get("item_names", []),
        image_urls=[]
    )
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
        if result_state.get("item_names"):
            print(f"\n[PASS] 成功提取并确认商品名: {result_state['item_names']}")
        else:
            print(f"\n[WARN] 未确认到商品名 (可能是向量库无匹配或LLM未提取)")

    except Exception as e:
        logger.exception("==========")