"""Owner-scoped library dashboard and fragment management."""

from datetime import date, datetime, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, StrictBool
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from study_platform.api.v1.auth import get_current_user
from study_platform.api.v1.materials import missing, owned_kb, owned_material
from study_platform.core.errors import ApiError
from study_platform.persistence.database import get_db
from study_platform.persistence.models.identity import Material
from study_platform.persistence.models.materials import (
    IndexVersion,
    MaterialChunk,
    MaterialSection,
    MaterialVersion,
)
from study_platform.persistence.models.operations import AiJob
from study_platform.services import classification, rag_workshop

router = APIRouter(prefix="/knowledge-bases/{kb_id}")
LOCAL_ZONE = "Asia/Shanghai"
PAGE_SIZE = 20


class ActivityDay(BaseModel):
    date: date
    count: int


class LibraryOverview(BaseModel):
    file_count: int
    ready_count: int
    parsed_count: int
    fragment_count: int
    cut_count: int
    embedded_count: int
    ocr_count: int
    ocr_queue: int
    ocr_material_ids: list[UUID]
    processing_count: int
    failed_count: int
    last_synced_at: datetime | None
    topics: list[str]
    tags: list[str]
    activity: list[ActivityDay]


class DocumentLocation(BaseModel):
    id: UUID
    heading: str | None
    locator: dict
    count: int


class Fragment(BaseModel):
    id: UUID
    text: str
    material_id: UUID
    title: str
    mime_type: str
    version_id: UUID
    locator: dict
    estimated_tokens: int
    retrieval_enabled: bool
    status: str
    ready_at: datetime


class FragmentPage(BaseModel):
    total: int
    page_size: int
    items: list[Fragment]


class ClassificationState(classification.Classification):
    ready: bool
    suggestion_id: UUID | None
    status: str
    suggestion: classification.Classification | None


def library_files(kb):
    return (Material.knowledge_base_id == kb.id, Material.owner_user_id == kb.owner_user_id,
            Material.deleted_at.is_(None), Material.status == "active")


def current_versions(query):
    return (query.select_from(Material)
            .join(MaterialVersion, sa.and_(MaterialVersion.material_id == Material.id,
                                           MaterialVersion.is_current.is_(True))))


def active_chunks(query):
    return (current_versions(query)
            .join(IndexVersion, sa.and_(IndexVersion.material_version_id == MaterialVersion.id,
                                       IndexVersion.is_active.is_(True), IndexVersion.status == "ready"))
            .join(MaterialChunk, MaterialChunk.index_version_id == IndexVersion.id))


