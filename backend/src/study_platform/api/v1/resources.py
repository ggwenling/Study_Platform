"""Authenticated personal knowledge library and conversation resources."""

from datetime import UTC, date, datetime, timedelta
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from study_platform.api.v1.auth import get_current_user
from study_platform.api.v1.materials import owned_kb
from study_platform.core.config import get_settings
from study_platform.core.errors import ApiError
from study_platform.persistence.database import get_db
from study_platform.persistence.models import (
    AiJob,
    AiJobAttempt,
    AiWorkflowRun,
    Conversation,
    IndexVersion,
    KnowledgeBase,
    Material,
    MaterialVersion,
    Message,
    MessageCitation,
)
from study_platform.services.citations import citation_dto, message_dto

router = APIRouter()


class NameBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=10000)

    @field_validator("name")
    @classmethod
    def trim_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("名称不能为空")
        return value.strip()


class KnowledgeBaseDTO(BaseModel):
    id: UUID
    name: str
    description: str | None
    created_at: datetime
    updated_at: datetime


class KnowledgeBasePage(BaseModel):
    items: list[KnowledgeBaseDTO]
    next_cursor: UUID | None


class ConversationBody(BaseModel):
    title: str = Field(default="新对话", min_length=1, max_length=200)

    @field_validator("title")
    @classmethod
    def trim_title(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("标题不能为空")
        return value.strip()


class ConversationDTO(BaseModel):
    id: UUID
    title: str | None
    created_at: datetime
    updated_at: datetime


class ConversationPage(BaseModel):
    items: list[ConversationDTO]
    next_cursor: UUID | None


class ScopeDTO(BaseModel):
    knowledge_base_id: UUID
    knowledge_base_name: str
    answer_mode: Literal["knowledge_only", "with_general"]
    index_version_ids: list[UUID]


class CitationDTO(BaseModel):
    source_label: str
    id: UUID
    material_id: UUID
    material_version_id: UUID
    knowledge_base_id: UUID
    title: str
    quote: str
    locator: dict
    location_label: str
    version_no: int
    availability: Literal["available", "unavailable"]
    unavailable_reason: str | None = None
    content_url: str | None = None
    preview_url: str | None = None


class MessageDTO(BaseModel):
    id: UUID
    conversation_id: UUID
    role: str
    content: str
    status: str
    sequence_no: int
    turn_id: UUID | None
    created_at: datetime
    scope: ScopeDTO | None
    citations: list[CitationDTO]


class MessagePage(BaseModel):
    items: list[MessageDTO]
    next_cursor: UUID | None


def kb_dto(kb):
    return {k: getattr(kb, k) for k in ("id", "name", "description", "created_at", "updated_at")}


def conversation_dto(row):
    return {k: getattr(row, k) for k in ("id", "title", "created_at", "updated_at")}


def owned_conversation(db, user, conversation_id, *, lock=False):
    query = sa.select(Conversation).where(
        Conversation.id == conversation_id,
        Conversation.owner_user_id == user.id,
        Conversation.deleted_at.is_(None),
    )
    row = db.scalar(query.with_for_update() if lock else query)
    if not row:
        raise ApiError(404, "not_found", "Resource not found")
    return row


def page_rows(db, stmt, model, limit, cursor, owner, scope=None):
    if cursor:
        anchor = db.scalar(
            sa.select(model).where(
                model.id == cursor, model.owner_user_id == owner, model.deleted_at.is_(None)
            )
        )
        if not anchor or (scope is not None and not scope(anchor)):
            raise ApiError(400, "invalid_cursor", "Invalid cursor")
        stmt = stmt.where(sa.tuple_(model.created_at, model.id) < (anchor.created_at, anchor.id))
    rows = db.scalars(
        stmt.order_by(model.created_at.desc(), model.id.desc()).limit(limit + 1)
    ).all()
    return rows[:limit], rows[limit - 1].id if len(rows) > limit else None


@router.get("/knowledge-bases", response_model=KnowledgeBasePage)
def list_knowledge_bases(
    limit: int = Query(20, ge=1, le=100),
    cursor: UUID | None = None,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    rows, next_cursor = page_rows(
        db,
        sa.select(KnowledgeBase).where(
            KnowledgeBase.owner_user_id == user.id, KnowledgeBase.deleted_at.is_(None)
        ),
        KnowledgeBase,
        limit,
        cursor,
        user.id,
    )
    return {"items": [kb_dto(r) for r in rows], "next_cursor": next_cursor}


@router.post("/knowledge-bases", response_model=KnowledgeBaseDTO, status_code=201)
def create_knowledge_base(
    body: NameBody, db: Session = Depends(get_db), user=Depends(get_current_user)
):
    row = KnowledgeBase(owner_user_id=user.id, name=body.name, description=body.description)
    db.add(row)
    db.commit()
    return kb_dto(row)


@router.get("/knowledge-bases/{kb_id}", response_model=KnowledgeBaseDTO)
def get_knowledge_base(kb_id: UUID, db: Session = Depends(get_db), user=Depends(get_current_user)):
    return kb_dto(owned_kb(db, user, kb_id))


@router.patch("/knowledge-bases/{kb_id}", response_model=KnowledgeBaseDTO)
def update_knowledge_base(
    kb_id: UUID, body: NameBody, db: Session = Depends(get_db), user=Depends(get_current_user)
):
    row = owned_kb(db, user, kb_id)
    row.name, row.description = body.name, body.description
    db.commit()
    return kb_dto(row)


@router.delete("/knowledge-bases/{kb_id}", status_code=204)
def delete_knowledge_base(
    kb_id: UUID, db: Session = Depends(get_db), user=Depends(get_current_user)
):
    row = db.scalar(
        sa.select(KnowledgeBase)
        .where(
            KnowledgeBase.id == kb_id,
            KnowledgeBase.owner_user_id == user.id,
            KnowledgeBase.deleted_at.is_(None),
        )
        .with_for_update()
    )
    if not row:
        raise ApiError(404, "not_found", "Resource not found")
    now = datetime.now(UTC)
    row.deleted_at = now
    materials = db.scalars(
        sa.select(Material)
        .where(Material.knowledge_base_id == row.id)
        .order_by(Material.id)
        .with_for_update()
    ).all()
    versions = sa.select(MaterialVersion.id).where(
        MaterialVersion.material_id.in_([m.id for m in materials])
    )
    jobs = db.scalars(
        sa.select(AiJob)
        .where(
            AiJob.owner_user_id == user.id,
            AiJob.job_type == "ingest",
            AiJob.resource_id.in_(versions),
            AiJob.status.in_(["queued", "running"]),
        )
        .order_by(AiJob.id)
        .with_for_update()
    ).all()
    for job in jobs:
        job.status, job.cancel_requested_at, job.finished_at = "cancelled", now, now
        job.fencing += 1
        db.execute(
            sa.update(AiJobAttempt)
            .where(AiJobAttempt.job_id == job.id, AiJobAttempt.finished_at.is_(None))
            .values(outcome="cancelled", finished_at=now)
        )
    runs = db.scalars(
        sa.select(AiWorkflowRun)
        .where(
            AiWorkflowRun.knowledge_base_id == row.id,
            AiWorkflowRun.owner_user_id == user.id,
            AiWorkflowRun.status.in_(["created", "running", "awaiting_student"]),
        )
        .order_by(AiWorkflowRun.id)
        .with_for_update()
    ).all()
    for run in runs:
        run.status, run.finished_at, run.lease_expires_at = "cancelled", now, None
        run.fencing += 1
        db.execute(
            sa.update(Message)
            .where(Message.workflow_run_id == run.id, Message.status.in_(["pending", "streaming"]))
            .values(status="cancelled")
        )
    db.commit()
    return Response(status_code=204)


@router.get("/conversations", response_model=ConversationPage)
def list_conversations(
    limit: int = Query(20, ge=1, le=100),
    cursor: UUID | None = None,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    rows, next_cursor = page_rows(
        db,
        sa.select(Conversation).where(
            Conversation.owner_user_id == user.id, Conversation.deleted_at.is_(None)
        ),
        Conversation,
        limit,
        cursor,
        user.id,
    )
    return {"items": [conversation_dto(r) for r in rows], "next_cursor": next_cursor}


@router.post("/conversations", response_model=ConversationDTO, status_code=201)
def create_conversation(
    body: ConversationBody, db: Session = Depends(get_db), user=Depends(get_current_user)
):
    row = Conversation(
        owner_user_id=user.id, course_id=None, title=body.title, mode="normal", scope_snapshot=[]
    )
    db.add(row)
    db.commit()
    return conversation_dto(row)


@router.get("/conversations/{conversation_id}", response_model=ConversationDTO)
def get_conversation(
    conversation_id: UUID, db: Session = Depends(get_db), user=Depends(get_current_user)
):
    return conversation_dto(owned_conversation(db, user, conversation_id))


@router.patch("/conversations/{conversation_id}", response_model=ConversationDTO)
def rename_conversation(
    conversation_id: UUID,
    body: ConversationBody,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    row = owned_conversation(db, user, conversation_id)
    row.title = body.title
    db.commit()
    return conversation_dto(row)


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(
    conversation_id: UUID, db: Session = Depends(get_db), user=Depends(get_current_user)
):
    row = owned_conversation(db, user, conversation_id, lock=True)
    now = datetime.now(UTC)
    row.deleted_at = now
    runs = db.scalars(
        sa.select(AiWorkflowRun)
        .where(
            AiWorkflowRun.conversation_id == row.id,
            AiWorkflowRun.owner_user_id == user.id,
            AiWorkflowRun.status.in_(["created", "running", "awaiting_student"]),
        )
        .with_for_update()
    ).all()
    for run in runs:
        run.status, run.finished_at, run.lease_expires_at = "cancelled", now, None
        run.fencing += 1
        db.execute(
            sa.update(Message)
            .where(Message.workflow_run_id == run.id, Message.status.in_(["pending", "streaming"]))
            .values(status="cancelled")
        )
    db.commit()
    return Response(status_code=204)


@router.get("/conversations/{conversation_id}/messages", response_model=MessagePage)
def list_messages(
    conversation_id: UUID,
    limit: int = Query(50, ge=1, le=100),
    cursor: UUID | None = None,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    owned_conversation(db, user, conversation_id)
    stmt = sa.select(Message).where(Message.conversation_id == conversation_id)
    if cursor:
        anchor = db.scalar(
            sa.select(Message).where(
                Message.id == cursor, Message.conversation_id == conversation_id
            )
        )
        if not anchor:
            raise ApiError(400, "invalid_cursor", "Invalid cursor")
        stmt = stmt.where(Message.sequence_no > anchor.sequence_no)
    rows = db.scalars(stmt.order_by(Message.sequence_no).limit(limit + 1)).all()
    return {
        "items": [message_dto(db, r) for r in rows[:limit]],
        "next_cursor": rows[limit - 1].id if len(rows) > limit else None,
    }


class SearchItem(BaseModel):
    id: UUID
    type: Literal["knowledge_base", "material", "conversation"]
    title: str
    knowledge_base_id: UUID | None = None


class SearchPage(BaseModel):
    items: list[SearchItem]
    next_cursor: UUID | None


@router.get("/search", response_model=SearchPage)
def search(
    q: str = Query(min_length=1, max_length=200),
    limit: int = Query(20, ge=1, le=100),
    cursor: UUID | None = None,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    term = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    kb = sa.select(
        KnowledgeBase.id.label("id"),
        sa.literal("knowledge_base").label("type"),
        KnowledgeBase.name.label("title"),
        KnowledgeBase.id.label("knowledge_base_id"),
    ).where(
        KnowledgeBase.owner_user_id == user.id,
        KnowledgeBase.deleted_at.is_(None),
        KnowledgeBase.name.ilike(term, escape="\\"),
    )
    materials = (
        sa.select(Material.id, sa.literal("material"), Material.title, Material.knowledge_base_id)
        .join(KnowledgeBase, KnowledgeBase.id == Material.knowledge_base_id)
        .where(
            Material.owner_user_id == user.id,
            Material.deleted_at.is_(None),
            KnowledgeBase.deleted_at.is_(None),
            Material.title.ilike(term, escape="\\"),
        )
    )
    conversations = sa.select(
        Conversation.id, sa.literal("conversation"), Conversation.title, sa.cast(sa.null(), sa.Uuid)
    ).where(
        Conversation.owner_user_id == user.id,
        Conversation.deleted_at.is_(None),
        Conversation.title.ilike(term, escape="\\"),
    )
    combined = sa.union_all(kb, materials, conversations).subquery()
    stmt = sa.select(combined)
    if cursor:
        stmt = stmt.where(combined.c.id > cursor)
    rows = db.execute(stmt.order_by(combined.c.id).limit(limit + 1)).mappings().all()
    return {
        "items": [dict(r) for r in rows[:limit]],
        "next_cursor": rows[limit - 1]["id"] if len(rows) > limit else None,
    }


class CapabilitiesDTO(BaseModel):
    rag: bool
    ocr: bool
    answer_modes: list[str]
    upload_mime_types: list[str]
    max_upload_bytes: int


@router.get("/capabilities", response_model=CapabilitiesDTO)
def capabilities(user=Depends(get_current_user)):
    from study_platform.api.v1.materials import MIMES

    settings = get_settings()
    return {
        "rag": bool(
            settings.deepseek_api_key.get_secret_value()
            and settings.embedding_api_key.get_secret_value()
        ),
        "ocr": True,
        "answer_modes": ["knowledge_only", "with_general"],
        "upload_mime_types": sorted(set(MIMES.values())),
        "max_upload_bytes": settings.max_upload_bytes,
    }


class ActivityDTO(BaseModel):
    date: date
    count: int


class DashboardDTO(BaseModel):
    knowledge_base_count: int
    material_count: int
    conversation_count: int
    completed_qa_count: int
    activity: list[ActivityDTO]


def activity_days(events, today):
    start = today - timedelta(days=today.weekday() + 20 * 7)
    counts = {}
    for event in events:
        day = event.astimezone(ZoneInfo("Asia/Shanghai")).date()
        if start <= day <= today:
            counts[day] = counts.get(day, 0) + 1
    return [
        {"date": start + timedelta(days=i), "count": counts.get(start + timedelta(days=i), 0)}
        for i in range((today - start).days + 1)
    ]


@router.get("/dashboard/overview", response_model=DashboardDTO)
def dashboard(db: Session = Depends(get_db), user=Depends(get_current_user)):
    kb_count = db.scalar(
        sa.select(sa.func.count())
        .select_from(KnowledgeBase)
        .where(KnowledgeBase.owner_user_id == user.id, KnowledgeBase.deleted_at.is_(None))
    )
    material_filter = [
        Material.owner_user_id == user.id,
        Material.deleted_at.is_(None),
        KnowledgeBase.deleted_at.is_(None),
    ]
    material_count = db.scalar(
        sa.select(sa.func.count())
        .select_from(Material)
        .join(KnowledgeBase, Material.knowledge_base_id == KnowledgeBase.id)
        .where(*material_filter)
    )
    conv_filter = [Conversation.owner_user_id == user.id, Conversation.deleted_at.is_(None)]
    conv_count = db.scalar(sa.select(sa.func.count()).select_from(Conversation).where(*conv_filter))
    # One event per ingested version, irrespective of retry/index generations.
    versions = db.execute(
        sa.select(MaterialVersion.id, sa.func.min(IndexVersion.ready_at))
        .join(IndexVersion, IndexVersion.material_version_id == MaterialVersion.id)
        .join(Material, Material.id == MaterialVersion.material_id)
        .join(KnowledgeBase, KnowledgeBase.id == Material.knowledge_base_id)
        .where(*material_filter, IndexVersion.ready_at.is_not(None))
        .group_by(MaterialVersion.id)
    ).all()
    runs = db.execute(
        sa.select(AiWorkflowRun.id, AiWorkflowRun.finished_at)
        .join(Conversation, Conversation.id == AiWorkflowRun.conversation_id)
        .where(
            *conv_filter,
            AiWorkflowRun.owner_user_id == user.id,
            AiWorkflowRun.status == "completed",
            AiWorkflowRun.source_scope["intent"].astext == "qa",
            AiWorkflowRun.finished_at.is_not(None),
        )
    ).all()
    events = [r[1] for r in versions] + [r[1] for r in runs]
    return {
        "knowledge_base_count": kb_count,
        "material_count": material_count,
        "conversation_count": conv_count,
        "completed_qa_count": len(runs),
        "activity": activity_days(events, datetime.now(ZoneInfo("Asia/Shanghai")).date()),
    }


@router.get("/sources/{citation_id}", response_model=CitationDTO)
def get_source(citation_id: UUID, db: Session = Depends(get_db), user=Depends(get_current_user)):
    citation = db.scalar(
        sa.select(MessageCitation)
        .join(Message, Message.id == MessageCitation.message_id)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(
            MessageCitation.id == citation_id,
            Conversation.owner_user_id == user.id,
            Conversation.deleted_at.is_(None),
        )
    )
    if not citation:
        raise ApiError(404, "not_found", "Resource not found")
    return citation_dto(db, citation)
