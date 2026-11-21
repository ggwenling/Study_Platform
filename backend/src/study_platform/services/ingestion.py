"""Durable ingestion worker and document extraction."""

import hashlib
import re
import shutil
import socket
import zipfile
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from io import BytesIO
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
from study_platform.services import classification, model_connections
from study_platform.services.lexical import lexical_column_values
from study_platform.services.model_usage import ModelUsageContext
from study_platform.services.models import embed_texts
from study_platform.services.storage import source_path, version_dir
from study_platform.services.vision_pipeline import extract_vision

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


def ensure_ooxml_uncompressed_limit(path):
    settings = get_settings()
    with zipfile.ZipFile(path) as archive:
        total = sum(info.file_size for info in archive.infolist())
        if total > settings.max_docx_uncompressed_bytes:
            raise ValueError("OOXML uncompressed payload too large")


def extract_docx(path, *, legacy_columns=False):
    ensure_ooxml_uncompressed_limit(path)
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
                    columns_before = 0 if legacy_columns else row.grid_cols_before
                    if columns_before < 0:
                        raise ValueError("DOCX table row has a negative leading column count")
                    for column_number, cell in enumerate(row.cells, columns_before + 1):
                        if cell._tc in seen_cells:
                            continue
                        seen_cells.add(cell._tc)
                        location = {"table": table_number, "row": row_number, "column": column_number}
                        yield from blocks(cell, (*table_path, location))

    yield from blocks(Document(path))


_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_ATX_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$")


def extract_markdown(path):
    """Yield Markdown sections split on ATX headings while ignoring fenced code blocks."""
    in_fence: tuple[str, int] | None = None
    heading: str | None = None
    level = 1
    start_line: int | None = None
    end_line = 0
    buf: list[str] = []

    def flush():
        body = "\n".join(buf).strip()
        if not body or start_line is None:
            return None
        loc: dict = {"start_line": start_line, "end_line": end_line}
        if heading:
            loc["heading"] = heading
            loc["level"] = level
        return body, loc

    with path.open(encoding="utf-8-sig") as f:
        for line_no, raw in enumerate(f, 1):
            line = raw.rstrip("\r\n")
            fence_match = _FENCE_RE.match(line)
            if fence_match:
                marker, rest = fence_match.group(1), fence_match.group(2)
                if in_fence is None:
                    if marker[0] != "`" or "`" not in rest:
                        in_fence = (marker[0], len(marker))
                elif marker[0] == in_fence[0] and len(marker) >= in_fence[1] and not rest.strip():
                    in_fence = None
            elif in_fence is None:
                heading_match = _ATX_HEADING_RE.match(line)
                if heading_match:
                    item = flush()
                    if item is not None:
                        yield item
                    heading = heading_match.group(2).strip()
                    level = len(heading_match.group(1))
                    start_line = line_no
                    end_line = line_no
                    buf = [line]
                    continue
            if line.strip():
                if start_line is None:
                    start_line = line_no
                end_line = line_no
                buf.append(line)
            elif buf:
                buf.append("")

    item = flush()
    if item is not None:
        yield item