def overview(db, kb):
    job = (sa.select(AiJob.status, AiJob.result).where(
        AiJob.job_type == "ingest", AiJob.resource_id == MaterialVersion.id,
    ).order_by(AiJob.created_at.desc(), AiJob.id.desc()).limit(1).lateral())
    files = db.execute(
        sa.select(Material.id, Material.topic, Material.tags, job.c.status, job.c.result,
                  MaterialVersion.parse_status, IndexVersion.chunk_count)
        .select_from(Material)
        .outerjoin(MaterialVersion, sa.and_(MaterialVersion.material_id == Material.id,
                                            MaterialVersion.is_current.is_(True)))
        .outerjoin(job, sa.true())
        .outerjoin(IndexVersion, sa.and_(IndexVersion.material_version_id == MaterialVersion.id,
                                         IndexVersion.is_active.is_(True)))
        .where(*library_files(kb))
    ).all()
    locator = MaterialSection.locator
    ocr_locations = current_versions(sa.select(
        Material.id.label("material_id"), MaterialVersion.id, locator["page"].astext, locator["image"].astext,
    )).join(MaterialSection, MaterialSection.material_version_id == MaterialVersion.id).where(
        *library_files(kb), locator.has_key("ocr"),
    ).distinct().subquery()
    today = datetime.now(ZoneInfo(LOCAL_ZONE)).date()
    start = today - timedelta(days=today.weekday() + 11 * 7)
    first_index = (
        sa.select(sa.func.min(IndexVersion.ready_at).label("ready_at")).select_from(Material)
        .join(MaterialVersion, MaterialVersion.material_id == Material.id)
        .join(IndexVersion, IndexVersion.material_version_id == MaterialVersion.id)
        .where(*library_files(kb), IndexVersion.ready_at.is_not(None))
        .group_by(MaterialVersion.id).subquery()
    )
    day = sa.cast(sa.func.timezone(LOCAL_ZONE, first_index.c.ready_at), sa.Date)
    activity = dict(db.execute(sa.select(day, sa.func.count()).where(
        day.between(start, today)).group_by(day)).all())
    progress = [row.result or {} for row in files]
    ocr_count = db.scalar(sa.select(sa.func.count()).select_from(ocr_locations))
    ocr_ids = set(db.scalars(sa.select(ocr_locations.c.material_id).distinct()))
    ocr_ids.update(row.id for row, detail in zip(files, progress, strict=True) if detail.get("ocr_count"))
    return {
        "file_count": len(files),
        "ready_count": sum(count is not None for *_, count in files),
        "parsed_count": sum(status == "ready" or detail.get("parsed", False)
                            for (*_, status, _), detail in zip(files, progress, strict=True)),
        "fragment_count": sum(count or 0 for *_, count in files),
        "cut_count": sum(count if count is not None else detail.get("chunk_count", 0)
                         for (*_, count), detail in zip(files, progress, strict=True)),
        "embedded_count": sum(count if count is not None else detail.get("embedded_count", 0)
                              for (*_, count), detail in zip(files, progress, strict=True)),
        "ocr_count": ocr_count + sum(detail.get("ocr_count", 0)
                                    for (*_, count), detail in zip(files, progress, strict=True)
                                    if count is None),
        "ocr_material_ids": sorted(ocr_ids),
        "ocr_queue": sum(row.status == "running" and detail.get("phase") == "ocr"
                         for row, detail in zip(files, progress, strict=True)),
        "processing_count": sum(row.status in ("queued", "running") for row in files),
        "failed_count": sum(row.status in ("failed", "cancelled") or row.parse_status == "failed"
                            for row in files),
        "last_synced_at": kb.last_synced_at,
        "topics": sorted({row.topic for row in files if row.topic}),
        "tags": sorted({tag for row in files for tag in row.tags}),
        "activity": [{"date": start + timedelta(days=i),
                      "count": activity.get(start + timedelta(days=i), 0)}
                     for i in range((today - start).days + 1)],
    }


@router.get("/overview", response_model=LibraryOverview)
def get_overview(kb_id: UUID, db: Session = Depends(get_db), user=Depends(get_current_user)):
    return overview(db, owned_kb(db, user, kb_id))


@router.post("/sync", response_model=LibraryOverview)
def sync(kb_id: UUID, db: Session = Depends(get_db), user=Depends(get_current_user)):
    kb = owned_kb(db, user, kb_id, lock=True)
    result = overview(db, kb)
    timestamp = datetime.now(timezone.utc)
    db.execute(sa.update(type(kb)).where(type(kb).id == kb_id)
               .values(last_synced_at=timestamp, updated_at=kb.updated_at))
    db.commit()
    return {**result, "last_synced_at": timestamp}


def library_material(db, user, kb_id, material_id, *, lock=False):
    owned_kb(db, user, kb_id, lock=lock)
    material = owned_material(db, user, material_id)
    if material.knowledge_base_id != kb_id or material.status != "active":
        raise missing()
    if lock:
        db.refresh(material, with_for_update=True)
    return material


@router.get("/documents/{material_id}/locations", response_model=list[DocumentLocation])
def locations(kb_id: UUID, material_id: UUID, db: Session = Depends(get_db),
              user=Depends(get_current_user)):
    library_material(db, user, kb_id, material_id)
    # Group fragments by the original extracted section. Omit OCR bounding boxes.
    rows = db.execute(active_chunks(sa.select(
        MaterialSection.id, MaterialSection.heading, MaterialSection.locator, sa.func.count(),
    )).join(MaterialSection, MaterialSection.id == MaterialChunk.section_id)
        .where(Material.id == material_id)
        .group_by(MaterialSection.id).order_by(MaterialSection.ordinal)).all()
    return [{"id": key, "heading": heading, "locator": public_locator(locator), "count": count}
            for key, heading, locator, count in rows]


def public_locator(locator):
    return {key: value for key, value in locator.items() if key != "ocr"}


def estimated_tokens(text):
    ascii_count = sum(char.isascii() for char in text)
    return len(text) - ascii_count + (ascii_count + 3) // 4


