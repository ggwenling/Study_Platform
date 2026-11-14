"""Authenticated POST-SSE chat endpoints."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from starlette.concurrency import run_in_threadpool

from study_platform.api.v1.auth import get_current_user
from study_platform.api.v1.resources import MessageDTO, ScopeDTO
from study_platform.services import chat

router = APIRouter()


class ChatRequest(BaseModel):
    conversation_id: UUID | None = None
    knowledge_base_id: UUID
    answer_mode: Literal["knowledge_only", "with_general"] = "knowledge_only"
    intent: Literal["qa", "summary"] = "qa"
    content: str = Field(min_length=1, max_length=8000)
    material_ids: list[UUID] | None = Field(default=None, max_length=50)

    @field_validator("content")
    @classmethod
    def content_not_blank(cls, value):
        if not value.strip():
            raise ValueError("content must not be blank")
        return value.strip()


class TurnDTO(BaseModel):
    turn_id: UUID
    conversation_id: UUID
    status: str
    scope: ScopeDTO
    messages: list[MessageDTO]
    error_code: str | None


def response(owner, prepared):
    return StreamingResponse(chat.stream(owner, *prepared), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/chat/stream", response_class=StreamingResponse, responses={200: {"content": {"text/event-stream": {}}}})
async def send(body: ChatRequest, user=Depends(get_current_user),
               idempotency_key: str = Header(min_length=1, max_length=128)):
    prepared = await run_in_threadpool(chat.prepare, user.id, body.model_dump(mode="json"),
                                      idempotency_key)
    return response(user.id, prepared)


@router.get("/conversations/{conversation_id}/turns/{turn_id}", response_model=TurnDTO)
def get_turn(conversation_id: UUID, turn_id: UUID, user=Depends(get_current_user)):
    return chat.turn(user.id, conversation_id, turn_id)


@router.post("/conversations/{conversation_id}/turns/{turn_id}/cancel", response_model=TurnDTO)
def cancel(conversation_id: UUID, turn_id: UUID, user=Depends(get_current_user)):
    return chat.cancel(user.id, conversation_id, turn_id)


@router.post("/conversations/{conversation_id}/turns/{turn_id}/retry",
             response_class=StreamingResponse, responses={200: {"content": {"text/event-stream": {}}}})
async def retry(conversation_id: UUID, turn_id: UUID, user=Depends(get_current_user)):
    prepared = await run_in_threadpool(chat.retry, user.id, conversation_id, turn_id)
    return response(user.id, prepared)
