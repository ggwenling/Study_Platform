"""Authenticated material upload and preview endpoints."""

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from study_platform.api.v1.auth import get_current_user
from study_platform.core.config import get_settings
from study_platform.core.errors import ApiError
from study_platform.persistence.database import get_db
from study_platform.persistence.models.identity import Material
from study_platform.persistence.models.knowledge import KnowledgeBase
from study_platform.persistence.models.materials import MaterialSection, MaterialVersion
from study_platform.persistence.models.operations import AiJob, AiJobAttempt
from study_platform.services.storage import page_path, source_path

router = APIRouter()
MIMES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


class UploadResponse(BaseModel):
    material_id: UUID
    version_id: UUID
    job_id: UUID
    status: str


class MaterialResponse(BaseModel):
    id: UUID
    knowledge_base_id: UUID
    title: str
    mime_type: str | None
    byte_size: int | None
    status: str
    version_id: UUID | None
    job_id: UUID | None
    created_at: datetime
    error_code: str | None
    error_message: str | None


class MaterialPage(BaseModel):
    items: list[MaterialResponse]
    next_cursor: UUID | None


class JobResponse(BaseModel):
    id: UUID
    status: str
    phase: str | None
    progress: int
    error_code: str | None
    attempt_count: int


class JobCancelResponse(BaseModel):
    id: UUID
    status: str


class PreviewSection(BaseModel):
    text: str
    locator: dict


class MaterialPreviewResponse(BaseModel):
    version_id: UUID
    mime_type: str
    page_count: int | None
    sections: list[PreviewSection]


def missing():
    return ApiError(404, "not_found", "Resource not found")


def owned_kb(db, user, kb_id, lock=False):
    stmt = select(KnowledgeBase).where(
        KnowledgeBase.id == kb_id,
        KnowledgeBase.owner_user_id == user.id,
        KnowledgeBase.deleted_at.is_(None),
    )
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    kb = db.scalar(stmt)
    if not kb:
        raise missing()
    return kb


def owned_material(db, user, material_id):
    material = db.scalar(
        select(Material)
        .join(KnowledgeBase, Material.knowledge_base_id == KnowledgeBase.id)
        .where(
            Material.id == material_id,
            Material.owner_user_id == user.id,
            Material.deleted_at.is_(None),
            KnowledgeBase.deleted_at.is_(None),
        )
    )
    if not material:
        raise missing()
    return material


def owned_version(db, user, version_id):
    version = db.get(MaterialVersion, version_id)
    if not version:
        raise missing()
    owned_material(db, user, version.material_id)
    return version


def latest_job(db, version):
    return db.scalar(
        select(AiJob)
        .where(AiJob.job_type == "ingest", AiJob.resource_id == version.id)
        .order_by(AiJob.created_at.desc(), AiJob.id.desc())
        .limit(1)
    )


def latest_version(db, material):
    return db.scalar(
        select(MaterialVersion)
        .where(MaterialVersion.material_id == material.id)
        .order_by(MaterialVersion.is_current.desc(), MaterialVersion.version_no.desc())
        .limit(1)
    )


def dto(db, material):
    version = latest_version(db, material)
    job = latest_job(db, version) if version else None
    if job:
        state = {"succeeded": "ready", "running": (job.result or {}).get("phase", "parsing")}.get(
            job.status, job.status
        )
    else:
        state = "ready" if version and version.parse_status == "ready" else "failed"
    error = job.failure_code if job else (version.failure_code if version else "source_unavailable")
    message = {
        "no_text_extracted": "未识别到可用文字，请上传更清晰的资料或改用文本文件。",
        "storage_unavailable": "无法写入资料存储，请检查磁盘空间和目录权限后重试。",
        "database_unavailable": "数据库连接中断，请恢复数据库后重试。",
        "database_error": "数据库操作失败，请检查配置后重试。",
        "model_not_configured": "尚未配置向量模型，请完成配置后重试。",
        "model_auth_failed": "模型认证或权限不足，请检查模型配置后重试。",
        "model_quota_unavailable": "模型额度、余额或速率受限，请处理后显式重试。",
        "model_timeout": "模型连接超时，请检查连接后重试。",
        "embedding_invalid": "向量模型结果不可用，请检查模型配置后重试。",
        "lease_expired": "后台处理多次中断，请重新启动后台处理后重试。",
    }.get(error, "资料处理失败，请检查原件后重试。") if error else None
    if error and state == "queued":
        message = "上次处理暂时中断，正在等待自动重试。"
    return {
        "id": material.id,
        "knowledge_base_id": material.knowledge_base_id,
        "title": material.title,
        "mime_type": version.source_mime if version else None,
        "byte_size": version.byte_size if version else None,
        "status": state,
        "version_id": version.id if version else None,
        "job_id": job.id if job else None,
        "created_at": material.created_at,
        "error_code": error,
        "error_message": message,
    }


