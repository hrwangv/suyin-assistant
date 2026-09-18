import os
import sys
from typing import List, Dict, Any

# 导入自定义模块
from app.rag.import_process.agent.state import ImportGraphState, state_summary
from app.utils.qdrant_utils import (
    get_qdrant_client,
    ensure_collection,
    ensure_payload_indexes,
    upsert_chunks,
)
from app.utils.task_utils import add_running_task, add_done_task
from app.core.logger import logger
from app.conf.qdrant_config import qdrant_config

# 从配置文件读取切片集合名称，与配置解耦，便于环境切换
CHUNKS_COLLECTION_NAME = qdrant_config.chunks_collection


def step_2_prepare_collections():
    """
    确保 chunks collection 存在（Qdrant 版）
    :param state:
    :return: QdrantClient
    """
    # 1. 获取 Qdrant 的客户端
    qdrant_client = get_qdrant_client()
       
    # 2. 确保 collection 存在（不存在则自动创建，dense + 自定义 sparse 双向量）
    ensure_collection(qdrant_client, CHUNKS_COLLECTION_NAME)

    # 2.1 确保检索会用到的 payload 索引存在（category/domain/file_title/news_date）。
    # 这步是性能优化：没有索引过滤照样生效，只是退化成全表扫描。
    # 放在这里而不是 ensure_collection 里面，是因为 collection 已存在时
    # ensure_collection 会直接 return，老库就永远补不上索引了。
    # 注意：item_name 不在索引清单里（它目前只是文件名兜底值，不是高频过滤条件）。
    ensure_payload_indexes(qdrant_client, CHUNKS_COLLECTION_NAME)
    
    return qdrant_client




def step_3_delete_old_data(qdrant_client, item_name):
    """
    删除旧数据：根据 item_name 删除（Qdrant 版）
    :param qdrant_client:
    :param item_name:
    :return:
    """
    from qdrant_client import models
    qdrant_client.delete(
        collection_name=CHUNKS_COLLECTION_NAME,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="item_name",
                        match=models.MatchValue(value=item_name),
                    ),
                ],
            ),
        ),
    )
    logger.info(f"已删除 collection[{CHUNKS_COLLECTION_NAME}] 中 item_name='{item_name}' 的旧 chunks 数据")


# ==================== 旧版 Milvus step_3（已废弃，保留供对比） ====================
# def step_3_delete_old_data(milvus_client, item_name):
#     """
#     删除旧数据 根据item_name删除（Milvus 版）
#     """
#     milvus_client.delete(collection_name=CHUNKS_COLLECTION_NAME,
#                          filter=f"item_name=='{item_name}'")
#     milvus_client.load_collection(collection_name=CHUNKS_COLLECTION_NAME)


def step_4_insert_collections(qdrant_client, chunks, file_id="", task_id=""):
    """
    批量插入 chunks 数据到 Qdrant（Qdrant 版）
    用 upsert_chunks 一步完成：生成 UUID chunk_id + 构造 PointStruct + 批量 upsert
    :param qdrant_client:
    :param chunks:
    :return: 带有 chunk_id 回显的 chunks
    """
    chunks = upsert_chunks(
        qdrant_client,
        CHUNKS_COLLECTION_NAME,
        chunks,
        file_id=file_id,
        task_id=task_id,
    )
    logger.info(f"完成了数据插入，成功插入了 {len(chunks)} 条数据")
    return chunks


# ==================== 旧版 Milvus step_4（已废弃，保留供对比） ====================
# def step_4_insert_collections(milvus_client, chunks):
#     """
#     插入集合的数据！（Milvus 版）
#     """
#     insert_result = milvus_client.insert(collection_name=CHUNKS_COLLECTION_NAME, data=chunks)
#     insert_count = insert_result.get("insert_count", 0)
#     logger.info(f"完成了数据插入，成功插入了 {insert_count} 条数据")
#
#     # 获取回显的 ids
#     ids = insert_result.get("ids", [])
#     if ids and len(ids) == len(chunks):
#         for index, chunk in enumerate(chunks):
#             chunk['chunk_id'] = ids[index]
#     return chunks

