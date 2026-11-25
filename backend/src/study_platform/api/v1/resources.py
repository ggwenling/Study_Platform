"""Authenticated personal knowledge library and conversation resources."""

from datetime import UTC, date, datetime, timedelta
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from study_platform.api.v1.auth import get_current_user
from study_platform.api.v1.library_detail import active_chunks, public_locator
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
    MaterialChunk,
    MaterialVersion,
    Message,
    MessageCitation,
    Note,
)
from study_platform.services.citations import citation_dto, message_dto
from study_platform.services.learning import count_due_wrong_questions
from study_platform.services.lexical import lexical_query_terms, plain_tsquery
from study_platform.services.workspace_search import (
    body_score_expression,
    body_search_expression,
    decode_search_cursor,
    encode_search_cursor,
    matched_search_terms,
    search_excerpt,
    title_search_expressions,
)

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
    embedding_connection_id: UUID | None = None
    embedding_binding: dict | None = None
    retrieval_settings: dict | None = None


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
    project_knowledge_base_id: UUID | None
    title: str | None
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None


class ConversationPage(BaseModel):
    items: list[ConversationDTO]
    next_cursor: UUID | None


class ScopeDTO(BaseModel):
    knowledge_base_id: UUID
    knowledge_base_name: str
    answer_mode: Literal["knowledge_only", "with_general"]
    intent: Literal["qa", "summary"] = "qa"
    selected_material_ids: list[UUID] | None = None
    index_version_ids: list[UUID]


class CitationDTO(BaseModel):
    source_label: str
    id: UUID
    chunk_id: UUID
    material_id: UUID
    material_version_id: UUID
    knowledge_base_id: UUID
    title: str
    source_url: str | None = None
    quote: str
    quote_status: Literal["verified", "unverified", "unavailable"] | None = None
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
    return {k: getattr(kb, k) for k in ("id", "name", "description", "created_at", "updated_at",
                                      "embedding_connection_id", "embedding_binding", "retrieval_settings")}


def conversation_dto(row):
    return {k: getattr(row, k) for k in ("id", "title", "project_knowledge_base_id", "created_at", "updated_at", "archived_at")}


class CreateConversationBody(ConversationBody):
    project_knowledge_base_id: UUID | None = None


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
    from study_platform.services.model_connections import bind_library
    connection_id, binding = bind_library(db, user.id)
    row = KnowledgeBase(owner_user_id=user.id, name=body.name, description=body.description,
                        embedding_connection_id=connection_id, embedding_binding=binding)
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
    from study_platform.services import ai_jobs
    from study_platform.services.reindex import cancel_for_deleted_library

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
    db.execute(sa.update(Conversation).where(
        Conversation.owner_user_id == user.id, Conversation.project_knowledge_base_id == row.id,
    ).values(project_knowledge_base_id=None))
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
            AiJob.job_type.in_(("ingest", "classification")),
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
    cancel_for_deleted_library(db, row)
    ai_jobs.cancel_for_deleted_library(db, row)
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
    project_knowledge_base_id: UUID | None = None,
    archived: bool = False,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    if project_knowledge_base_id:
        owned_kb(db, user, project_knowledge_base_id)
    rows, next_cursor = page_rows(
        db,
        sa.select(Conversation).where(
            Conversation.owner_user_id == user.id, Conversation.deleted_at.is_(None),
            Conversation.archived_at.is_not(None) if archived else Conversation.archived_at.is_(None),
            *([Conversation.project_knowledge_base_id == project_knowledge_base_id] if project_knowledge_base_id else []),
        ),
        Conversation,
        limit,
        cursor,
        user.id,
        scope=lambda row: (row.archived_at is not None) == archived
        and (not project_knowledge_base_id or row.project_knowledge_base_id == project_knowledge_base_id),
    )
    return {"items": [conversation_dto(r) for r in rows], "next_cursor": next_cursor}