def create_job(db, user, version):
    job = AiJob(
        id=uuid4(),
        owner_user_id=user.id,
        job_type="ingest",
        resource_type="material_version",
        resource_id=version.id,
        status="queued",
        progress=0,
        payload={},
        attempt_count=0,
        max_attempts=3,
        fencing=0,
        next_run_at=datetime.now(timezone.utc),
    )
    db.add(job)
    return job


@router.post("/knowledge-bases/{kb_id}/materials", status_code=202, response_model=UploadResponse)
def upload(
    kb_id: UUID,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    owned_kb(db, user, kb_id, lock=True)
    title = Path((file.filename or "").replace("\\", "/")).name[:512]
    mime = MIMES.get(Path(title).suffix.lower())
    if not title or not mime:
        raise ApiError(400, "unsupported_file", "Unsupported file type")
    version_id = uuid4()
    path = source_path(version_id)
    digest = hashlib.sha256()
    size = 0
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as out:
            while block := file.file.read(1024 * 1024):
                size += len(block)
                if size > get_settings().max_upload_bytes:
                    raise ApiError(413, "file_too_large", "File is too large")
                digest.update(block)
                out.write(block)
        if not size:
            raise ApiError(400, "empty_file", "File is empty")
        material = Material(
            id=uuid4(),
            owner_user_id=user.id,
            knowledge_base_id=kb_id,
            course_id=None,
            title=title,
            kind="other",
            status="active",
        )
        version = MaterialVersion(
            id=version_id,
            material_id=material.id,
            version_no=1,
            is_current=True,
            source_mime=mime,
            source_sha256=digest.hexdigest(),
            byte_size=size,
            object_key=f"{version_id}/source",
            parse_status="pending",
        )
        db.add(material)
        db.flush()
        db.add(version)
        db.flush()
        job = create_job(db, user, version)
        db.commit()
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return {
        "material_id": material.id,
        "version_id": version.id,
        "job_id": job.id,
        "status": "queued",
    }


@router.get("/knowledge-bases/{kb_id}/materials", response_model=MaterialPage)
def list_materials(
    kb_id: UUID,
    limit: int = Query(20, ge=1, le=100),
    cursor: UUID | None = None,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    owned_kb(db, user, kb_id)
    stmt = select(Material).where(
        Material.knowledge_base_id == kb_id,
        Material.owner_user_id == user.id,
        Material.deleted_at.is_(None),
    )
    if cursor:
        anchor = owned_material(db, user, cursor)
        if anchor.knowledge_base_id != kb_id:
            raise missing()
        from sqlalchemy import tuple_

        stmt = stmt.where(tuple_(Material.created_at, Material.id) < (anchor.created_at, anchor.id))
    rows = db.scalars(
        stmt.order_by(Material.created_at.desc(), Material.id.desc()).limit(limit + 1)
    ).all()
    return {
        "items": [dto(db, x) for x in rows[:limit]],
        "next_cursor": rows[limit - 1].id if len(rows) > limit else None,
    }


@router.get("/materials/{material_id}", response_model=MaterialResponse)
def get_material(material_id: UUID, db: Session = Depends(get_db), user=Depends(get_current_user)):
    return dto(db, owned_material(db, user, material_id))


@router.delete("/materials/{material_id}", status_code=204)
def delete_material(
    material_id: UUID, db: Session = Depends(get_db), user=Depends(get_current_user)
):
    material = owned_material(db, user, material_id)
    owned_kb(db, user, material.knowledge_base_id, lock=True)
    material = db.scalar(
        select(Material)
        .where(Material.id == material_id, Material.deleted_at.is_(None))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not material:
        raise missing()
    material.deleted_at = datetime.now(timezone.utc)
    material.status = "deleted"
    versions = db.scalars(
        select(MaterialVersion.id).where(MaterialVersion.material_id == material.id)
    ).all()
    jobs = db.scalars(
        select(AiJob)
        .where(
            AiJob.resource_id.in_(versions),
            AiJob.job_type.in_(("ingest", "classification")),
            AiJob.status.in_(["queued", "running"]),
        )
        .with_for_update()
    ).all()
    for job in jobs:
        job.cancel_requested_at = material.deleted_at
        job.status = "cancelled"
        job.fencing += 1
        attempt = db.scalar(
            select(AiJobAttempt).where(
                AiJobAttempt.job_id == job.id, AiJobAttempt.finished_at.is_(None)
            )
        )
        if attempt:
            attempt.outcome = "cancelled"
            attempt.finished_at = material.deleted_at
    db.commit()


@router.post("/materials/{material_id}/retry", status_code=202, response_model=UploadResponse)
def retry(material_id: UUID, db: Session = Depends(get_db), user=Depends(get_current_user)):
    material = owned_material(db, user, material_id)
    owned_kb(db, user, material.knowledge_base_id, lock=True)
    material = db.scalar(
        select(Material)
        .where(Material.id == material_id, Material.deleted_at.is_(None))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not material:
        raise missing()
    version = latest_version(db, material)
    if not version or not source_path(version.id).is_file():
        raise ApiError(409, "source_unavailable", "资料原件不可用，请重新上传")
    job = latest_job(db, version)
    if job and job.status not in ("failed", "cancelled"):
        raise ApiError(409, "job_active", "当前处理任务不可重试")
    job = create_job(db, user, version)
    db.commit()
    return {
        "material_id": material.id,
        "version_id": version.id,
        "job_id": job.id,
        "status": "queued",
    }


def owned_job(db, user, job_id):
    job = db.scalar(
        select(AiJob).where(
            AiJob.id == job_id, AiJob.owner_user_id == user.id, AiJob.job_type == "ingest"
        )
    )
    if not job:
        raise missing()
    owned_version(db, user, job.resource_id)
    return job


@router.get("/jobs/{job_id}", response_model=JobResponse)
def get_job(job_id: UUID, db: Session = Depends(get_db), user=Depends(get_current_user)):
    job = owned_job(db, user, job_id)
    return {
        "id": job.id,
        "status": job.status,
        "phase": (job.result or {}).get("phase"),
        "progress": job.progress,
        "error_code": job.failure_code,
        "attempt_count": job.attempt_count,
    }


@router.post("/jobs/{job_id}/cancel", response_model=JobCancelResponse)
def cancel_job(job_id: UUID, db: Session = Depends(get_db), user=Depends(get_current_user)):
    owned_job(db, user, job_id)
    job = db.scalar(
        select(AiJob)
        .where(AiJob.id == job_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if job.status in ("queued", "running"):
        job.cancel_requested_at = datetime.now(timezone.utc)
        job.finished_at = job.cancel_requested_at
        job.status = "cancelled"
        job.fencing += 1
        attempt = db.scalar(
            select(AiJobAttempt).where(
                AiJobAttempt.job_id == job.id, AiJobAttempt.finished_at.is_(None)
            )
        )
        if attempt:
            attempt.outcome = "cancelled"
            attempt.finished_at = job.cancel_requested_at
        db.commit()
    return {"id": job.id, "status": job.status}


@router.get("/material-versions/{version_id}/preview", response_model=MaterialPreviewResponse)
def preview(version_id: UUID, db: Session = Depends(get_db), user=Depends(get_current_user)):
    version = owned_version(db, user, version_id)
    sections = db.scalars(
        select(MaterialSection)
        .where(MaterialSection.material_version_id == version.id)
        .order_by(MaterialSection.ordinal)
    ).all()
    return {
        "version_id": version.id,
        "mime_type": version.source_mime,
        "page_count": version.page_count,
        "sections": [{"text": s.text, "locator": s.locator} for s in sections],
    }


@router.get("/material-versions/{version_id}/content")
def content(version_id: UUID, db: Session = Depends(get_db), user=Depends(get_current_user)):
    version = owned_version(db, user, version_id)
    path = source_path(version.id)
    if not path.is_file():
        raise missing()
    mime = version.source_mime if version.source_mime in MIMES.values() else "application/octet-stream"
    return FileResponse(path, media_type=mime, headers={
        "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store",
    })


@router.get("/material-versions/{version_id}/pages/{page_no}")
def page(
    version_id: UUID, page_no: int, db: Session = Depends(get_db), user=Depends(get_current_user)
):
    owned_version(db, user, version_id)
    if page_no < 1:
        raise missing()
    path = page_path(version_id, page_no)
    if not path.is_file():
        raise missing()
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, no-store"})
