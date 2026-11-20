"""Queue and publish long-running generation jobs."""

from datetime import timedelta
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError

from study_platform.core.errors import ApiError
from study_platform.persistence.database import connection_failure, session_factory
from study_platform.persistence.models.identity import Material
from study_platform.persistence.models.knowledge import KnowledgeBase
from study_platform.persistence.models.materials import MaterialVersion
from study_platform.persistence.models.operations import AiJob
from study_platform.services import job_queue, model_connections

GENERATION_TYPES = ("notes", "flashcards", "quiz")
RESULT_TYPES = {"notes": "note", "flashcards": "flashcard_deck", "quiz": "quiz"}
TITLES = {"notes": "工坊产物", "flashcards": "记忆闪卡", "quiz": "练习题",
          "reindex": "索引重建", "ingest": "资料入库", "classification": "资料分类"}


def _library_id():
    legacy_library = (sa.select(Material.knowledge_base_id)
        .join(MaterialVersion, MaterialVersion.material_id == Material.id)
        .where(MaterialVersion.id == AiJob.resource_id,
               Material.owner_user_id == AiJob.owner_user_id).correlate(AiJob).scalar_subquery())
    return sa.case((AiJob.resource_type == "knowledge_base", AiJob.resource_id), else_=legacy_library)


def _owned_library(db, owner, kb_id, *, lock=False):
    query = sa.select(KnowledgeBase).where(KnowledgeBase.id == kb_id,
        KnowledgeBase.owner_user_id == owner, KnowledgeBase.deleted_at.is_(None))
    kb = db.scalar(query.with_for_update().execution_options(populate_existing=True) if lock else query)
    if kb is None:
        raise ApiError(404, "not_found", "知识库不存在。")
    return kb


def _query():
    kb = _library_id()
    return sa.select(AiJob, kb).where(AiJob.job_type.in_(TITLES))


def _dto(job, kb):
    result = job.result or {}
    return {
        "id": job.id, "job_type": job.job_type, "knowledge_base_id": kb,
        "title": job.payload.get("title") or TITLES[job.job_type], "status": job.status,
        "phase": job.status if job.status in ("cancelled", "failed") else result.get("phase", job.status),
        "progress": job.progress, "result_resource_type": result.get("resource_type"),
        "result_resource_id": result.get("resource_id"), "error_code": job.failure_code,
        "error_message": result.get("error_message") or (
            "任务处理失败，请查看错误代码后重新提交。" if job.status == "failed" else None),
        "created_at": job.created_at, "updated_at": job.updated_at,
    }


def enqueue(db, owner, kb_id, kind, body):
    kb = _owned_library(db, owner, kb_id, lock=True)
    active = db.scalar(sa.select(sa.func.count()).select_from(AiJob).where(
        AiJob.owner_user_id == owner, AiJob.resource_id == kb_id,
        AiJob.job_type == kind, AiJob.status.in_(job_queue.ACTIVE),
    ))
    if active >= 3:
        raise ApiError(409, "too_many_active_jobs", "本库已有 3 个同类任务，请等待完成或取消后重试。")
    materials = body.get("material_ids")
    if materials:
        available = set(db.scalars(sa.select(Material.id).where(
            Material.id.in_([UUID(item) for item in materials]), Material.owner_user_id == owner,
            Material.knowledge_base_id == kb_id, Material.deleted_at.is_(None), Material.status == "active",
        )))
        if available != {UUID(item) for item in materials}:
            raise ApiError(404, "not_found", "所选资料不存在。")
    label = {
        "study_guide": "学习指南", "mindmap": "思维导图", "formula_sheet": "公式速查",
        "short_answer": "简答练习", "mcq": "单选练习", "mixed": "混合练习",
    }.get(body.get("kind") or body.get("mode"), TITLES[kind])
    title = (body.get("title") or "").strip() or f"{kb.name} · {label}"
    row = AiJob(id=uuid4(), owner_user_id=owner, job_type=kind, resource_type="knowledge_base",
                resource_id=kb_id, status="queued", attempt_count=0, max_attempts=3,
                next_run_at=job_queue.now(), payload={"body": body, "title": title,
                    "llm_connection": model_connections.default_snapshot(db, owner, "llm")})
    db.add(row)
    db.commit()
    return {"job_id": row.id, "job_type": kind, "status": "queued", "knowledge_base_id": kb_id, "title": title}