def extract_pptx(path, version=None):
    ensure_ooxml_uncompressed_limit(path)
    from PIL import Image
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    settings = get_settings()
    presentation = Presentation(path)
    slide_count = len(presentation.slides)
    if slide_count == 0:
        raise ValueError("Invalid PPTX slide count")
    if version is not None:
        version.page_count = slide_count

    def shape_contents(shapes):
        for shape in shapes:
            if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
                yield from shape_contents(shape.shapes)
            elif shape.has_table:
                rows = [
                    " | ".join(cell.text.strip() for cell in row.cells).strip()
                    for row in shape.table.rows
                ]
                text = "\n".join(row for row in rows if row.strip())
                if text:
                    yield text
            elif shape.has_text_frame and shape.text.strip():
                yield shape.text.strip()

    def pictures(shapes):
        for shape in shapes:
            if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
                yield from pictures(shape.shapes)
            elif shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                yield shape

    ocr_count = 0
    for slide_no, slide in enumerate(presentation.slides, 1):
        heading_shape = slide.shapes.title
        heading = heading_shape.text.strip() if heading_shape else ""
        parts = list(shape_contents(slide.shapes))
        visible_text = "\n\n".join(parts)
        if visible_text:
            locator = {"slide": slide_no}
            if heading:
                locator["heading"] = heading
            yield visible_text, locator

        if sum(char.isalnum() for char in visible_text) < 20:
            ocr_text = []
            for shape in pictures(slide.shapes):
                if ocr_count >= settings.max_pptx_ocr_images:
                    continue
                image_part = shape.image
                if image_part.content_type not in ("image/png", "image/jpeg"):
                    continue
                with Image.open(BytesIO(image_part.blob)) as image:
                    if image.width * image.height > settings.max_image_pixels:
                        raise ValueError("Invalid PPTX image")
                    image.load()
                    lines = ocr_image(image)
                ocr_count += 1
                if lines:
                    ocr_text.append("\n".join(line["text"] for line in lines))
            if ocr_text:
                yield "\n\n".join(ocr_text), {"slide": slide_no, "ocr": True}

        notes = ""
        if slide.has_notes_slide:
            notes_frame = slide.notes_slide.notes_text_frame
            notes = notes_frame.text.strip() if notes_frame is not None else ""
        if notes:
            yield notes, {"slide": slide_no, "notes": True}


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
    elif mime == "text/markdown":
        yield from extract_markdown(path)
    elif mime.endswith("wordprocessingml.document"):
        yield from extract_docx(path)
    elif mime.endswith("presentationml.presentation"):
        yield from extract_pptx(path, version)
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
            AiJob.job_type.in_(("ingest", "classification")),
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


def update_phase(db, job_id, fence, phase, **details):
    job = locked_job(db, job_id, fence)
    job.result = {**job.result, "phase": phase, **details}
    job.progress = {"parsing": 10, "ocr": 30, "vision": 30, "indexing": 60}[phase]
    job.lease_expires_at = now() + timedelta(seconds=get_settings().job_lease_seconds)
    owner_id = job.owner_user_id
    db.commit()
    return owner_id