def node_import_qdrant(state: ImportGraphState) -> ImportGraphState:
    """
    节点: 导入向量库 (node_import_qdrant)
    将处理好的向量数据写入 Qdrant 数据库。
    1. 连接 Qdrant。
    2. 根据 item_name 删除旧数据 (幂等性)。
    3. 批量插入新的向量数据。
    """
    # 1. 进入的日志和任务状态的配置
    function_name = sys._getframe().f_code.co_name
    logger.info(f">>> [{function_name}]开始执行了！现在的状态为：{state_summary(state)}")
    add_running_task(state['task_id'], function_name)
    try:
        # 1. 获取输入的数据 （校验）
        chunks = state.get('chunks')
        if not chunks:
            logger.error(f">>> [{function_name}]没有chunks数据，请检查！")
            raise ValueError("没有chunks数据")
        # 2. 确保 collection 存在（不存在则自动创建）
        qdrant_client = step_2_prepare_collections()
        # 3. 根据 item_name 删除旧数据（幂等性）
        step_3_delete_old_data(qdrant_client, chunks[0]['item_name'])
        # 4. 批量插入 chunks 数据（自动生成 UUID chunk_id 回显）
        with_id_chunks = step_4_insert_collections(
            qdrant_client,
            chunks,
            file_id=state.get("file_id", ""),
            task_id=state.get("task_id", ""),
        )

        state['chunks'] = with_id_chunks
    except Exception as e:
        # 处理异常
        logger.error(f">>> [{function_name}]导入chunks对应的向量数据库发生了异常，异常信息：{e}")
        raise  # 终止工作流
    finally:
        # 6. 结束的日志和任务状态的配置
        logger.info(f">>> [{function_name}]开始结束了！现在的状态为：{state_summary(state)}")
        add_done_task(state['task_id'], function_name)
    return state


# ==================== 旧版 Milvus 主函数（已废弃，保留供对比） ====================
# def node_import_milvus(state: ImportGraphState) -> ImportGraphState:
#     """
#     节点: 导入向量库 (node_import_milvus)（Milvus 版）
#     """
#     function_name = sys._getframe().f_code.co_name
#     logger.info(f">>> [{function_name}]开始执行了！现在的状态为：{state}")
#     add_running_task(state['task_id'], function_name)
#     try:
#         chunks = state.get('chunks')
#         if not chunks:
#             logger.error(f">>> [{function_name}]没有chunks数据，请检查！")
#             raise ValueError("没有chunks数据")
#         milvus_client = step_2_prepare_collections(state)
#         step_3_delete_old_data(milvus_client, chunks[0]['item_name'])
#         with_id_chunks = step_4_insert_collections(milvus_client, chunks)
#         state['chunks'] = with_id_chunks
#     except Exception as e:
#         logger.error(f">>> [{function_name}]导入chunks对应的向量数据库发生了异常，异常信息：{e}")
#         raise
#     finally:
#         logger.info(f">>> [{function_name}]开始结束了！现在的状态为：{state}")
#         add_done_task(state['task_id'], function_name)
#     return state



if __name__ == '__main__':
    # --- 单元测试 ---
    # 目的：验证 Qdrant 导入节点的完整流程，包括连接、创建集合、清理旧数据和插入新数据。
    import sys
    import os
    from dotenv import load_dotenv

    # 加载环境变量 (自动寻找项目根目录的 .env)
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(current_dir))
    load_dotenv(os.path.join(project_root, ".env"))

    # 构造测试数据
    dim = 1024
    test_state = {
        "task_id": "test_qdrant_task",
        "chunks": [
            {
                "content": "Qdrant 测试文本 1",
                "title": "测试标题",
                "item_name": "测试项目_Qdrant",
                "parent_title": "test.pdf",
                "part": 1,
                "file_title": "test.pdf",
                "dense_vector": [0.1] * dim,
                "sparse_vector": {1: 0.5, 10: 0.8},
            },
            {
                "content": "Qdrant 测试文本 2",
                "title": "测试标题2",
                "item_name": "测试项目_Qdrant2",
                "parent_title": "test.pdf2",
                "part": 1,
                "file_title": "test.pdf2",
                "dense_vector": [0.1] * dim,
                "sparse_vector": {1: 0.5, 10: 0.8},
            },
        ],
    }

    print("正在执行 Qdrant 导入节点测试...")
    try:
        # 检查必要的环境变量
        if not qdrant_config.url:
            print("❌ 未设置 QDRANT_URL，无法连接 Qdrant")
        else:
            # 执行节点函数
            result_state = node_import_qdrant(test_state)

            # 验证结果
            chunks = result_state.get("chunks", [])
            if chunks and chunks[0].get("chunk_id"):
                print(f"✅ Qdrant 导入测试通过，生成 ID: {chunks[0]['chunk_id']}")
            else:
                print("❌ 测试失败：未能获取 chunk_id")

    except Exception as e:
        print(f"❌ 测试失败: {e}")
