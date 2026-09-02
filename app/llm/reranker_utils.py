"""
Qwen Rerank 工具。

"""
import os
from http import HTTPStatus
from typing import List, Dict


import dashscope
from dotenv import load_dotenv

from app.core.logger import logger

load_dotenv()

# ---------- 配置常量 ----------
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")
# 阿里云百炼 DashScope 的 base URL（北京地域）
DASHSCOPE_BASE_URL = os.getenv("DASHSCOPE_BASE_URL")
# text-embedding-v4：支持 dense + sparse 混合输出的通用文本向量模型
RERANK_MODEL = os.getenv("RERANK_MODEL")
# 是否开启调试输出
DEBUG_EMBEDDING = False


def text_rerank(query:str, chunks_list:List, top_n: int = 10):
    '''
    入参
    '''
    # ---- 1. 入参校验 ----
    if not isinstance(query, str) or len(query) == 0:
        logger.warning("输入查询问题不合法")
        raise ValueError("参数texts必须是包含文本的非空列表")

    if not DASHSCOPE_API_KEY:
        raise ValueError("缺少 DASHSCOPE_API_KEY，请在 .env 中配置")

    # ---- 2. 配置 DashScope ----
    dashscope.base_http_api_url = DASHSCOPE_BASE_URL
    dashscope.api_key = DASHSCOPE_API_KEY
    try:
        resp = dashscope.TextReRank.call(
            model = RERANK_MODEL,
            query = query,
            documents = chunks_list,
            top_n = top_n,
            return_documents=True,
            instruct="Given a web search query, retrieve relevant passages that answer the query."
        )
        '''
        {"status_code": 200, 
        "request_id": "d2393f3c-cd9a-9b75-b511-debe7604f666", 
        "code": "", 
        "message": "", 
        "output": {
            "results": [
            {"index": 0, "relevance_score": 0.9247129722205545, "document": {"text": "文本排序模型广泛用于搜索引擎和推荐系统中，它们根据文本相关性对候选文本进行排序"}}, 
            {"index": 2, "relevance_score": 0.7579385108464249, "document": {"text": "预训练语言模型的发展给文本排序模型带来了新的进展"}}, 
            {"index": 1, "relevance_score": 0.2772209839843891, "document": {"text": "量子计算是计算科学的一个前沿领域"}}]}, "usage": {"total_tokens": 105}
            }

        '''

        if resp.status_code == HTTPStatus.OK:
            logger.info(f"重排序成功，返回 {len(resp.output.results)} 条结果")
            return resp.output.results  # 包含 document 和 relevance_score
        else:
            error_msg = f"API 返回错误: {resp.status_code} - {resp.message}"
            logger.error(error_msg)
            raise RuntimeError(error_msg)

    except (ValueError, RuntimeError):
        raise  # 已知异常直接上抛，不重复记录
    except Exception as e:
        logger.error(f"重排序失败：{str(e)}", exc_info=True)
        raise


if __name__ == '__main__':
    text_rerank("什么是文本排序模型",[
            "文本排序模型广泛用于搜索引擎和推荐系统中，它们根据文本相关性对候选文本进行排序",
            "量子计算是计算科学的一个前沿领域",
            "预训练语言模型的发展给文本排序模型带来了新的进展"
        ],)



