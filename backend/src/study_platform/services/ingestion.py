"""Durable ingestion worker and document extraction."""

import hashlib
import shutil
import socket
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from study_platform.core.config import get_settings
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
from study_platform.persistence.models.operations import AiJob, AiJobAttempt
from study_platform.services.storage import source_path, version_dir

_ocr = None


def now():
    return datetime.now(timezone.utc)


def ocr_image(image):
    global _ocr
    if _ocr is None:
        from rapidocr import RapidOCR

        _ocr = RapidOCR()
    import numpy as np

    result = _ocr(np.asarray(image.convert("RGB")))
    texts = getattr(result, "txts", None)
    if texts is None:
        texts = []
    boxes = getattr(result, "boxes", None)
    scores = getattr(result, "scores", None)
    return (
        [
            {
                "text": str(t),
                "box": boxes[i].tolist() if hasattr(boxes[i], "tolist") else boxes[i],
                "confidence": float(scores[i]),
            }
            for i, t in enumerate(texts)
        ]
        if boxes is not None and scores is not None
        else []
    )


def extract_docx(path, *, legacy_columns=False):
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    def blocks(container, table_path=()):
        paragraph_number = table_number = 0
        for block in container.iter_inner_content():
            if isinstance(block, Paragraph):
                paragraph_number += 1
                text = block.text.strip()
                if text:
                    locator = (
                        {"table_path": list(table_path), "cell_paragraph": paragraph_number}
                        if table_path
                        else {"paragraph": paragraph_number}
                    )
                    yield text, locator
            elif isinstance(block, Table):
                table_number += 1
                seen_cells = set()
                for row_number, row in enumerate(block.rows, 1):
                    first_column = 1 if legacy_columns else row.grid_cols_before + 1
                    for column_number, cell in enumerate(row.cells, first_column):
                        if cell._tc in seen_cells:
                            continue
                        seen_cells.add(cell._tc)
                        location = {"table": table_number, "row": row_number, "column": column_number}
                        yield from blocks(cell, (*table_path, location))

    yield from blocks(Document(path))


def extract(version, output_dir, phase):
    """Yield text with original page/paragraph/line locators."""
    settings = get_settings()
    path = source_path(version.id)
    mime = version.source_mime
    if mime == "text/plain":
        with path.open(encoding="utf-8-sig") as f:
            for number, line in enumerate(f, 1):
                if line.strip():
                    yield line.strip(), {"line": number}
    elif mime.endswith("wordprocessingml.document"):
        yield from extract_docx(path)
    elif mime == "application/pdf":
        import pypdfium2 as pdfium

        doc = pdfium.PdfDocument(path)
        try:
            if len(doc) > settings.max_pdf_pages or len(doc) == 0:
                raise ValueError("Invalid PDF page count")
            version.page_count = len(doc)
            for number in range(1, len(doc) + 1):
                phase("parsing")
                page = doc[number - 1]
                try:
                    textpage = page.get_textpage()
                    try:
                        text = textpage.get_text_range().strip()
                    finally:
                        textpage.close()
                    if text:
                        yield text, {"page": number}
                    else:
                        phase("ocr")
                        width, height = page.get_size()
                        scale = min(200 / 72, 2500 / max(width, height))
                        bitmap = page.render(scale=scale)
                        try:
                            image = bitmap.to_pil()
                            image.save(output_dir / f"page-{number}.png")
                            lines = ocr_image(image)
                            image.close()
                        finally:
                            bitmap.close()
                        if lines:
                            yield (
                                "\n".join(line["text"] for line in lines),
                                {"page": number, "ocr": lines},
                            )
                finally:
                    page.close()
        finally:
            doc.close()
    elif mime in ("image/png", "image/jpeg"):
        from PIL import Image

        with Image.open(path) as image:
            expected = "PNG" if mime == "image/png" else "JPEG"
            if image.format != expected or image.width * image.height > settings.max_image_pixels:
                raise ValueError("Invalid image")
            image.load()
            phase("ocr")
            image.convert("RGB").save(output_dir / "page-1.png")
            lines = ocr_image(image)
            if lines:
                yield "\n".join(line["text"] for line in lines), {"image": 1, "ocr": lines}
    else:
        raise ValueError("Unsupported file")


def chunks(text, size=800, overlap=120):
    start = 0
    while start < len(text):
        yield text[start : start + size]
        if start + size >= len(text):
            break
        start += size - overlap


class LostLease(Exception):
    pass


