import sys
import os
from typing import Any, List, Dict

from app.rag.import_process.agent.state import ImportGraphState
from app.llm.qwen_embedding_utils import generate_embeddings
from app.utils.task_utils import add_running_task,add_done_task
from app.core.logger import logger

def node_bge_embedding(state: ImportGraphState) -> ImportGraphState:
    """
    节点: 向量化 (node_bge_embedding)
    为什么叫这个名字: 使用向量模型将文本转换为向量 (Embedding)。
    上一节点只是保存item name,没有涉及正文文本
    未来要实现:
    1. 加载  模型。
    2. 对每个 Chunk 的文本进行 Dense (稠密) 和 Sparse (稀疏) 向量化。
    3. 准备好写入 向量数据库 的数据格式。
    """
    # 获取当前节点名称，用于日志和任务状态记录
    current_node = sys._getframe().f_code.co_name
    logger.info(f">>> 开始执行LangGraph节点：{current_node}")

    # 标记任务运行状态，用于任务监控/前端进度展示
    add_running_task(state.get("task_id", ""), current_node)
    logger.info("--- 文本向量化处理启动 ---")

    try:
        # 1.获取要生成向量的chunks
        chunks = state.get("chunks")
        if not chunks or not isinstance(chunks, list):
            logger.error(">>> chunks数据不是列表格式，请检查数据格式")
            raise  ValueError(">>> chunks数据不是列表格式，请检查数据格式")

        # 2.给每个chunk生成向量
        # 2.1 获取嵌入式模型的客户端
        # 2.2 批量生成向量

        """
          1. 什么内容需要生成向量
              chunks - chunk - content - 生成向量                                     华为手机       充电器            什么开机的问题
               用户 - 问题 （华为手机（主语）怎么开机！） - 向量 -混合检索-  向量 （item_name 华为手机 title 开机方式  content 三击屏幕侧方按钮，即可开机！！）
                     问题 -》 item_name  == ||  item_name  确保不会差错文档！！
               什么东西 content - 向量 
                       item_name  content(title+内容)
                       f"商品名：item_name,介绍：content" 
                       有中文，尽量使用中文的标点符号！ 
                       原则：核心词前置 （前置集中力 权重 前128token）
          2. 列表能放一个字符串
              for - chunk - [content,content,content] -> 生成向量 
              8192 - token - 5
        """
        final_chunks = [] # 存储处理完的chunk -> 带有向量
        batch_size = 5 # 
        # 实际用的是阿里云 DashScope text-embedding-v4 云端模型（qwen_embedding_utils.py），
        # 每条文本上限 8192 tokens，但单次 API 调用中多条文本的 token 总和也有限制。
        # 一个 chunk 大约是 500-2000 字符
        for i in range(0,len(chunks),batch_size):
            # i+1  i+batch_size (步长)
            # 本次批量处理的chunk
            # batch_size 分片大小 
            batch_items = chunks[i:i+batch_size]
            # 定义当前批次的字符串！
            current_texts = []
            for item in batch_items:
                item_name = item.get("item_name")
                item_content = item.get("content")
                # 原则：核心词前置 （前置集中力 权重 前128token）
                item_text = f"{item_name}，内容介绍：{item_content}"
                current_texts.append(item_text)

            # 当前批次生成的向量
            result = generate_embeddings(current_texts)
            # 当前批次的chunk添加向量即可
            #  
            # 遍历当前批次的每一个 chunk，通过索引 i 取回对应生成的稠密/稀疏向量。
            # 使用 chunk.copy() 生成浅拷贝，避免直接修改原始 chunks 列表
            # （如果后续还需要原始数据，这是个好习惯；若原始数据已无他用，也可直接赋值）。
            # 将向量存入新字段 dense_vector 和 sparse_vector，追加到 final_chunks 结果列表。
            for i, chunk in enumerate(batch_items):
                chunk_item = chunk.copy()
                chunk_item['dense_vector'] = result['dense'][i]
                chunk_item['sparse_vector'] = result['sparse'][i]
                final_chunks.append(chunk_item)
        state['chunks'] = final_chunks
        logger.info(f"--- 向量化处理完成，共处理 {len(final_chunks)} 条文本切片 ---")
        add_done_task(state.get("task_id", ""), current_node)
    except Exception as e:
        # 捕获节点所有异常，记录错误堆栈，不中断整体流程
        logger.error(f"向量化节点执行失败：{str(e)}", exc_info=True)

    return state