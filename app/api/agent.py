"""企业业务智能 Agent 的统一入口（规范第 67 / 68 节）。

    POST /api/agent/chat            统一聊天入口（同步 / 流式 / HITL 恢复）
    POST /api/agent/upload          会话附件上传（只支持 PNG / JPEG 图片）
    GET  /api/agent/stream/{id}     SSE 长连接（与 /stream/{session_id} 同一套队列）
    GET  /api/agent/state/{id}      查看线程 State（调试 / 前端恢复用）
    GET  /api/agent/runs            查看执行记录（PostgreSQL）
    GET  /api/agent/files/{name}    下载生成的 DOCX
    GET  /api/agent/tools           MCP 与工具配置自检（调试外部 MCP 用）
                         toolbox/*  工具箱管理与调用（工具清单 / schema / 调用 / MCP 开关）
"""
import uuid
import time
from pathlib import Path
from typing import Any, List, Optional

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.agent.events import AgentEvent, emit
from app.agent.graph import (
    build_response,
    get_thread_state,
    invoke_agent,
    is_thread_paused,
    resume_agent,
)
from app.agent.mcp.client import list_tools
from app.agent.mcp import registry as mcp_registry
from app.agent.mcp.company_client import company_search_tool_name, company_spec
from app.agent.mcp.ocr_client import ocr_input_mode, ocr_spec, ocr_tool_name
from app.agent.nodes.clarify import interrupt_text
from app.agent.services.file_store import save_upload
from app.agent.services import execution_store
from app.agent.state import create_agent_state
from app.agent.tools.router import toolbox_router
from app.conf.agent_config import agent_config
from app.core.logger import logger
from app.core.tracing import trace_scope
from app.utils.sse_utils import create_sse_queue, sse_generator
from app.utils.task_utils import update_task_status

USER_FACING_ERROR_MESSAGE = "服务处理异常，请稍后重试"

# 会话附件只收图片：文档解析走 OCR MCP，而它是"图片链接"入参（见 mcp/builtin.py）。
# PDF / Word 由前端提示用户先转成图片，后端这里再兜一道。
ALLOWED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}
ALLOWED_IMAGE_MIME_TYPES = {"image/png", "image/jpeg", "image/jpg"}
_GENERIC_MIME_TYPES = {"", "application/octet-stream", "binary/octet-stream"}
IMAGE_ONLY_HINT = "只支持图片格式（PNG / JPEG）。若是 PDF / Word 文档，请先转成图片再上传。"

agent_router = APIRouter(prefix="/api/agent", tags=["agent"])

# 工具箱（工具清单 / schema / 调用 / MCP 服务管理）：/api/agent/toolbox/*
agent_router.include_router(toolbox_router)


class AttachmentPayload(BaseModel):
    """前端上传后拿到的附件描述，原样回传给 /chat 即可。"""

    file_id: str = Field(..., description="POST /api/agent/upload 返回的 file_id")
    filename: Optional[str] = None
    mime_type: Optional[str] = None


class AgentChatRequest(BaseModel):
    message: str = Field("", description="用户输入文本")
    thread_id: Optional[str] = Field(None, description="会话 ID，不传自动生成")
    user_id: Optional[str] = Field(None, description="用户标识（长期记忆按它聚合）")
    attachments: List[AttachmentPayload] = Field(default_factory=list)
    stream: bool = Field(False, description="是否流式（SSE）返回")
    resume: Optional[Any] = Field(None, description="HITL 恢复值：Command(resume=...)")
    new_turn: bool = Field(
        False, description="线程停在 interrupt 时，强制把本次 message 当成新一轮对话"
    )


def _check_image_only(filename: str, content_type: Optional[str]) -> None:
    """只放过 PNG / JPEG；其余一律 415，理由写清楚，前端也有一道同样口径的提示。"""
    suffix = Path(filename or "").suffix.lower()
    mime = (content_type or "").lower()
    name_ok = suffix in ALLOWED_IMAGE_SUFFIXES
    mime_ok = mime in _GENERIC_MIME_TYPES or mime in ALLOWED_IMAGE_MIME_TYPES
    if name_ok and mime_ok:
        return
    raise HTTPException(
        status_code=415,
        detail=f"{IMAGE_ONLY_HINT}（不接受的类型：{filename or '未命名文件'}）",
    )