def fragment_query(kb):
    phase = (sa.select(AiJob.status).where(AiJob.job_type == "ingest",
                                          AiJob.resource_id == MaterialVersion.id)
             .order_by(AiJob.created_at.desc(), AiJob.id.desc()).limit(1).scalar_subquery())
    return active_chunks(sa.select(
        MaterialChunk, Material.id.label("material_id"), Material.title, MaterialVersion.source_mime,
        IndexVersion.ready_at, sa.func.coalesce(phase, "succeeded").label("processing_status"),
    )).where(*library_files(kb))


def fragment_dto(row):
    chunk, source_id, title, mime, ready_at, state = row
    return {
        "id": chunk.id, "text": chunk.text, "material_id": source_id, "title": title,
        "mime_type": mime, "version_id": chunk.material_version_id,
        "locator": public_locator(chunk.locator), "estimated_tokens": estimated_tokens(chunk.text),
        "retrieval_enabled": chunk.retrieval_enabled, "status": state, "ready_at": ready_at,
    }


@router.get("/fragments", response_model=FragmentPage)
def fragments(
    kb_id: UUID, q: str = Query("", max_length=200), file_type: str | None = None,
    status: str | None = None, tag: str | None = Query(None, max_length=80), day: date | None = None,
    material_id: UUID | None = None, section_id: UUID | None = None,
    page: int = Query(1, ge=1), db: Session = Depends(get_db), user=Depends(get_current_user),
):
    kb = owned_kb(db, user, kb_id)
    stmt = fragment_query(kb)
    if q.strip():
        pattern = "%" + q.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        tags = sa.func.jsonb_array_elements_text(Material.tags).column_valued("tag_name")
        stmt = stmt.where(sa.or_(MaterialChunk.text.ilike(pattern), Material.title.ilike(pattern),
                                 sa.exists(sa.select(1).where(sa.cast(tags, sa.Text).ilike(pattern)))))
    if file_type:
        stmt = stmt.where(MaterialVersion.source_mime.startswith(file_type, autoescape=True))
    if status:
        stmt = stmt.where(stmt.selected_columns.processing_status == status)
    if tag:
        stmt = stmt.where(Material.tags.contains(sa.cast(sa.literal([tag], type_=JSONB), JSONB)))
    if day:
        stmt = stmt.where(sa.cast(sa.func.timezone(LOCAL_ZONE, IndexVersion.ready_at), sa.Date) == day)
    if material_id:
        stmt = stmt.where(Material.id == material_id)
    if section_id:
        stmt = stmt.where(MaterialChunk.section_id == section_id)
    total = db.scalar(sa.select(sa.func.count()).select_from(stmt.subquery()))
    rows = db.execute(stmt.order_by(IndexVersion.ready_at.desc(), Material.id, MaterialChunk.ordinal)
                      .offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)).all()
    return {
        "total": total, "page_size": PAGE_SIZE,
        "items": [fragment_dto(row) for row in rows],
    }


class RetrievalUpdate(BaseModel):
    ids: list[UUID] = Field(min_length=1, max_length=PAGE_SIZE)
    enabled: StrictBool


@router.patch("/fragments/retrieval", status_code=204)
def update_retrieval(kb_id: UUID, body: RetrievalUpdate, db: Session = Depends(get_db),
                     user=Depends(get_current_user)):
    kb = owned_kb(db, user, kb_id, lock=True)
    ids = set(body.ids)
    owned = db.scalars(current_versions(sa.select(MaterialChunk.id))
                       .join(MaterialChunk, MaterialChunk.material_version_id == MaterialVersion.id)
                       .where(*library_files(kb), MaterialChunk.id.in_(ids))).all()
    if set(owned) != ids:
        # A replaced version in this library is stale, while foreign IDs stay opaque.
        historical = db.scalars(sa.select(MaterialChunk.id).select_from(Material)
                                .join(MaterialVersion, MaterialVersion.material_id == Material.id)
                                .join(MaterialChunk, MaterialChunk.material_version_id == MaterialVersion.id)
                                .where(*library_files(kb), MaterialChunk.id.in_(ids))).all()
        if set(historical) != ids:
            raise missing()
    live = db.scalars(active_chunks(sa.select(MaterialChunk.id))
                      .where(*library_files(kb), MaterialChunk.id.in_(ids))).all()
    if set(live) != ids:
        raise ApiError(409, "index_changed", "片段索引已更新，请刷新后重试。")
    db.execute(sa.update(MaterialChunk).where(MaterialChunk.id.in_(ids))
               .values(retrieval_enabled=body.enabled))
    db.commit()


