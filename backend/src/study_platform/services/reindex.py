"""Build a second generation for a library and publish it in one transaction."""

import hashlib
from datetime import timedelta
from itertools import groupby
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from study_platform.core.errors import ApiError
from study_platform.persistence.database import connection_failure, session_factory
from study_platform.persistence.models.identity import Material
from study_platform.persistence.models.knowledge import KnowledgeBase
from study_platform.persistence.models.materials import (
    ChunkEmbedding,
    IndexVersion,
    MaterialChunk,
    MaterialSection,
    MaterialVersion,
)
from study_platform.persistence.models.operations import AiJob
from study_platform.services import job_queue, model_connections
from study_platform.services.chunking import semantic_chunks
from study_platform.services.lexical import lexical_column_values
from study_platform.services.model_usage import ModelUsageContext
from study_platform.services.models import embed_texts


def _jobs(kb):
    return select(AiJob).where(
        AiJob.owner_user_id == kb.owner_user_id,
        AiJob.resource_id == kb.id,
        AiJob.job_type == "reindex",
    ).order_by(AiJob.created_at.desc(), AiJob.id.desc()).limit(1)


def _materials(kb):
    return select(Material).where(
        Material.owner_user_id == kb.owner_user_id,
        Material.knowledge_base_id == kb.id,
        Material.deleted_at.is_(None),
        Material.status == "active",
    ).order_by(Material.id)


def _ready(db, kb):
    return {
        str(version): {"source_index_id": str(index), "title": title}
        for version, index, title in db.execute(
            _materials(kb).with_only_columns(MaterialVersion.id, IndexVersion.id, Material.title)
            .select_from(Material)
            .join(MaterialVersion, MaterialVersion.material_id == Material.id)
            .join(IndexVersion, IndexVersion.material_version_id == MaterialVersion.id)
            .where(MaterialVersion.is_current.is_(True), MaterialVersion.parse_status == "ready",
                   IndexVersion.is_active.is_(True), IndexVersion.status == "ready")
        )
    }


def _target(db, kb, connection_id):
    if connection_id is None:
        return model_connections.library_snapshot(db, kb.owner_user_id, kb.id)
    row = model_connections.owned(db, kb.owner_user_id, connection_id, lock=True)
    if row.kind != "embedding" or row.status != "ready":
        raise ApiError(409, "model_test_required", "请选择已通过 1024 维验证的向量模型连接。")
    version = row.versions[str(row.revision)]
    return {**model_connections.snapshot(row), "binding": {
        "base_url": version["base_url"], "model": version["model"], "dimensions": 1024,
        "profile": f"connection:{row.id}:1024",
    }}


def enqueue(db, kb, connection_id, rechunk):
    current = db.scalar(_jobs(kb).with_for_update())
    if current and current.status in job_queue.ACTIVE:
        raise ApiError(409, "reindex_in_progress", "此知识库已有正在进行的索引重建。")
    if current and current.status == "failed":
        cancel_job(db, current)
    target = _target(db, kb, connection_id)
    items = _ready(db, kb)
    job = AiJob(
        id=uuid4(), owner_user_id=kb.owner_user_id, job_type="reindex",
        resource_type="knowledge_base", resource_id=kb.id, status="queued",
        attempt_count=0, max_attempts=3, next_run_at=job_queue.now(),
        payload={"embedding_connection": target, "material_version_ids": list(items), "rechunk": rechunk},
        result={"phase": "queued", "materials": items},
    )
    db.add(job)
    db.commit()
    return {"job_id": job.id, "status": "queued", "total_materials": len(items)}


def status(job):
    if job is None:
        return None
    result = job.result or {}
    items = result.get("materials", {})
    failures = [
        {"material_version_id": key, "title": item["title"], "error_code": item["failure_code"]}
        for key, item in items.items() if item.get("failure_code")
    ]
    target = job.payload["embedding_connection"]
    return {
        "job_id": job.id, "status": job.status, "total": len(items),
        "succeeded": sum(bool(item.get("index_id")) for item in items.values()),
        "failed": len(failures), "running": int(job.status == "running" and bool(result.get("building"))),
        "target_model": target["binding"]["model"], "target_connection_id": target.get("id"),
        "failures": failures, "error_code": job.failure_code,
    }


