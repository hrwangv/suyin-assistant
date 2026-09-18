import os
import shutil
import uuid
from pathlib import Path
from typing import List, Dict, Any
from datetime import datetime
import uvicorn
# 第三方库
from fastapi import APIRouter, FastAPI, UploadFile, File, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
# 项目内部工具/配置/客户端

from app.utils.path_util import PROJECT_ROOT
from app.utils.task_utils import (
    add_running_task,
    add_done_task,
    get_done_task_list,
    get_running_task_list,
    update_task_status,
    get_task_status,
    clear_task,
)
from app.rag.import_process.agent.state import get_default_state
from app.rag.import_process.agent.main_graph import kb_import_app  # LangGraph全流程编译实例
from app.core.logger import logger  # 项目统一日志工具
from app.db.knowledge_file_store import (
    delete_knowledge_file,
    get_knowledge_file,
    insert_knowledge_file,
    list_knowledge_files,
    update_knowledge_file_status,
)


# 路由对象：可被 query_server 或独立 import_file 应用挂载
import_router = APIRouter()



# 定义调用import_graph的函数，像之前测试一样执行的图！！
# local_file_path (str)  task_id  local_dir(str)
def run_import_graph(
    task_id: str,
    file_id: str,
    local_file_path: str,
    local_dir: str,
):
    """
    开启图的执行和调用
    :param task_id: 每次的标识
    :param local_file_path: 文件的地址
    :param local_dir: 输出文件夹的地址
    :return:
    """
    # 本次任务对应的节点状态
    # _tasks_running_list: Dict[str, List[str]] = {}
    # _tasks_done_list: Dict[str, List[str]] = {}
    # key=task_id 本次上传文件任务的唯一标识
    # value = list [文件上传，检查文件 节点名字] （正在进行 | 已经完成）
    # add_done_task(task_id, "upload_file")
    # add_running_task(task_id, "upload_file")
    try:
        # 将文件状态更新
        update_knowledge_file_status(file_id, "processing")
        # 本次任务的总状态
        # _tasks_status: Dict[str, str] = {}
        # key= task_id
        # value = task_id任务的状态
        update_task_status(task_id,"processing")
        init_state = get_default_state() # 获得初始的图实例
        init_state["task_id"] = task_id
        init_state["file_id"] = file_id
        init_state["input_file_path"] = local_file_path
        init_state["local_dir"] = local_dir
        # 执行我们图
        for event in kb_import_app.stream(init_state):
        # event {节点名 : state}
            for node_name, result in event.items():
                logger.info(f"节点：{node_name}已经完成执行，执行结果为：{result}")
                # add_done_task(task_id, node_name)
        update_task_status(task_id,"completed")
        update_knowledge_file_status(file_id, "completed")
        logger.info(f"{task_id}:图状态执行完毕！！")
    except Exception as e:
        logger.exception("====图执行失败！发生异常====")
        update_task_status(task_id, "failed")
        update_knowledge_file_status(file_id, "failed", error_message=str(e))


# 8080/upload post -> 文件上传 + 开启导入流程
"""
  1. 接收文件存储到output文件夹！  /output/当天的日期/uuid(taskid)/文件名
  2. 异步开启，import_graph图的执行 【1. 整个任务的状态（开始和结束） 2. 每个节点的状态 add_running add_done】
"""
'''
background_tasks 是一个“后台任务管理器”对象。
它本身不执行业务逻辑，只做一件事：“登记”你想要在响应返回之后执行的任务。

它内部维护了一个任务队列（list of functions）。
当你调用 background_tasks.add_task(...) 时，你并没有立刻执行这个函数
而是把这个函数的调用信息（函数名、参数）存到了队列里。
'''

@import_router.post("/upload")
async def upload_file(background_tasks: BackgroundTasks,
                      files: List[UploadFile] = File(...)):
    """
    :param background_tasks:
    :param file:
    :return:
    """
    # 1. 整理下输出的位置 output / 日期文件夹
    today_str = datetime.now().strftime("%Y%m%d")
    base_out_path = PROJECT_ROOT / "output" / today_str
    # 2. 记录下每个文件上传的文件ID和任务ID
    file_ids = []
    task_ids = []
    # 3. 循环处理每个上传的文件（存储到本地） + 进行异步图任务调用
    for file in files:
        # file -> UploadFile  (.file 上传文件的输入流  .filename 上传文件名  .read 可以直接读取  .contenttype 获取我们文件mimetype类型)
        file_id = str(uuid.uuid4())  # 文件唯一ID，用于 MySQL 元数据和 Qdrant 向量关联
        task_id = str(uuid.uuid4())  # uuid不重复
        file_ids.append(file_id)
        task_ids.append(task_id)
        # 记录下进行文件上传了
        add_running_task(task_id,"upload_file")
        # 文件的dir_path
        dir_path = base_out_path / file_id
        # 没有文件夹就创建
        dir_path.mkdir(parents=True, exist_ok=True)
        # 文件的local_file_path
        local_file_path = dir_path / file.filename
        # 将上传的文件写入到 local_file_path
        with local_file_path.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        # 将文件元数据持久化到 MySQL，作为文件列表的 Source of Truth。
        insert_knowledge_file(
            file_id=file_id,
            task_id=task_id,
            filename=file.filename,
            file_path=str(local_file_path),
            size=local_file_path.stat().st_size,
            status="pending",
        )
        # 注册一个后台任务（并不执行，只是登记）
        # 异步执行
        # 参数1： run_import_graph 执行的方法
        # 参数2： *args  参数列表 task_id, local_file_path, dir_path - 》 run_import_graph
        background_tasks.add_task(
            run_import_graph,
            task_id,
            file_id,
            str(local_file_path),
            str(dir_path),
        )
        update_task_status(task_id, "pending")  # 设置初始状态，前端轮询时显示"上传中"
        logger.info(f"{task_id}:完成文件上传，并开启了对应的异步任务！！")
        add_done_task(task_id, "upload_file")
    # 4. 最终返回结果即可
    return {
        "code": 200,
        "message": f"完成了文件上传，并开启了异步任务！文件数量为: {len(files)}",
        "file_ids": file_ids,
        "task_ids": task_ids,
    }