def list_jobs(db, owner, kb_id, job_types, active, limit):
    query = _query().where(AiJob.owner_user_id == owner)
    if kb_id is not None:
        _owned_library(db, owner, kb_id)
        query = query.where(_library_id() == kb_id)
    if job_types:
        query = query.where(AiJob.job_type.in_(job_types))
    if active:
        query = query.where(AiJob.status.in_(job_queue.ACTIVE))
    rows = db.execute(query.order_by(AiJob.created_at.desc(), AiJob.id.desc()).limit(limit))
    return [_dto(job, kb) for job, kb in rows]


def get(db, owner, job_id):
    row = db.execute(_query().where(AiJob.id == job_id, AiJob.owner_user_id == owner)).one_or_none()
    if row is None:
        raise ApiError(404, "not_found", "任务不存在。")
    return _dto(*row)


def cancel(db, user, job_id):
    result = get(db, user.id, job_id)
    kind = result["job_type"]
    if kind == "ingest":
        from study_platform.api.v1.materials import cancel_job

        cancel_job(job_id, db=db, user=user)
        return get(db, user.id, job_id)
    if result["knowledge_base_id"] is not None:
        _owned_library(db, user.id, result["knowledge_base_id"], lock=True)
    job = db.scalar(sa.select(AiJob).where(AiJob.id == job_id, AiJob.owner_user_id == user.id)
                    .with_for_update().execution_options(populate_existing=True))
    if kind == "reindex" and job.status in (*job_queue.ACTIVE, "failed"):
        from study_platform.services.reindex import cancel_job

        cancel_job(db, job)
    else:
        job_queue.cancel(db, job)
    db.commit()
    return _dto(job, result["knowledge_base_id"])


def cancel_for_deleted_library(db, kb):
    for job in db.scalars(sa.select(AiJob).where(AiJob.owner_user_id == kb.owner_user_id,
            AiJob.resource_id == kb.id, AiJob.job_type.in_(GENERATION_TYPES),
            AiJob.status.in_(job_queue.ACTIVE)).order_by(AiJob.id).with_for_update()):
        job_queue.cancel(db, job)


def run(job_id, kb_id, fence):
    from study_platform.services import learning, studio
    from study_platform.services.chat import visible_indices

    with session_factory()() as db:
        def lock_job():
            return job_queue.require_lease(db.get(AiJob, job_id, with_for_update=True,
                                                   populate_existing=True), fence)

        def before_publish():
            _owned_library(db, owner, kb_id, lock=True)
            return lock_job()

        try:
            job = db.get(AiJob, job_id)
            owner = job.owner_user_id
            kb = _owned_library(db, owner, kb_id, lock=True)
            job = lock_job()
            kind = job.job_type
            body = dict(job.payload["body"])
            if body.get("material_ids") is not None:
                body["material_ids"] = [UUID(value) for value in body["material_ids"]]
            llm = job.payload["llm_connection"]
            embedding = model_connections.library_snapshot(db, owner, kb_id) if kind == "quiz" else None
            if kind == "quiz":
                body["index_ids"] = visible_indices(db, owner, kb.id, body.get("material_ids"))
            job.progress = 50
            db.commit()
            generate = {"notes": studio.generate_studio_artifact,
                        "flashcards": studio.generate_flashcard_deck,
                        "quiz": learning.generate_learning_quiz}[kind]
            with model_connections.task(owner, llm=llm, embedding=embedding):
                generated = generate(db, owner_user_id=owner, knowledge_base_id=kb_id,
                                     before_publish=before_publish, **body)
                resource_id = generated.id if kind == "quiz" else generated["id"]
                job = before_publish()
                job.result = {"phase": "ready", "resource_type": RESULT_TYPES[kind], "resource_id": str(resource_id)}
                job.status, job.progress, job.finished_at = "succeeded", 100, job_queue.now()
                job_queue.finish_attempt(db, job, "success")
                db.commit()
        except job_queue.LostLease:
            db.rollback()
        except Exception as error:
            db.rollback()
            if isinstance(error, DBAPIError) and connection_failure(error):
                raise
            try:
                job = lock_job()
                code = error.code if isinstance(error, ApiError) else "generation_failed"
                retry = isinstance(error, ApiError) and error.retryable and job.attempt_count < job.max_attempts
                job.status = "queued" if retry else "failed"
                job.next_run_at = job_queue.now() + timedelta(seconds=5)
                job.finished_at = None if retry else job_queue.now()
                job.failure_code = code
                # Only service-authored errors suitable for display may enter the task result.
                message = error.message if isinstance(error, ApiError) else "生成失败，请重试。"
                job.result = {"phase": job.status, "error_message": message}
                job_queue.finish_attempt(db, job, "retry" if retry else "failed", code)
                db.commit()
            except job_queue.LostLease:
                db.rollback()