def latest(db, kb):
    return status(db.scalar(_jobs(kb)))


def retry(db, kb):
    job = db.scalar(_jobs(kb).with_for_update())
    if job is None or job.status != "failed":
        raise ApiError(409, "reindex_not_failed", "当前没有可重试的索引重建。")
    _verify_target(db, job)
    result = job.result
    items = {
        key: {name: value for name, value in item.items() if name != "failure_code"}
        for key, item in result["materials"].items()
    }
    job.result = {**result, "materials": items, "phase": "queued", "building": None}
    job.status = "queued"
    job.next_run_at = job_queue.now()
    job.finished_at = job.failure_code = None
    job.max_attempts = min(job.attempt_count + 3, 32767)
    db.commit()
    return status(job)


def _discard(db, items):
    ids = [UUID(item["index_id"]) for item in items if item.get("index_id")]
    if not ids:
        return
    builds = select(IndexVersion.id).where(IndexVersion.id.in_(ids), IndexVersion.is_active.is_(False),
                                          IndexVersion.status == "building")
    chunks = select(MaterialChunk.id).where(MaterialChunk.index_version_id.in_(builds))
    db.execute(sa.delete(ChunkEmbedding).where(ChunkEmbedding.chunk_id.in_(chunks)))
    db.execute(sa.delete(MaterialChunk).where(MaterialChunk.index_version_id.in_(builds)))
    db.execute(sa.delete(IndexVersion).where(IndexVersion.id.in_(builds)))


def cancel_job(db, job):
    if job.status == "failed":
        job.status = "cancelled"
        job.cancel_requested_at = job.finished_at = job_queue.now()
        job.fencing += 1
    else:
        job_queue.cancel(db, job)
    _discard(db, job.result.get("materials", {}).values())
    items = {key: {"title": item["title"]} for key, item in job.result["materials"].items()}
    job.result = {"phase": "cancelled", "materials": items}
    job.failure_code = None


def cancel_for_deleted_library(db, kb):
    job = db.scalar(_jobs(kb).with_for_update())
    if job and job.status in (*job_queue.ACTIVE, "failed"):
        cancel_job(db, job)


def cancel(db, kb):
    job = db.scalar(_jobs(kb).with_for_update())
    if job is None or job.status not in (*job_queue.ACTIVE, "failed"):
        raise ApiError(409, "reindex_not_active", "当前没有可取消的索引重建。")
    cancel_job(db, job)
    db.commit()
    return status(job)


def _locked(db, job_id, kb_id, fence):
    kb = db.get(KnowledgeBase, kb_id, with_for_update=True, populate_existing=True)
    job = db.get(AiJob, job_id, with_for_update=True, populate_existing=True)
    job_queue.require_lease(job, fence)
    if not kb or kb.deleted_at or kb.owner_user_id != job.owner_user_id:
        raise job_queue.LostLease()
    return kb, job


def _chunks(db, source_index_id):
    return db.execute(
        select(MaterialChunk.text, MaterialChunk.locator, MaterialChunk.section_id,
               MaterialChunk.group_id, MaterialChunk.retrieval_enabled, MaterialSection.text)
        .outerjoin(MaterialSection, MaterialSection.id == MaterialChunk.section_id)
        .where(MaterialChunk.index_version_id == UUID(source_index_id))
        .order_by(MaterialChunk.ordinal)
    ).all()


def _signature(rows):
    # Edits and retrieval toggles can change an active generation in place.
    digest = hashlib.sha256()
    for text, _locator, _section, _group, enabled, _section_text in rows:
        digest.update(hashlib.sha256(text.encode()).digest() + bytes([enabled]))
    return digest.hexdigest()