def run_once():
    factory = session_factory()
    with factory() as db:
        claim_result = claim(db)
        if not claim_result:
            return False
        job_id, version_id, fence, attempt_no = claim_result
        job = db.get(AiJob, job_id)
        version = db.get(MaterialVersion, version_id)
        material = db.get(Material, version.material_id)
        owner = job.owner_user_id
        with ExitStack() as task_scope:
            staging = version_dir(version_id) / f"attempt-{job_id}-{fence}"
            index_id = None
            is_ingestion = db.get(AiJob, job_id).job_type == "ingest"
            try:
                embedding = (job.payload.get("embedding_connection") or model_connections.library_snapshot(db, owner, material.knowledge_base_id)) if is_ingestion else None
                llm = job.payload.get("llm_connection", {"id": None, "kind": "llm"})
                vision = job.payload.get("vision_connection") if is_ingestion else None
                db.commit()
                task_scope.enter_context(model_connections.task(owner, llm=llm, embedding=embedding, vision=vision))
                if not is_ingestion:
                    job = locked_job(db, job_id, fence)
                    payload = classification.suggestion_input(db, job)
                    owner_id = job.owner_user_id
                    db.commit()
                    result = classification.suggest(payload, owner_user_id=owner_id)
                    job = locked_job(db, job_id, fence)
                    active = classification.active_index(db, db.get(MaterialVersion, version_id).material_id)
                    if active is None or str(active.id) != job.payload["index_id"]:
                        raise ApiError(409, "index_changed", "资料索引已更新。")
                    job.result = {"suggestion": result}
                    job.status = "succeeded"
                    job.progress = 100
                    job.finished_at = now()
                    attempt = db.scalar(select(AiJobAttempt).where(
                        AiJobAttempt.job_id == job_id, AiJobAttempt.attempt_no == attempt_no))
                    attempt.outcome = "success"
                    attempt.finished_at = now()
                    db.commit()
                    return True
                staging.mkdir(exist_ok=True)
                locked_job(db, job_id, fence)
                version = db.get(MaterialVersion, version_id)
                if version.parse_status != "ready":
                    version.parse_status = "processing"
                db.commit()
                def phase_changed(phase):
                    update_phase(db, job_id, fence, phase)

                pipeline = "document-v1"
                if job.payload.get("extraction_mode") == "vlm":
                    if not vision:
                        raise ApiError(409, "vision_not_configured", "当前任务没有就绪的视觉模型连接。")
                    pipeline = "document-vision-v1"
                    page_count, extracted = extract_vision(
                        source_path(version.id), version.source_mime, phase_changed, owner=owner, saved=vision,
                    )
                else:
                    extracted = list(extract(version, staging, phase_changed))
                    page_count = version.page_count
                if not extracted:
                    raise ApiError(422, "no_text_extracted", "未提取到可用文字")
                update_phase(db, job_id, fence, "indexing", parsed=True,
                             ocr_count=sum(bool(locator.get("ocr")) for _, locator in extracted))
                profile = model_connections.embedding_profile(owner)
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
                    pipeline_profile=pipeline,
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
                vectors = []
                for offset in range(0, len(chunk_rows), 20):
                    owner_id = update_phase(db, job_id, fence, "indexing",
                                            chunk_count=len(chunk_rows), embedded_count=len(vectors))
                    batch = chunk_rows[offset : offset + 20]
                    result = embed_texts(
                        [row[0] for row in batch],
                        context=ModelUsageContext(owner_id, "embedding"),
                    )
                    if len(result) != len(batch) or any(len(v) != 1024 for v in result):
                        raise ApiError(502, "embedding_invalid", "向量模型返回维度或内容不正确")
                    vectors.extend(result)
                job = locked_job(db, job_id, fence)
                version = db.get(MaterialVersion, version_id)
                index = db.get(IndexVersion, index_id)
                sections = []
                for ordinal, (text, locator) in enumerate(extracted):
                    raw_level = locator.get("level", 1)
                    section = MaterialSection(
                        id=uuid4(),
                        material_version_id=version_id,
                        ordinal=ordinal,
                        heading=locator.get("heading"),
                        level=raw_level if isinstance(raw_level, int) and 1 <= raw_level <= 8 else 1,
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
                        **lexical_column_values(text),
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
                version.parser_profile = pipeline
                version.failure_code = None
                if job.payload.get("activate_version"):
                    prior = (
                        select(MaterialVersion.id)
                        .where(MaterialVersion.material_id == version.material_id, MaterialVersion.is_current.is_(True))
                    )
                    db.execute(sa.update(IndexVersion).where(
                        IndexVersion.material_version_id.in_(prior),
                        IndexVersion.is_active.is_(True),
                    ).values(is_active=False, status="retired"))
                    db.execute(sa.update(MaterialVersion).where(
                        MaterialVersion.id.in_(prior),
                    ).values(is_current=False))
                    version.is_current = True
                job.status = "succeeded"
                job.progress = 100
                job.result = {**job.result, "phase": "ready", "embedded_count": len(vectors)}
                classification.enqueue(db, job.owner_user_id, index)
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
                        code = "ingestion_failed" if is_ingestion else "classification_failed"
                    job.failure_code = str(code)[:64]
                    job.status = "queued" if retryable and attempt_no < job.max_attempts else "failed"
                    job.next_run_at = now() + timedelta(seconds=5)
                    job.finished_at = now() if job.status == "failed" else None
                    version = db.get(MaterialVersion, version_id)
                    if is_ingestion and version.parse_status != "ready":
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
                if is_ingestion:
                    shutil.rmtree(staging, ignore_errors=True)
    return True