@agent_router.post("/upload")
async def upload_attachments(files: List[UploadFile] = File(...)):
    """上传会话附件（只支持 PNG / JPEG，不进入知识库导入流程）。"""
    limit = agent_config.max_upload_mb * 1024 * 1024
    saved = []
    for file in files:
        _check_image_only(file.filename or "", file.content_type)
        content = await file.read()
        if len(content) > limit:
            raise HTTPException(
                status_code=413,
                detail=f"文件 {file.filename} 超过 {agent_config.max_upload_mb}MB 限制",
            )
        uploaded = save_upload(content, file.filename or "attachment", file.content_type)
        saved.append(uploaded.to_dict())
    return {"files": saved, "count": len(saved)}


def _run_agent(
    thread_id: str,
    message: str,
    attachments: List[dict],
    user_id: Optional[str],
    is_stream: bool,
    resume: Optional[Any],
    new_turn: bool,
):
    """执行一轮 Agent（同步函数，放在后台任务 / 线程池里跑）。"""
    # 一轮对话 = 一条 Langfuse trace，用 thread_id 当 session，多轮在 UI 里能串起来。
    # 未配置 Langfuse 时 trace_scope 是 no-op，行为与接入前一致。
    with trace_scope(
        "agent-chat",
        session_id=thread_id,
        user_id=user_id,
        input=message or "",
        tags=["agent", "resume" if resume is not None else "chat"],
        metadata={"attachments": len(attachments or []), "stream": is_stream},
    ):
        update_task_status(thread_id, "processing", is_stream)
        started = time.perf_counter()
        should_resume = resume is not None or (not new_turn and is_thread_paused(thread_id))
        status = "completed"
        error = ""
        result = None
        try:
            if should_resume:
                value = resume if resume is not None else message
                emit(thread_id, AgentEvent.RESUME, {"value": value})
                result = resume_agent(value, thread_id)
            else:
                state = create_agent_state(
                    thread_id=thread_id,
                    request_text=message or "",
                    user_id=user_id or "",
                    attachments=attachments,
                    is_stream=is_stream,
                )
                result = invoke_agent(state, thread_id)

            if is_stream:
                _push_interrupt_if_paused(thread_id, result)
            update_task_status(thread_id, "completed", is_stream)
            logger.info(f"[agent] thread={thread_id} 本轮执行完成")
            return result
        except Exception as exc:
            logger.exception(f"[agent] thread={thread_id} 执行失败：{exc}")
            status = "failed"
            error = str(exc)
            update_task_status(thread_id, "failed", is_stream)
            emit(thread_id, "error", {"error": USER_FACING_ERROR_MESSAGE})
            return None
        finally:
            _record_run(
                thread_id=thread_id,
                user_id=user_id or "",
                turn_kind="resume" if should_resume else "chat",
                status=status,
                message=message or "",
                attachments=attachments,
                result=result,
                error=error,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )


def _record_run(
    thread_id: str,
    user_id: str,
    turn_kind: str,
    status: str,
    message: str,
    attachments: List[dict],
    result: Optional[dict],
    error: str,
    duration_ms: int,
) -> None:
    """把这一轮的执行情况写进 PostgreSQL（未配置时静默跳过，绝不影响返回）。"""
    summary = execution_store.summarize_state(result)
    # 图里判定的状态（例如 HITL 中断）优先于这里的兜底值
    final_status = summary["status"] if status == "completed" else status
    execution_store.record_run(
        thread_id=thread_id,
        user_id=user_id,
        turn_kind=turn_kind,
        status=final_status,
        request_text=message,
        attachments=[
            {"file_id": item.get("file_id"), "filename": item.get("filename")}
            for item in (attachments or [])
        ],
        actions=summary["actions"],
        answer=summary["answer"],
        error=error or summary["error"],
        duration_ms=duration_ms,
    )