# --------------------------
# 核心接口：任务状态查询接口
# 前端轮询此接口获取单个任务的处理进度和状态
# 访问地址：http://localhost:8000/status/{task_id} （GET请求）
# --------------------------
@import_router.get("/status/{task_id}", summary="任务状态查询", description="根据TaskID查询单个文件的处理进度和全局状态")
async def get_task_progress(task_id: str):
    """
    任务状态查询接口
    前端轮询此接口（如每秒1次），获取任务的实时处理进度
    返回数据均来自内存中的任务管理字典（task_utils.py），高性能无IO
    :param task_id: 全局唯一任务ID（由/upload接口返回）
    :return: 包含任务全局状态、已完成节点、运行中节点的JSON响应
    """
    # 构造任务状态返回体
    task_status_info: Dict[str, Any] = {
        "code": 200,
        "task_id": task_id,
        "status": get_task_status(task_id),  # 任务全局状态：pending/processing/completed/failed
        "done_list": get_done_task_list(task_id),  # 已完成的节点/阶段列表
        "running_list": get_running_task_list(task_id)  # 正在运行的节点/阶段列表
    }
    # 记录状态查询日志，方便追踪前端轮询情况
    logger.info(
        f"[{task_id}] 任务状态查询，当前状态：{task_status_info['status']}，已完成节点：{task_status_info['done_list']}")
    return task_status_info


def _format_file_size(size: int) -> str:
    """将字节数格式化为前端易读的大小字符串。"""
    if not size:
        return "0B"
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size)
    index = 0
    while value >= 1024 and index < len(units) - 1:
        value /= 1024
        index += 1
    return f"{value:.1f} {units[index]}"


def _backend_status_to_frontend(status: str) -> str:
    """将后端任务状态映射为前端知识库页面可识别的状态。"""
    return {
        "pending": "uploading",
        "processing": "parsing",
        "completed": "completed",
        "failed": "failed",
    }.get(status, status)


def _delete_qdrant_file_points(file_id: str) -> None:
    """按 file_id 删除 chunks 和 item_name 两个 Qdrant collection 中的向量数据。"""
    from qdrant_client import models

    from app.conf.qdrant_config import qdrant_config
    from app.utils.qdrant_utils import get_qdrant_client

    client = get_qdrant_client()
    for collection_name in (
        qdrant_config.chunks_collection,
        qdrant_config.item_name_collection,
    ):
        if not client.collection_exists(collection_name):
            continue

        client.delete(
            collection_name=collection_name,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="file_id",
                            match=models.MatchValue(value=file_id),
                        ),
                    ],
                ),
            ),
        )
        logger.info(
            f"已删除 collection[{collection_name}] 中 file_id='{file_id}' 的数据"
        )


@import_router.get("/files", summary="获取已上传文件列表")
async def get_files():
    """从 MySQL 查询文件元数据并返回文件列表。"""
    rows = list_knowledge_files()
    files = []
    for row in rows:
        created_at = row.get("created_at")
        upload_time = (
            created_at.strftime("%Y-%m-%d %H:%M:%S")
            if hasattr(created_at, "strftime")
            else str(created_at or "")
        )
        status = _backend_status_to_frontend(row.get("status") or "pending")
        files.append(
            {
                "id": row.get("file_id"),
                "name": row.get("filename"),
                "size": _format_file_size(int(row.get("size") or 0)),
                "uploadTime": upload_time,
                "status": status,
            }
        )
    return {"code": 200, "data": files}


@import_router.delete("/files/{file_id}", summary="删除已上传文件")
async def delete_file(file_id: str):
    """删除 MySQL 文件记录、Qdrant 向量数据和本地文件。"""

    # 按照file id获取文件记录
    record = get_knowledge_file(file_id)
    if not record:
        raise HTTPException(status_code=404, detail="文件不存在")

    # 1. 删除 Qdrant 中该文件对应的向量。
    try:
        _delete_qdrant_file_points(file_id)
    except Exception as exc:
        logger.warning(f"删除文件向量失败，file_id={file_id}：{exc}")
        raise HTTPException(status_code=500, detail="删除知识库向量数据失败")

    # 2. 删除本地文件目录。
    file_path = record.get("file_path") or ""
    if file_path:
        try:
            shutil.rmtree(Path(file_path).parent)
        except FileNotFoundError:
            pass
        except Exception as exc:
            logger.warning(f"删除本地文件目录失败，file_id={file_id}：{exc}")
            raise HTTPException(status_code=500, detail="删除本地文件失败")

    # 3. 清理内存任务状态并删除 MySQL 元数据。
    clear_task(record.get("task_id") or file_id)
    delete_knowledge_file(file_id)
    return {"code": 200, "deleted": True, "file_id": file_id}


# 独立启动时仍可保留 File Import Service
app = FastAPI(
    title="File Import Service",
    description="Web service for uploading files to Knowledge Base",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(import_router)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