def _parts(rows, rechunk):
    for section_id, group in groupby(rows, key=lambda row: row[2]):
        chunks = list(group)
        text, locator, _, group_id, _, section_text = chunks[0]
        if rechunk and section_id and section_text and all(
            enabled and not row_group and part in section_text
            for part, _, _, row_group, enabled, _ in chunks
        ):
            yield from ((part, locator, section_id, group_id, True) for part in semantic_chunks(section_text))
        else:
            yield from (row[:5] for row in chunks)


def _embed(parts, owner):
    vectors = []
    for offset in range(0, len(parts), 20):
        texts = [part[0] for part in parts[offset:offset + 20]]
        batch = embed_texts(texts, context=ModelUsageContext(owner, "embedding"))
        if len(batch) != len(texts) or any(len(vector) != 1024 for vector in batch):
            raise ApiError(502, "embedding_invalid", "向量模型返回维度或内容不正确。")
        vectors.extend(batch)
    return vectors


def _store(db, version_id, profile, pipeline, parts, vectors):
    generation = db.scalar(select(sa.func.max(IndexVersion.generation)).where(
        IndexVersion.material_version_id == version_id,
    ))
    index = IndexVersion(id=uuid4(), material_version_id=version_id, generation=(generation or 0) + 1,
                         embedding_profile=profile, pipeline_profile=pipeline, chunk_count=len(parts),
                         status="building", is_active=False)
    db.add(index)
    db.flush()
    chunks = []
    for ordinal, ((text, locator, section_id, group_id, enabled), vector) in enumerate(zip(parts, vectors, strict=True)):
        chunk = MaterialChunk(id=uuid4(), index_version_id=index.id, material_version_id=version_id,
                              text=text, text_sha256=hashlib.sha256(text.encode()).hexdigest(),
                              ordinal=ordinal, locator=locator, section_id=section_id, group_id=group_id,
                              retrieval_enabled=enabled, **lexical_column_values(text))
        chunks.append((chunk, vector))
        db.add(chunk)
    db.flush()
    db.add_all(ChunkEmbedding(chunk_id=chunk.id, model_profile_id=profile, embedding=vector)
               for chunk, vector in chunks)
    return str(index.id)