def claim(db):
    current = now()
    job = db.scalar(
        select(AiJob)
        .where(
            AiJob.job_type == "ingest",
            AiJob.cancel_requested_at.is_(None),
            sa.or_(
                sa.and_(AiJob.status == "queued", AiJob.next_run_at <= current),
                sa.and_(AiJob.status == "running", AiJob.lease_expires_at < current),
            ),
        )
        .order_by(AiJob.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if not job:
        return None
    if job.attempt_count >= job.max_attempts:
        previous = db.scalar(
            select(AiJobAttempt).where(
                AiJobAttempt.job_id == job.id, AiJobAttempt.attempt_no == job.attempt_count
            )
        )
        if previous and previous.finished_at is None:
            previous.finished_at = current
            previous.outcome = "failed"
            previous.failure_code = "lease_expired"
        job.status = "failed"
        job.failure_code = "lease_expired"
        job.finished_at = current
        db.commit()
        return None
    if job.status == "running":
        previous = db.scalar(
            select(AiJobAttempt).where(
                AiJobAttempt.job_id == job.id, AiJobAttempt.attempt_no == job.attempt_count
            )
        )
        if previous and previous.finished_at is None:
            previous.finished_at = current
            previous.outcome = "retry"
            previous.failure_code = "lease_expired"
    job.status = "running"
    job.failure_code = None
    job.attempt_count += 1
    job.fencing += 1
    job.lease_expires_at = current + timedelta(seconds=get_settings().job_lease_seconds)
    job.lease_token_hash = hashlib.sha256(uuid4().bytes).hexdigest()
    job.result = {"phase": "parsing"}
    db.add(
        AiJobAttempt(
            job_id=job.id,
            attempt_no=job.attempt_count,
            worker_id=socket.gethostname()[:128],
            fencing=job.fencing,
            started_at=current,
        )
    )
    result = (job.id, job.resource_id, job.fencing, job.attempt_count)
    db.commit()
    return result


def locked_job(db, job_id, fence):
    # Resolve identifiers first, then serialize with deletion in a consistent order.
    ids = db.execute(
        select(Material.knowledge_base_id, Material.id)
        .join(MaterialVersion, MaterialVersion.material_id == Material.id)
        .join(AiJob, AiJob.resource_id == MaterialVersion.id)
        .where(AiJob.id == job_id)
    ).first()
    if not ids:
        raise LostLease()
    kb = db.scalar(
        select(KnowledgeBase)
        .where(KnowledgeBase.id == ids[0])
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    material = db.scalar(
        select(Material)
        .where(Material.id == ids[1])
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    job = db.scalar(
        select(AiJob)
        .where(AiJob.id == job_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        not kb
        or kb.deleted_at
        or not material
        or material.deleted_at
        or not job
        or job.status != "running"
        or job.fencing != fence
        or job.cancel_requested_at
        or not job.lease_expires_at
        or job.lease_expires_at <= now()
    ):
        raise LostLease()
    return job


def update_phase(db, job_id, fence, phase):
    job = locked_job(db, job_id, fence)
    job.result = {"phase": phase}
    job.progress = {"parsing": 10, "ocr": 30, "indexing": 60}[phase]
    job.lease_expires_at = now() + timedelta(seconds=get_settings().job_lease_seconds)
    db.commit()


def run_once():
    factory = session_factory()
    with factory() as db:
        claim_result = claim(db)
        if not claim_result:
            return False
        job_id, version_id, fence, attempt_no = claim_result
        staging = version_dir(version_id) / f"attempt-{job_id}-{fence}"
        index_id = None
        try:
            staging.mkdir(exist_ok=True)
            locked_job(db, job_id, fence)
            version = db.get(MaterialVersion, version_id)
            if version.parse_status != "ready":
                version.parse_status = "processing"
            db.commit()
            extracted = list(
                extract(version, staging, lambda phase: update_phase(db, job_id, fence, phase))
            )
            page_count = version.page_count
            if not extracted:
                raise ApiError(422, "no_text_extracted", "未提取到可用文字")
            update_phase(db, job_id, fence, "indexing")
            profile = f"{get_settings().embedding_model}:{get_settings().embedding_dimensions}"
            locked_job(db, job_id, fence)
            generation = (
                db.scalar(
                    select(sa.func.max(IndexVersion.generation)).where(
                        IndexVersion.material_version_id == version_id
                    )
                )
                or 0
            ) + 1
            index = IndexVersion(
                id=uuid4(),
                material_version_id=version_id,
                generation=generation,
                pipeline_profile="document-v1",
                embedding_profile=profile,
                embedding_dim=1024,
                status="building",
                is_active=False,
                chunk_count=0,
            )
            db.add(index)
            db.commit()
            index_id = index.id
            chunk_rows = [
                (part, locator, ordinal)
                for ordinal, (text, locator) in enumerate(extracted)
                for part in chunks(text)
            ]
            from study_platform.services.models import embed_texts

            vectors = []
            for offset in range(0, len(chunk_rows), 20):
                update_phase(db, job_id, fence, "indexing")
                batch = chunk_rows[offset : offset + 20]
                result = embed_texts([row[0] for row in batch])
                if len(result) != len(batch) or any(len(v) != 1024 for v in result):
                    raise ApiError(502, "embedding_invalid", "向量模型返回维度或内容不正确")
                vectors.extend(result)
            job = locked_job(db, job_id, fence)
            version = db.get(MaterialVersion, version_id)
            index = db.get(IndexVersion, index_id)
            sections = []
            for ordinal, (text, locator) in enumerate(extracted):
                section = MaterialSection(
                    id=uuid4(),
                    material_version_id=version_id,
                    ordinal=ordinal,
                    level=1,
                    text=text,
                    locator=locator,
                )
                db.add(section)
                sections.append(section)
            db.flush()
            for ordinal, ((text, locator, section_no), vector) in enumerate(
                zip(chunk_rows, vectors, strict=True)
            ):
                chunk = MaterialChunk(
                    id=uuid4(),
                    index_version_id=index.id,
                    material_version_id=version_id,
                    section_id=sections[section_no].id,
                    ordinal=ordinal,
                    text=text,
                    text_sha256=hashlib.sha256(text.encode()).hexdigest(),
                    locator=locator,
                )
                db.add(chunk)
                db.flush()
                db.add(
                    ChunkEmbedding(chunk_id=chunk.id, model_profile_id=profile, embedding=vector)
                )
            db.execute(
                sa.update(IndexVersion)
                .where(
                    IndexVersion.material_version_id == version_id, IndexVersion.is_active.is_(True)
                )
                .values(is_active=False, status="retired")
            )
            index.status = "ready"
            index.is_active = True
            index.ready_at = now()
            index.chunk_count = len(chunk_rows)
            version.page_count = page_count
            version.parse_status = "ready"
            version.parser_profile = "document-v1"
            version.failure_code = None
            job.status = "succeeded"
            job.progress = 100
            job.result = {"phase": "ready"}
            job.finished_at = now()
            attempt = db.scalar(
                select(AiJobAttempt).where(
                    AiJobAttempt.job_id == job_id, AiJobAttempt.attempt_no == attempt_no
                )
            )
            attempt.outcome = "success"
            attempt.finished_at = now()
            # Copy page previews before publication while the job row is locked.
            for image in staging.glob("page-*.png"):
                shutil.copyfile(image, version_dir(version_id) / image.name)
            db.commit()
        except LostLease:
            db.rollback()
        except Exception as exc:
            db.rollback()
            try:
                job = locked_job(db, job_id, fence)
                retryable = (isinstance(exc, ApiError) and exc.retryable) or (
                    isinstance(exc, DBAPIError) and connection_failure(exc)
                )
                if isinstance(exc, ApiError):
                    code = exc.code
                elif isinstance(exc, DBAPIError):
                    code = "database_unavailable" if retryable else "database_error"
                elif isinstance(exc, OSError):
                    code = "storage_unavailable"
                else:
                    code = "ingestion_failed"
                job.failure_code = str(code)[:64]
                job.status = "queued" if retryable and attempt_no < job.max_attempts else "failed"
                job.next_run_at = now() + timedelta(seconds=5)
                job.finished_at = now() if job.status == "failed" else None
                version = db.get(MaterialVersion, version_id)
                if version.parse_status != "ready":
                    version.parse_status = "failed" if job.status == "failed" else "pending"
                    version.failure_code = job.failure_code
                if index_id:
                    index = db.get(IndexVersion, index_id)
                    index.status = "failed"
                    index.failure_code = job.failure_code
                attempt = db.scalar(
                    select(AiJobAttempt).where(
                        AiJobAttempt.job_id == job_id, AiJobAttempt.attempt_no == attempt_no
                    )
                )
                attempt.outcome = "retry" if job.status == "queued" else "failed"
                attempt.failure_code = job.failure_code
                attempt.finished_at = now()
                db.commit()
            except LostLease:
                db.rollback()
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    return True