def classification_state(material, index, job):
    result = job.result or {} if job else {}
    return {
        "topic": material.topic, "tags": material.tags, "ready": index is not None,
        "suggestion_id": job.id if job else None,
        "status": result.get("decision", job.status) if job else "missing",
        "suggestion": result.get("suggestion") if "decision" not in result else None,
    }


@router.get("/documents/{material_id}/classification", response_model=ClassificationState)
def get_classification(kb_id: UUID, material_id: UUID, db: Session = Depends(get_db),
                       user=Depends(get_current_user)):
    material = library_material(db, user, kb_id, material_id)
    index = classification.active_index(db, material.id)
    return classification_state(material, index, classification.latest_suggestion(db, index) if index else None)


@router.post("/documents/{material_id}/classification", response_model=ClassificationState)
def generate(kb_id: UUID, material_id: UUID, db: Session = Depends(get_db), user=Depends(get_current_user)):
    material = library_material(db, user, kb_id, material_id, lock=True)
    index = classification.active_index(db, material.id)
    if not index:
        raise ApiError(409, "index_unavailable", "资料索引就绪后才能生成建议。")
    job = classification.latest_suggestion(db, index)
    if not job or job.status not in ("queued", "running"):
        job = classification.enqueue(db, user.id, index)
    result = classification_state(material, index, job)
    db.commit()
    return result


class ClassificationDecision(classification.Classification):
    suggestion_id: UUID
    ignore: StrictBool = False


@router.patch("/documents/{material_id}/classification", response_model=ClassificationState)
def confirm(kb_id: UUID, material_id: UUID, body: ClassificationDecision,
            db: Session = Depends(get_db), user=Depends(get_current_user)):
    material = library_material(db, user, kb_id, material_id, lock=True)
    index = classification.active_index(db, material.id)
    job = classification.latest_suggestion(db, index) if index else None
    if not job or job.id != body.suggestion_id or job.status != "succeeded" or "decision" in job.result:
        raise ApiError(409, "suggestion_changed", "分类建议已更新，请重新打开。")
    if not body.ignore:
        material.topic, material.tags = body.topic, body.tags
    job.result = {**job.result, "decision": "ignored" if body.ignore else "confirmed"}
    result = classification_state(material, index, job)
    db.commit()
    return result


class FragmentEditBody(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


class HitTestBody(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(default=6, ge=1, le=20)
    lexical_weight: float = Field(default=0.4, ge=0.0, le=2.0)
    material_ids: list[UUID] | None = Field(default=None, max_length=50)


class HitTestItem(BaseModel):
    chunk_id: UUID
    material_id: UUID
    version_id: UUID
    title: str
    locator: dict
    location_label: str
    text: str
    retrieval_enabled: bool
    vector_rank: int | None
    vector_similarity: float | None
    above_threshold: bool
    lexical_rank: int | None
    lexical_score: float | None
    matched_terms: list[str]
    rrf_score: float


class HitTestResponse(BaseModel):
    query: str
    query_terms: list[str]
    lexical_weight: float
    min_similarity_threshold: float
    items: list[HitTestItem]


@router.patch("/fragments/{chunk_id}", response_model=Fragment)
def edit_fragment(
    kb_id: UUID,
    chunk_id: UUID,
    body: FragmentEditBody,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    return rag_workshop.edit_fragment_text(
        db, user=user, kb_id=kb_id, chunk_id=chunk_id, text=body.text
    )


@router.get("/fragments/{chunk_id}", response_model=Fragment)
def get_fragment(kb_id: UUID, chunk_id: UUID, db: Session = Depends(get_db), user=Depends(get_current_user)):
    kb = owned_kb(db, user, kb_id)
    row = db.execute(fragment_query(kb).where(
        MaterialChunk.id == chunk_id, MaterialVersion.parse_status == "ready",
    )).one_or_none()
    if row is None:
        raise missing()
    return fragment_dto(row)


@router.post("/hit-test", response_model=HitTestResponse)
def hit_test(
    kb_id: UUID,
    body: HitTestBody,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    return rag_workshop.run_hit_test(
        db,
        user=user,
        kb_id=kb_id,
        query=body.query,
        top_k=body.top_k,
        lexical_weight=body.lexical_weight,
        material_ids=body.material_ids,
    )