def _set_result(job, items, phase, building=None):
    job.result = {"materials": items, "phase": phase, "building": building}
    done = sum(bool(item.get("index_id") or item.get("failure_code")) for item in items.values())
    job.progress = min(99, 100 * done // max(len(items), 1))


def _verify_target(db, job):
    saved = job.payload["embedding_connection"]
    if saved.get("id"):
        row = model_connections.owned(db, job.owner_user_id, UUID(saved["id"]), lock=True)
        if row.kind != "embedding" or row.revision != saved["revision"] or row.status != "ready":
            raise ApiError(409, "model_connection_changed", "目标向量连接已变更，请重新发起重建。")
    return saved


def _finish(db, kb, job, items):
    if any(item.get("failure_code") for item in items.values()):
        _set_result(job, items, "failed")
        job.status, job.failure_code, job.finished_at = "failed", "reindex_materials_failed", job_queue.now()
        job_queue.finish_attempt(db, job, "failed", job.failure_code)
        return
    pending_ingest = db.scalar(select(AiJob.id).join(MaterialVersion, MaterialVersion.id == AiJob.resource_id)
        .where(AiJob.owner_user_id == kb.owner_user_id, AiJob.job_type == "ingest", AiJob.status.in_(job_queue.ACTIVE),
               MaterialVersion.material_id.in_(_materials(kb).with_only_columns(Material.id))).limit(1))
    if pending_ingest:
        # Yield the heavy worker so queued uploads can finish with the old binding first.
        _set_result(job, items, "waiting_for_ingestion")
        job.status, job.next_run_at = "queued", job_queue.now() + timedelta(seconds=2)
        job.max_attempts = min(job.max_attempts + 1, 32767)
        job_queue.finish_attempt(db, job, "retry")
        return
    saved = _verify_target(db, job)
    at = job_queue.now()
    if items:
        db.execute(sa.update(IndexVersion).where(IndexVersion.id.in_(
            [UUID(item["source_index_id"]) for item in items.values()]
        )).values(is_active=False, status="retired"))
        db.execute(sa.update(IndexVersion).where(IndexVersion.id.in_(
            [UUID(item["index_id"]) for item in items.values()]
        )).values(is_active=True, status="ready", ready_at=at))
    kb.embedding_connection_id = UUID(saved["id"]) if saved.get("id") else None
    kb.embedding_binding = saved["binding"]
    _set_result(job, items, "ready")
    job.status, job.progress, job.finished_at = "succeeded", 100, at
    job_queue.finish_attempt(db, job, "success")


def _refresh_plan(db, kb, items):
    ready = _ready(db, kb)
    for key, item in items.items():
        source = ready.get(key)
        if source and source["source_index_id"] == item["source_index_id"] and (
            not item.get("index_id") or item["signature"] == _signature(_chunks(db, item["source_index_id"]))
        ):
            ready[key] = {**item, "title": source["title"]}
        else:
            _discard(db, [item])
    return ready


def run(job_id, kb_id, fence):
    with session_factory()() as db:
        try:
            kb, job = _locked(db, job_id, kb_id, fence)
            owner = job.owner_user_id
            target = _verify_target(db, job)
            rechunk = job.payload["rechunk"]
            db.commit()
            with model_connections.task(owner, llm={"id": None, "kind": "llm"}, embedding=target):
                while True:
                    kb, job = _locked(db, job_id, kb_id, fence)
                    items = job.result["materials"]
                    key = next((k for k, item in items.items() if not item.get("index_id") and not item.get("failure_code")), None)
                    if key is None:
                        # All structural writers serialize on the library row. Lock materials
                        # before touching generations, and recheck uploads, deletions and edits.
                        db.scalars(_materials(kb).with_for_update()).all()
                        ready = _refresh_plan(db, kb, items)
                        if any(not item.get("index_id") and not item.get("failure_code") for item in ready.values()):
                            _set_result(job, ready, "reindexing")
                            db.commit()
                            continue
                        _finish(db, kb, job, ready)
                        db.commit()
                        return
                    item = items[key]
                    source = db.get(IndexVersion, UUID(item["source_index_id"]))
                    rows = _chunks(db, item["source_index_id"])
                    pipeline = "semantic-v2" if rechunk else source.pipeline_profile
                    _set_result(job, items, "reindexing", key)
                    db.commit()
                    parts = list(_parts(rows, rechunk))
                    try:
                        if not parts:
                            raise ApiError(422, "no_text_extracted", "未提取到可用文字。")
                        vectors = _embed(parts, owner)
                    except ApiError as error:
                        item = {**item, "failure_code": error.code}
                        vectors = None
                    kb, job = _locked(db, job_id, kb_id, fence)
                    if vectors is not None:
                        index_id = _store(db, UUID(key), target["binding"]["profile"], pipeline, parts, vectors)
                        item = {**item, "index_id": index_id, "signature": _signature(rows)}
                    _set_result(job, {**items, key: item}, "reindexing")
                    db.commit()
        except job_queue.LostLease:
            db.rollback()
        except Exception as error:
            db.rollback()
            if isinstance(error, DBAPIError) and connection_failure(error):
                raise
            try:
                _, job = _locked(db, job_id, kb_id, fence)
                job.status, job.finished_at = "failed", job_queue.now()
                job.failure_code = error.code if isinstance(error, ApiError) else "reindex_failed"
                _set_result(job, job.result["materials"], "failed")
                job_queue.finish_attempt(db, job, "failed", job.failure_code)
                db.commit()
            except job_queue.LostLease:
                db.rollback()
