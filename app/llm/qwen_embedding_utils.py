"""
Qwen Embedding 工具。

与 ``embedding_utils.py`` 的差异：
- 不依赖 pymilvus / BGE-M3 本地模型，无需下载或部署模型文件；
- 通过 DashScope API 调用云端 text-embedding-v4 模型；
- 生成稠密+稀疏混合向量

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
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL")
# 是否开启调试输出
DEBUG_EMBEDDING = False

def generate_embeddings(texts: List[str]) -> Dict:
    """
    为文本列表生成稠密+稀疏混合向量嵌入（调用阿里云 DashScope API）

    :param texts: 要生成嵌入的文本列表，单文本也需封装为列表
    :return: 字典格式的向量结果Dict{dense:List[],sparse:List[]}
             - {dense: [[float], ...]}  稠密向量嵌套列表，与输入文本一一对应
             - {sparse: [{int: float}, ...]}  稀疏向量字典列表
    :raise ValueError: 入参不合法或缺少 API 密钥
    :raise RuntimeError: API 调用失败
    """
    # ---- 1. 入参校验 ----
    if not isinstance(texts, list) or len(texts) == 0:
        logger.warning("生成向量入参不合法，texts必须为非空列表")
        raise ValueError("参数texts必须是包含文本的非空列表")

    if not DASHSCOPE_API_KEY:
        raise ValueError("缺少 DASHSCOPE_API_KEY，请在 .env 中配置")

    # ---- 2. 配置 DashScope ----
    dashscope.base_http_api_url = DASHSCOPE_BASE_URL
    dashscope.api_key = DASHSCOPE_API_KEY

    logger.info(f"开始为{len(texts)}条文本生成向量（模型: {EMBEDDING_MODEL}）")

    try:
        # ---- 3. 调用 DashScope 文本向量 API ----
        resp = dashscope.TextEmbedding.call(
            model=EMBEDDING_MODEL,
            input=texts,
            output_type="dense&sparse",  # 同时返回稠密 + 稀疏向量
        )

        if resp.status_code != HTTPStatus.OK:
            error_msg = (
                f"DashScope API 调用失败："
                f"status_code={resp.status_code}, message={resp.message}"
            )
            logger.error(error_msg)
            raise RuntimeError(error_msg)

        # ---- 4. 解析返回结果 ----
        embeddings_output = resp.output.get("embeddings", [])
        if len(embeddings_output) != len(texts):
            raise RuntimeError(
                f"Embedding API 返回数量异常："
                f"期望 {len(texts)}，实际 {len(embeddings_output)}"
            )

        # 按 text_index 排序，确保与输入顺序严格一致
        sorted_embeddings = sorted(
            embeddings_output, key=lambda x: x.get("text_index", 0)
        )

        dense_vectors = []
        sparse_vectors = []
        
        debug_sparse = [] # 调试向量，里面会显示

        for emb in sorted_embeddings:
            # 稠密向量：直接取 embedding 列表
            dense_vectors.append(emb["embedding"])

            # 稀疏向量：从 sparse_embedding 列表中提取
            sparse_emb = emb.get("sparse_embedding")
            # sparse_emb = [
            #     {
            #         "index": 102214,
            #         "value": 2.0664,
            #         "token": "衣服"
            #     },
            #     {
            #         "index": 108042,
            #         "value": 2.334,
            #         "token": "的质量"
            #     },
            #     {
            #         "index": 103178,
            #         "value": 2.3418,
            #         "token": "杠"
            #     }
            # ]
            if isinstance(sparse_emb, list): # 如果是列表
                # 遍历列表中的每个字典，组装成 {index: value} 格式 
                # 在同一个字典里不断增加新的键值对。
                sparse_dict = {item["index"]: item["value"] for item in sparse_emb}
                # sparse_dict
                # {
                #     102214: 2.0664,
                #     108042: 2.334,
                #     103178: 2.3418
                # }

            else:
                # 如果字段不存在或不是列表，置空字典
                sparse_dict = {}
            sparse_vectors.append(sparse_dict)
            # 此时 dense_vectors 和 sparse_vectors 顺序与 texts 严格一致
            # sparse_vectors 列表
            # [
            #     {102214: 2.0664, 108042: 2.334, 103178: 2.3418},
            #     {102214: 1.234, 108042: 2.567, 103178: 2.890},
            #     ...
            # ]
            # 只有确实是列表时才提取调试信息，主要增加了可显示的token参数
            if isinstance(sparse_emb, list) and DEBUG_EMBEDDING:
                debug_sparse.append([
                    {
                        "index": item["index"],
                        "token": item.get("token"),
                        "weight": item["value"]
                    }
                    for item in sparse_emb
                ])
        
        if DEBUG_EMBEDDING:
            print(debug_sparse)

        result = {
            "dense": dense_vectors,
            "sparse": sparse_vectors,
        }
        
        logger.success(f"{len(texts)}条文本向量生成完成")
        return result

    except (ValueError, RuntimeError):
        raise  # 已知异常直接上抛，不重复记录
    except Exception as e:
        logger.error(f"文本向量生成失败：{str(e)}", exc_info=True)
        raise