def _push_interrupt_if_paused(thread_id: str, result: dict) -> None:
    """HITL 暂停时，给前端补一条可读提示（复用既有 SSE 事件协议）。"""
    interrupts = (result or {}).get("__interrupt__") or []
    if not interrupts:
        return
    payload = getattr(interrupts[0], "value", interrupts[0])
    payload = payload if isinstance(payload, dict) else {"value": payload}
    text = interrupt_text(payload)
    emit(thread_id, "delta", {"delta": text})
    emit(
        thread_id,
        "final",
        {"answer": text, "status": "completed", "type": "interrupt", "interrupt": payload},
    )


@agent_router.post("/chat")
async def agent_chat(request: AgentChatRequest, background_tasks: BackgroundTasks):
    """统一聊天入口。"""
    thread_id = request.thread_id or str(uuid.uuid4())
    attachments = [item.model_dump() for item in request.attachments]

    if request.stream:
        create_sse_queue(thread_id)
        background_tasks.add_task(
            _run_agent,
            thread_id,
            request.message,
            attachments,
            request.user_id,
            True,
            request.resume,
            request.new_turn,
        )
        return {"thread_id": thread_id, "message": "本次任务处理中...."}

    result = await run_in_threadpool(
        _run_agent,
        thread_id,
        request.message,
        attachments,
        request.user_id,
        False,
        request.resume,
        request.new_turn,
    )
    if result is None:
        raise HTTPException(status_code=500, detail=USER_FACING_ERROR_MESSAGE)
    return build_response(result)


@agent_router.get("/stream/{thread_id}")
async def agent_stream(thread_id: str, request: Request):
    """SSE 长连接（与 /stream/{session_id} 共用队列实现）。"""
    logger.info(f"[agent] thread={thread_id} 建立 SSE 长连接")
    return StreamingResponse(sse_generator(thread_id, request), media_type="text/event-stream")


@agent_router.get("/state/{thread_id}")
async def agent_state(thread_id: str):
    """查看线程状态：调试 HITL、排查多轮上下文用。"""
    state = await run_in_threadpool(get_thread_state, thread_id)
    return {"thread_id": thread_id, "state": state}


@agent_router.get("/files/{file_name}")
async def download_generated_file(file_name: str):
    """下载生成的业务申请书。"""
    safe_name = Path(file_name).name
    path = agent_config.outputs_dir / safe_name
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(
        path,
        filename=safe_name,
        media_type=(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
    )


@agent_router.get("/runs")
async def agent_runs(limit: int = 20, thread_id: str = ""):
    """查看执行记录（PostgreSQL）。没配 PG 时返回 configured=false，便于确认配置是否生效。"""
    from app.conf.pg_config import pg_config

    rows = await run_in_threadpool(execution_store.recent_runs, limit, thread_id)
    return {
        "configured": pg_config.configured,
        "target": pg_config.describe(),
        "count": len(rows),
        "runs": rows,
    }


@agent_router.get("/tools")
async def agent_tools(probe: bool = False):
    """MCP / 工具配置自检：MCP 的连接信息与工具名定义在 app/agent/mcp/builtin.py。"""
    info: dict = {
        "agent_enabled": agent_config.enabled,
        # 声明里有哪些 MCP、连哪里、是否配齐（密钥与其他连接信息见 builtin.py）
        "mcp_servers": {
            entry.slug: {
                "name": entry.name,
                "configured": entry.configured,
                "enabled": entry.enabled,
                "transport": entry.transport,
                "url": entry.url,
                "tools": entry.tools,
            }
            for entry in mcp_registry.list_servers()
        },
        "ocr": {
            "provider": "mcp",
            "input_mode": ocr_input_mode(),
            "tool_name": ocr_tool_name(),
        },
        "company": {"tool_name": company_search_tool_name()},
        "templates": sorted(path.name for path in agent_config.templates_dir.glob("*.docx")),
    }
    if probe:
        info["ocr"]["available_tools"] = list_tools(ocr_spec())
        info["company"]["available_tools"] = list_tools(company_spec())
    return info
