"""Agent Memory HTTP API。"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.logger import logger
from app.memory.coordinator import get_memory_coordinator


memory_router = APIRouter(prefix="/memory", tags=["memory"])


class AddRequest(BaseModel):
    messages: Any
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    run_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    infer: bool = True


class SearchRequest(BaseModel):
    query: str
    scope: str
    top_k: int = 10
    rerank: bool = False
    explain: bool = False


class UpdateRequest(BaseModel):
    data: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


@memory_router.post("/add")
async def add_memory(request: AddRequest):
    try:
        memories = get_memory_coordinator().add(
            messages=request.messages,
            user_id=request.user_id,
            agent_id=request.agent_id,
            run_id=request.run_id,
            metadata=request.metadata,
            infer=request.infer,
        )
        return {"code": 200, "data": [memory.model_dump(mode="json") for memory in memories]}
    except Exception as exc:
        logger.error(f"memory add 失败：{exc}")
        raise HTTPException(status_code=400, detail=str(exc))


@memory_router.get("/search")
async def search_memory(
    query: str,
    scope: str,
    top_k: int = 10,
    rerank: bool = False,
    explain: bool = False,
):
    """长期记忆检索接口（只返回 long_term_memories）。

    短期对话记录请用 /history/{session_id}。
    """
    try:
        result = get_memory_coordinator().search(
            query=query,
            scope=scope,
            top_k=top_k,
            rerank=rerank,
            explain=explain,
        )
        return {"code": 200, "data": result}
    except Exception as exc:
        logger.error(f"memory search 失败：{exc}")
        raise HTTPException(status_code=400, detail=str(exc))


@memory_router.get("/{memory_id}")
async def get_memory(memory_id: str, scope: str):
    memory = get_memory_coordinator().get(memory_id, scope)
    if not memory:
        raise HTTPException(status_code=404, detail="memory not found")
    return {"code": 200, "data": memory.model_dump(mode="json")}


@memory_router.put("/{memory_id}")
async def update_memory(memory_id: str, scope: str, request: UpdateRequest):
    memory = get_memory_coordinator().update(
        memory_id,
        scope,
        data=request.data,
        metadata=request.metadata,
    )
    if not memory:
        raise HTTPException(status_code=404, detail="memory not found")
    return {"code": 200, "data": memory.model_dump(mode="json")}


@memory_router.delete("/{memory_id}")
async def delete_memory(memory_id: str, scope: str):
    deleted = get_memory_coordinator().delete(memory_id, scope)
    if not deleted:
        raise HTTPException(status_code=404, detail="memory not found")
    return {"code": 200, "deleted": True}


@memory_router.get("/{memory_id}/history")
async def memory_history(memory_id: str, scope: str):
    records = get_memory_coordinator().history(memory_id, scope)
    return {"code": 200, "data": [record.model_dump(mode="json") for record in records]}


@memory_router.post("/lifecycle/run")
async def run_lifecycle(scope: str):
    result = get_memory_coordinator().run_lifecycle(scope)
    return {"code": 200, "data": result}


@memory_router.post("/consolidate")
async def consolidate(scope: str):
    count = get_memory_coordinator().consolidate(scope)
    return {"code": 200, "data": {"created": count}}
