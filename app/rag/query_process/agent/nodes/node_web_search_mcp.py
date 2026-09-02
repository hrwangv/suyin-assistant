import asyncio
import os
import json
import sys
from agents.mcp import MCPServerSse # pip install openai-agents
from agents.mcp import MCPServerStreamableHttp # pip install openai-agents
from app.core.logger import  logger

from app.conf.mcp_config import mcp_config
from app.utils.task_utils import add_running_task,add_done_task

DASHSCOPE_BASE_URL_STREAMABLE = mcp_config.mcp_base_url
DASHSCOPE_API_KEY = mcp_config.api_key

YJT_BASE_URL = mcp_config.yjt_base_url
YJT_API_KEY =mcp_config.yjt_api_key


async def mcp_call_streamable(query):
    """
    调用mcp工具
    :param query:
    :return:
    """
    # 1. 创建MCPServerStreamableHttp对象
    search_mcp = MCPServerStreamableHttp(
        params={
            # 核心参数
            # "name": "qcc-company",
            "url": YJT_BASE_URL,
            "headers": {"x-api-key": YJT_API_KEY}, # 企业预警通采用的请求头
            # "headers": {"Authorization": f"Bearer {YJT_API_KEY}"}, # 企查查采用的请求头
            # "timeout": 10, #连接超时时间
        }
    )
    # 2. 连接 - 调用 - 关闭
    try:
        # 连接
        await search_mcp.connect()

        # 获取工具
        tools = await search_mcp.list_tools()

        # print(f"工具列表：{tools}")

        # 获取枚举指
        # result = await search_mcp.call_tool(    
        #             tool_name="caihui_mcp_metadata",  # 工具名称 
        #             arguments={
        #                 "category": "params_metadata",
        #                 "query":"search_news/enums",
        #             }
        #         )
        # 调用
        result = await search_mcp.call_tool(    
            tool_name="search_news",  # 工具名称 
            arguments={
                "target_company": [query],
                "publish_date":"近一个月",
                # "news_category":["公司经营, 其他新闻, 市场预警"],

            }
        )
        return result
    finally:
        await search_mcp.cleanup()


def node_web_search_mcp(state):
    """
    节点功能，调用外部搜索引擎补充信息
    :param state:
    :return:
    """
    add_running_task(state["session_id"], sys._getframe().f_code.co_name,state["is_stream"])
    print("---node-web-search-mcp处理---")

    # 1. 获取问题 （rewritten_query）
    query = state.get("rewritten_query")
    # 2. 调用streamable网络搜索方法
    # 在同步（普通）Python环境中，启动一个异步事件循环，
    # 来执行一个名为 mcp_call_streamable 的异步协程，并等待它彻底完成后，把返回值赋给 result。
    # 因为 mcp_call_streamable 是一个异步函数
    # 它可能会在等待网络响应时暂停执行，而 asyncio.run 会确保整个过程在一个事件循环中顺利进行。
    result = asyncio.run(mcp_call_streamable(query))
  
    # 将mcp的返回转化成json格式
    result_json = json.loads(result.content[0].text)
    data_rows = result_json["data"]["records"]["data"]
    print(data_rows)
    # 提取与格式处理
    docs = []
    for row in data_rows:
        title = row[1]
        summary = row[5] or "（暂无摘要）"
        date = row[2]  # 提取日期
        company_name = row[7][0][0]  # 公司全称
        category = row[7][0][1] # 新闻类别大类

        # 去除摘要中的换行符，替换为空格
        summary_clean = summary.replace('\n', ' ').replace('\r', ' ')
        # 清理多余空格（将连续多个空格合并为一个）
        summary_clean = ' '.join(summary_clean.split())

        # 组成字典
        doc = {
            "date": date,
            "category": category,
            "title": title,
            "summary": summary_clean
        # 如需要可添加 company_name = row[7][0][0]
        }
        docs.append(doc)

    # 字典转为 JSON 字符串
    final_json = json.dumps(docs,ensure_ascii=False)

    logger.info(f"mcp搜索的结果为:{final_json}")
    print("---node-web-search-mcp处理结束---")
    add_done_task(state["session_id"], sys._getframe().f_code.co_name, state["is_stream"])
    # 并行的 不要直接返回state
    return {
        "web_search_docs":final_json
    }


from dotenv import load_dotenv

if __name__ == '__main__':
    load_dotenv()
    test_state = {
        "session_id":"mcp_01",
        "rewritten_query": "润泽科技",
        "is_stream":True
    }

    # 调用 websearch_node 函数
    result_state = node_web_search_mcp(test_state)

    # 验证结果
    print("测试结果:")
    print(f"查询内容: {test_state.get('rewritten_query')}")

    # 输出搜索结果
    search_results = result_state.get('web_search_docs', [])
    #print(f"搜索结果数量: {len(search_results)}")
    print("search_results", search_results)
    