@router.post("/conversations", response_model=ConversationDTO, status_code=201)
def create_conversation(
    body: CreateConversationBody, db: Session = Depends(get_db), user=Depends(get_current_user)
):
    if body.project_knowledge_base_id:
        owned_kb(db, user, body.project_knowledge_base_id, lock=True)
    row = Conversation(
        owner_user_id=user.id, course_id=None, title=body.title, mode="normal", scope_snapshot=[],
        project_knowledge_base_id=body.project_knowledge_base_id,
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


class ConversationArchiveBody(BaseModel):
    archived: bool


@router.patch("/conversations/{conversation_id}/archive", response_model=ConversationDTO)
def set_conversation_archive(
    conversation_id: UUID, body: ConversationArchiveBody,
    db: Session = Depends(get_db), user=Depends(get_current_user),
):
    row = owned_conversation(db, user, conversation_id, lock=True)
    if body.archived != (row.archived_at is not None):
        row.archived_at = datetime.now(UTC) if body.archived else None
        # Archiving should not change when the last conversation activity took place.
        row.updated_at = Conversation.updated_at
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
    type: Literal["knowledge_base", "material", "conversation", "fragment", "note"]
    title: str
    knowledge_base_id: UUID | None = None
    excerpt: str | None = None
    locator: dict | None = None
    retrieval_enabled: bool | None = None
    matched_terms: list[str] = Field(default_factory=list)


class SearchPage(BaseModel):
    items: list[SearchItem]
    next_cursor: str | None


@router.get("/search", response_model=SearchPage, response_model_exclude_unset=True)
def search(
    q: str = Query(min_length=1, max_length=200),
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = None,
    include_content: bool = False,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    anchor = None
    if cursor is not None:
        try:
            anchor = decode_search_cursor(cursor)
        except ValueError as exc:
            raise ApiError(400, "invalid_cursor", "Invalid cursor") from exc
    q = q.strip()
    if not q:
        return {"items": [], "next_cursor": None}

    terms = lexical_query_terms(q)
    tsquery = plain_tsquery(q)
    no_text = sa.cast(sa.null(), sa.Text).label("match_text")
    no_locator = sa.cast(sa.null(), JSONB).label("locator")
    no_retrieval_flag = sa.cast(sa.null(), sa.Boolean).label("retrieval_enabled")

    kb_title_match, kb_title_score = title_search_expressions(KnowledgeBase.name, q, terms)
    kb = sa.select(
        KnowledgeBase.id.label("id"),
        sa.literal("knowledge_base").label("type"),
        KnowledgeBase.name.label("title"),
        KnowledgeBase.id.label("knowledge_base_id"),
        no_text,
        no_locator,
        no_retrieval_flag,
        kb_title_score.label("score"),
        sa.literal(0).label("type_rank"),
    ).where(
        KnowledgeBase.owner_user_id == user.id,
        KnowledgeBase.deleted_at.is_(None),
        kb_title_match,
    )
    material_title_match, material_title_score = title_search_expressions(Material.title, q, terms)
    materials = (
        sa.select(
            Material.id.label("id"),
            sa.literal("material").label("type"),
            Material.title.label("title"),
            Material.knowledge_base_id.label("knowledge_base_id"),
            no_text,
            no_locator,
            no_retrieval_flag,
            material_title_score.label("score"),
            sa.literal(1).label("type_rank"),
        )
        .join(KnowledgeBase, KnowledgeBase.id == Material.knowledge_base_id)
        .where(
            Material.owner_user_id == user.id,
            Material.deleted_at.is_(None),
            KnowledgeBase.deleted_at.is_(None),
            material_title_match,
        )
    )
    conversation_title_match, conversation_title_score = title_search_expressions(
        Conversation.title, q, terms
    )
    conversations = sa.select(
        Conversation.id.label("id"),
        sa.literal("conversation").label("type"),
        Conversation.title.label("title"),
        sa.cast(sa.null(), sa.Uuid).label("knowledge_base_id"),
        no_text,
        no_locator,
        no_retrieval_flag,
        conversation_title_score.label("score"),
        sa.literal(3).label("type_rank"),
    ).where(
        Conversation.owner_user_id == user.id,
        Conversation.deleted_at.is_(None),
        conversation_title_match,
    )
    queries = [kb, materials, conversations]
    if include_content:
        fragment_body_match = body_search_expression(
            MaterialChunk.text, MaterialChunk.lexical, q, tsquery
        )
        fragment_body_score = body_score_expression(MaterialChunk.lexical, terms, tsquery)
        fragment_matches = active_chunks(sa.select(
            MaterialChunk.id.label("id"),
            sa.literal("fragment").label("type"),
            Material.title.label("title"),
            Material.knowledge_base_id.label("knowledge_base_id"),
            MaterialChunk.text.label("match_text"),
            MaterialChunk.locator.label("locator"),
            MaterialChunk.retrieval_enabled.label("retrieval_enabled"),
            fragment_body_score.label("score"),
            sa.literal(4).label("type_rank"),
        )).join(KnowledgeBase, KnowledgeBase.id == Material.knowledge_base_id).where(
            KnowledgeBase.owner_user_id == user.id,
            KnowledgeBase.deleted_at.is_(None),
            Material.owner_user_id == user.id,
            Material.deleted_at.is_(None),
            Material.status == "active",
            MaterialVersion.parse_status == "ready",
            fragment_body_match,
        )
        note_title_match, note_title_score = title_search_expressions(Note.title, q, terms)
        note_body_match = body_search_expression(Note.content_md, Note.lexical, q, tsquery)
        note_body_score = body_score_expression(Note.lexical, terms, tsquery)
        note_matches = sa.select(
            Note.id.label("id"),
            sa.literal("note").label("type"),
            Note.title.label("title"),
            KnowledgeBase.id.label("knowledge_base_id"),
            sa.case((note_body_match, Note.content_md), else_=no_text).label("match_text"),
            no_locator,
            no_retrieval_flag,
            (note_title_score + note_body_score).label("score"),
            sa.literal(2).label("type_rank"),
        ).select_from(Note).join(KnowledgeBase, KnowledgeBase.id == Note.course_id).where(
            KnowledgeBase.owner_user_id == user.id,
            KnowledgeBase.deleted_at.is_(None),
            Note.owner_user_id == user.id,
            Note.deleted_at.is_(None),
            sa.or_(note_title_match, note_body_match),
        )
        queries.extend((fragment_matches, note_matches))
    combined = sa.union_all(*queries).subquery()
    stmt = sa.select(combined)
    if anchor is not None:
        cursor_score, cursor_type_rank, cursor_id = anchor
        stmt = stmt.where(
            sa.or_(
                combined.c.score < cursor_score,
                sa.and_(
                    combined.c.score == cursor_score,
                    combined.c.type_rank > cursor_type_rank,
                ),
                sa.and_(
                    combined.c.score == cursor_score,
                    combined.c.type_rank == cursor_type_rank,
                    combined.c.id > cursor_id,
                ),
            )
        )
    rows = db.execute(
        stmt.order_by(combined.c.score.desc(), combined.c.type_rank, combined.c.id).limit(limit + 1)
    ).mappings().all()
    items = []
    for row in rows[:limit]:
        item = {key: row[key] for key in ("id", "type", "title", "knowledge_base_id")}
        if row.get("match_text") is not None:
            item["excerpt"] = search_excerpt(row["match_text"], q, terms)
        if row["type"] == "fragment":
            item["locator"] = public_locator(row["locator"])
            item["retrieval_enabled"] = row["retrieval_enabled"]
        if include_content:
            item["matched_terms"] = matched_search_terms(
                row["title"] or "", row.get("match_text"), q, terms
            )
        items.append(item)
    return {
        "items": items,
        "next_cursor": (
            encode_search_cursor(rows[limit - 1]["score"], rows[limit - 1]["type_rank"], rows[limit - 1]["id"])
            if len(rows) > limit else None
        ),
    }


class CapabilitiesDTO(BaseModel):
    rag: bool
    library_rag: dict[str, bool] = Field(default_factory=dict)
    ocr: bool
    vision: bool
    rerank: bool
    retrieval_defaults: dict[str, int | float | bool]
    answer_modes: list[str]
    upload_mime_types: list[str]
    max_upload_bytes: int
    max_pdf_upload_bytes: int
    max_pdf_pages: int


@router.get("/capabilities", response_model=CapabilitiesDTO)
def capabilities(user=Depends(get_current_user), db: Session = Depends(get_db)):
    from study_platform.api.v1.materials import MIMES
    from study_platform.persistence.models import ModelConnection
    from study_platform.services.model_connections import default_snapshot
    settings = get_settings()
    available = {}
    for kind, platform_key in (("llm", settings.deepseek_api_key), ("embedding", settings.embedding_api_key)):
        try:
            personal = default_snapshot(db, user.id, kind)
            available[kind] = bool(personal.get("id") or platform_key.get_secret_value())
        except ApiError:
            available[kind] = False
    libraries = db.execute(sa.select(KnowledgeBase.id, KnowledgeBase.embedding_connection_id, ModelConnection.status)
        .outerjoin(ModelConnection, ModelConnection.id == KnowledgeBase.embedding_connection_id)
        .where(KnowledgeBase.owner_user_id == user.id, KnowledgeBase.deleted_at.is_(None))).all()
    try:
        vision = bool(default_snapshot(db, user.id, "vision").get("id"))
    except ApiError:
        vision = False
    try:
        rerank = bool(default_snapshot(db, user.id, "rerank").get("id"))
    except ApiError:
        rerank = False
    library_rag = {str(kb): available["llm"] and (status == "ready" if connection else bool(settings.embedding_api_key.get_secret_value()))
                   for kb, connection, status in libraries}
    return {
        "rag": available["llm"] and (available["embedding"] or any(library_rag.values())),
        "library_rag": library_rag,
        "ocr": True,
        "vision": vision,
        "rerank": rerank,
        "retrieval_defaults": {
            "top_k": settings.retrieval_top_k,
            "lexical_weight": settings.retrieval_lexical_weight,
            "min_similarity": settings.retrieval_min_similarity,
            "rerank_enabled": False,
            "rerank_candidate_k": settings.retrieval_candidate_k,
        },
        "answer_modes": ["knowledge_only", "with_general"],
        "upload_mime_types": sorted(set(MIMES.values())),
        "max_upload_bytes": settings.max_upload_bytes,
        "max_pdf_upload_bytes": settings.max_pdf_upload_bytes,
        "max_pdf_pages": settings.max_pdf_pages,
    }


class ActivityDTO(BaseModel):
    date: date
    count: int


class DashboardDTO(BaseModel):
    knowledge_base_count: int
    material_count: int
    conversation_count: int
    completed_qa_count: int
    due_flashcard_count: int
    due_wrong_question_count: int
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
    # Local import: services.studio loads this package, and a module-level import
    # reads count_due_flashcards before that module finishes initializing.
    from study_platform.services.studio import count_due_flashcards

    now = datetime.now(UTC)
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
        "due_flashcard_count": count_due_flashcards(db, user.id, now),
        "due_wrong_question_count": count_due_wrong_questions(db, user.id, now),
        "activity": activity_days(events, now.astimezone(ZoneInfo("Asia/Shanghai")).date()),
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
