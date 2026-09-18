
# 6个接口  健康状态 返回页面  发起提问   sse长连接  查看历史对话  清空历史对话
from contextlib import asynccontextmanager
from pathlib import Path
import uuid
import uvicorn
from fastapi import FastAPI, BackgroundTasks, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.middleware.cors import CORSMiddleware
from app.core.logger import logger
from app.db.mysql import safe_init_db
from app.rag.query_process.agent.state import create_query_default_state
from app.utils.path_util import PROJECT_ROOT

from app.utils.task_utils import *
from app.utils.sse_utils import create_sse_queue, SSEEvent, sse_generator
from app.memory.recent_message_service import get_recent_message_service
from app.memory.utils.scope import build_scope
from app.memory.utils.scope import build_long_term_scope
from app.memory.extraction_trigger import maybe_trigger_extraction
from app.memory.config import MEMORY_DEFAULT_USER_ID
from app.memory.api import memory_router
from app.api.import_file import import_router
from app.rag.query_process.agent.main_graph import query_app


# 推给前端的统一错误文案。
# 原始异常可能带 SQL 语句、表名、内部路径，只写日志，不进 SSE 负载。
USER_FACING_ERROR_MESSAGE = "服务处理异常，请稍后重试"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """启动时初始化一次短期记忆的表结构。

    为什么放这里、而不是 RecentMessageStore 的构造函数里：
    建表会真的连库，放在构造函数里意味着 MySQL 一挂，短期记忆服务连构造都
    构造不出来，读路径连「Redis 优先」都走不到（见 app/memory/recent_message_service.py）。
    这里失败只告警：服务照常启动，问答照常可用，短期记忆降级。
    用线程池执行，避免启动阶段阻塞事件循环。
    """
    await run_in_threadpool(safe_init_db)
    yield


# 定义fastapi对象
app = FastAPI(title="query service", description="掌柜智库查询服务！", lifespan=lifespan)

# 挂载 Agent Memory API，路径统一为 /memory/...
app.include_router(memory_router)

# 挂载文件上传/导入路由，统一与查询服务共用同一个端口。
app.include_router(import_router)

# 跨域配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 健康状态
@app.get("/health")
async def health():
    logger.info(f"触发后台检测检查接口，数据一切正常！！")
    return {"status": "ok"}


# 返回chat.html
@app.get("/chat.html")
async def chat_html():
    # 查找chat.html页面的地址
    chat_html_path  = PROJECT_ROOT / 'app' / 'rag' / 'query_process' / 'page' / 'chat.html'
    if not chat_html_path.exists():
        raise HTTPException(status_code=404, detail="chat.html文件不存在")
    return FileResponse(chat_html_path)


# 发起提问接口
# 接收参数的类型
class QueryRequest(BaseModel):
    query: str = Field(..., title="查询内容,必须传递")
    session_id:str = Field(None, title="会话id，可以不传递，后台uuid生成第一个！")
    is_stream:bool = Field(False, title="是否流式返回结果")
    # 用户标识：长期记忆按用户聚合（跨对话共享）。
    # 不传时回退到配置 MEMORY_DEFAULT_USER_ID；都没有则长期记忆退化为会话级。
    user_id:str = Field(None, title="用户标识，可不传，用于跨对话的长期记忆")


def run_query_graph(query: str, session_id: str, is_stream: bool, user_id: str = None):
    # 一会回调用 main_graph执行
    # 本次任务开启了！ is_stream = True 把结果加入到队列，sse可以取到
    # 任务ID、状态名称、是否开启流式输出（开启则创建相应队列）
    
    update_task_status(session_id, "processing", is_stream)# 更新任务状态为

    # 创建默认状态
    state = create_query_default_state(
        session_id=session_id,
        user_id=user_id or "",
        original_query=query,
        is_stream=is_stream
    )
    try:
        query_app.invoke(state)
        # 本次任务开启了！ is_stream = True 把结果加入到队列，sse可以取到
        update_task_status(session_id, "completed", is_stream)
    except Exception as e:
        logger.exception(f"---session_id = {session_id},查询流程出现异常！！{str(e)}")
        # 修改 event = process
        update_task_status(session_id, "failed", is_stream)
        # 推送指定类型的事件
        # 只推统一文案：原始异常（含 SQL、内部路径）已经进了日志
        push_to_session(session_id, SSEEvent.ERROR, {"error": USER_FACING_ERROR_MESSAGE})


