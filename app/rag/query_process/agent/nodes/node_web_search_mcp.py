import asyncio
import json
import sys
from agents.mcp import MCPServerStreamableHttp # pip install openai-agents
from app.core.logger import  logger

from app.conf.mcp_config import mcp_config
from app.core.tracing import observe, update_current_span
from app.utils.task_utils import add_running_task,add_done_task


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
            "url": mcp_config.yjt_base_url,
            "headers": {"x-api-key": mcp_config.yjt_api_key}, # 企业预警通采用的请求头
            # "headers": {"Authorization": f"Bearer {mcp_config.qcc_api_key}"}, # 企查查采用的请求头
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

        # 获取枚举值
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


def _extract_web_search_docs(result_json: dict) -> list[dict]:
    """从新版 MCP 返回结构中提取资讯列表。

    新版 data.records.data 中每条记录为数组，字段顺序：
    0 news_id, 1 title, 2 date, 3 source, 4 summary,
    5 original_url, 6 related_companies, 7 etime
    """
    records_data = result_json.get("data", {}).get("records", {}).get("data", [])
    if not isinstance(records_data, list):
        return []

    docs = []
    for row in records_data:
        if not isinstance(row, list):
            continue

        title = row[1] if len(row) > 1 else ""
        date = row[2] if len(row) > 2 else ""
        source = row[3] if len(row) > 3 else ""
        summary = row[4] if len(row) > 4 else ""
        original_url = row[5] if len(row) > 5 else ""
        related_companies = row[6] if len(row) > 6 else []

        company_name = ""
        category = ""
        if (
            isinstance(related_companies, list)
            and related_companies
            and isinstance(related_companies[0], list)
        ):
            company_item = related_companies[0]
            if company_item:
                company_name = company_item[0] or ""
            if len(company_item) > 1:
                category = company_item[1] or ""

        summary = (summary or "（暂无摘要）").replace("\n", " ").replace("\r", " ")
        summary = " ".join(summary.split())

        docs.append({
            "date": date,
            "category": category,
            "title": title,
            "summary": summary,
            # "source": source,
            # "original_url": original_url,
            "company_name": company_name,
        })

    return docs


@observe(name="node:web_search_mcp", as_type="span", capture_input=False, capture_output=False)
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
    update_current_span(input={"rewritten_query": query})
    # 2. 调用streamable网络搜索方法
    # 在同步（普通）Python环境中，启动一个异步事件循环，
    # 来执行一个名为 mcp_call_streamable 的异步协程，并等待它彻底完成后，把返回值赋给 result。
    # 因为 mcp_call_streamable 是一个异步函数
    # 它可能会在等待网络响应时暂停执行，而 asyncio.run 会确保整个过程在一个事件循环中顺利进行。
    docs = []
    try:
        result = asyncio.run(mcp_call_streamable(query))
        if not result:
            raise ValueError("MCP 返回结果为空")

        # 将mcp的返回转化成json格式
        result_json = json.loads(result.content[0].text)
        docs = _extract_web_search_docs(result_json)
    except Exception as exc:
        # MCP 格式或网络可能发生变化，兜底返回空列表，保证后续 RRF/rerank 不受影响。
        logger.error(f"MCP搜索报错：{exc}")
        docs = []

    logger.info(f"mcp搜索的结果为:{json.dumps(docs, ensure_ascii=False)}")
    print("---node-web-search-mcp处理结束---")
    update_current_span(
        output={"docs": len(docs), "top": [doc.get("title") for doc in docs[:5]]}
    )
    add_done_task(state["session_id"], sys._getframe().f_code.co_name, state["is_stream"])
    # 并行的 不要直接返回state
    return {
        "web_search_docs": docs
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
    