@app.post("/query")  # 客户端 -》 问题 -》 graph开启了 -》 查到rag的结果 -》 返回即可！！
async def query(request: QueryRequest,background_tasks: BackgroundTasks): # background_tasks开启异步执行函数
    """
    :param request: 请求参数
    :param background_tasks: 异步执行函数  is_stream = True
    :return:
    """
    query = request.query
    session_id = request.session_id or str(uuid.uuid4()) # 如果没有传递session_id，后台生成一个uuid
    is_stream = request.is_stream
    user_id = request.user_id
    # 判断是不是流式处理 （异步 -》 先返回一个结果 开始处理 | 后台运行图，结果向前端推送）
    if is_stream:
        # 只要开启流式处理，我们业务中就是将数据，插入到队列中！
        #  {session_id , queue [update_task_state , add_running_task,add_done_list]}
        # 创建当前session_id对应的队列 =》 _session_stream
        create_sse_queue(session_id)
        # 异步执行  立即返回结果前端 || 中间的过程 sse 一点一点推送给前端
        background_tasks.add_task(run_query_graph, query, session_id, is_stream, user_id)
        logger.info(f"query:{query}已经开启了异步和流式处理！！")
        return {
            "session_id": session_id,
            "message": "本次查询处理中...."
        }
    else:
        # 同步执行
        run_query_graph(query, session_id, is_stream, user_id)
        # 获取最后一个节点插入的结果！ node_answer_output (answer)
        answer = get_task_result(session_id,"answer")  # task_utils 封装的一个存储会话结果函数，获取key为“answer”的值，默认为空
        # 返回对应的json数据即可
        logger.info(f"query:{query}开启同步处理！处理结果为：{answer}!")
        return {
            "answer": answer,
            "session_id": session_id,
            "message": "本次查询处理完毕！",
            "done_list": []
        }

# 获取流式输出的数据
@app.get("/stream/{session_id}")
async def stream(session_id: str,request:Request):
    """
    :param session_id:
    :param request: 前端的原生请求对象，可以判断是否断开连接！！
    :return:
    """
    logger.info(f"session_id = {session_id}客户端，已经和后台建立了长连接！")
    return StreamingResponse( # 推流
        sse_generator(session_id,request),
        media_type="text/event-stream"
    )


@app.post("/session/close")
async def close_session(session_id: str, user_id: str = None):
    """会话收尾：把上一个对话的残余消息沉淀成长期记忆。

    前端在用户点「新建对话」时调用一次。为什么需要它：
    按条数触发的抽取有个盲区——短对话（不足 40 条）永远等不到触发，
    关掉后再开新对话，那段内容就跨对话不可见了。这里用 force=True 忽略阈值，
    只要残余 ≥ 2 条（至少一问一答）就抽一次。

    调用是异步的（内部起线程），接口立即返回。
    """
    run_scope = build_scope(run_id=session_id)
    long_term_scope = build_long_term_scope(
        user_id=user_id,
        default_user_id=MEMORY_DEFAULT_USER_ID,
        run_id=session_id,
    )
    try:
        triggered = maybe_trigger_extraction(run_scope, long_term_scope, force=True)
    except Exception as exc:
        logger.warning(f"会话收尾触发的长期记忆抽取失败，session_id={session_id}：{exc}")
        raise HTTPException(status_code=500, detail=str(exc))
    return {"code": 200, "session_id": session_id, "triggered": triggered}


@app.get("/history/{session_id}")
async def history(session_id: str,limit: int = 10):
    """
    :param session_id:
    :param limit: 切割的数量
    :return:
    """
    # 获取历史对话
    memory_service = get_recent_message_service()
    scope = build_scope(run_id=session_id)
    chats = memory_service.get_messages(scope, limit)
    items = chats
    logger.info(f"查询历史对话，session_id = {session_id}成功！查询数据为：{chats}")
    return {
        "session_id":session_id,
        "items": items
    }

@app.delete("/history/{session_id}")
async def delete_history(session_id: str):
    """
    :param session_id:
    :return:
    """
    # 删除历史对话
    memory_service = get_recent_message_service()
    scope = build_scope(run_id=session_id)
    delete_count = memory_service.clear(scope)
    logger.info(f"删除历史对话，session_id = {session_id}成功,删除数量：{delete_count}！")
    return {
        "deleted_count":delete_count,
        "message": f"{session_id}聊天记录删除成功！"
    }


if __name__ == '__main__':
    uvicorn.run(app, host="127.0.0.1", port=8001)
