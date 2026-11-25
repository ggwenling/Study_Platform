"""Read ordered source chunks and render bounded, cached page previews."""

import os
from pathlib import Path
from threading import Lock
from uuid import UUID, uuid4

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from study_platform.core.config import get_settings
from study_platform.core.errors import ApiError
from study_platform.persistence.models import IndexVersion, Material, MaterialChunk, MaterialVersion
from study_platform.services.storage import page_path, source_path

IMAGE_MIMES = {"image/png", "image/jpeg"}
PDFIUM_LOCK = Lock()


class ReaderChunk(BaseModel):
    id: UUID
    ordinal: int
    text: str
    locator: dict
    location_label: str
    is_target: bool


class MaterialReaderResponse(BaseModel):
    material_id: UUID
    version_id: UUID
    knowledge_base_id: UUID
    title: str
    mime_type: str
    source_url: str | None
    page_count: int | None
    preview_page_count: int
    can_render_pages: bool
    content_url: str | None
    target_chunk_id: UUID | None
    target_page: int | None
    target_quote: str | None
    chunks: list[ReaderChunk]
    total_chunks: int
    has_prev: bool
    has_next: bool


def locator_label(locator: dict) -> str:
    if "slide" in locator:
        return f"第 {locator['slide']} 页幻灯片" + (" · 备注" if locator.get("notes") else "")
    if locator.get("heading"):
        line_no = locator.get("start_line")
        return f"{locator['heading']} · 第 {line_no} 行" if isinstance(line_no, int) else str(locator["heading"])
    if locator.get("table_path"):
        parts = []
        for part in locator["table_path"]:
            segment = f"表格 {part['table']}，第 {part['row']} 行"
            if "column" in part:
                segment += f"，第 {part['column']} 列"
            parts.append(segment)
        joined = " / ".join(parts)
        if "cell_paragraph" in locator:
            return f"{joined}，段落 {locator['cell_paragraph']}"
        return joined
    for key, fmt in (
        ("page", "第 {} 页"),
        ("paragraph", "段落 {}"),
        ("start_line", "第 {} 行"),
        ("line", "行 {}"),
        ("image", "图片 {}"),
    ):
        if key in locator:
            return fmt.format(locator[key])
    return "原文"


def reader_locator(locator: dict) -> dict:
    # The reader needs an OCR badge, not the repeated bounding boxes and text.
    return {**locator, "ocr": bool(locator["ocr"])} if "ocr" in locator else locator


def reader_chunks(db: Session, version_id: UUID, chunk_id: UUID | None, window: int):
    indices = select(IndexVersion.id).where(
        IndexVersion.material_version_id == version_id,
        IndexVersion.status.in_(("ready", "retired")),
    )
    target = None
    if chunk_id is not None:
        target = db.scalar(
            select(MaterialChunk).where(
                MaterialChunk.id == chunk_id,
                MaterialChunk.index_version_id.in_(indices),
            )
        )
        if target is None:
            raise ApiError(404, "not_found", "Resource not found")
        index_id = target.index_version_id
    else:
        index_id = db.scalar(
            indices.order_by(IndexVersion.is_active.desc(), IndexVersion.generation.desc()).limit(1)
        )
    if index_id is None:
        return target, [], 0, False, False

    in_index = MaterialChunk.index_version_id == index_id
    query = select(MaterialChunk).where(in_index)
    ordinal = MaterialChunk.ordinal
    count, first, last = db.execute(
        select(func.count(), func.min(ordinal), func.max(ordinal)).where(in_index)
    ).one()
    if target is None:
        rows = list(db.scalars(query.order_by(ordinal).limit(window + 1)))
        target = rows[0] if rows else None
    else:
        prev = list(db.scalars(query.where(ordinal < target.ordinal).order_by(ordinal.desc()).limit(window)))
        following = list(db.scalars(query.where(ordinal > target.ordinal).order_by(ordinal).limit(window)))
        rows = [*reversed(prev), target, *following]
    return (
        target,
        rows,
        count,
        bool(rows and first < rows[0].ordinal),
        bool(rows and last > rows[-1].ordinal),
    )


def material_reader(
    db: Session, material: Material, version: MaterialVersion, chunk_id: UUID | None, window: int
) -> MaterialReaderResponse:
    target, rows, count, has_prev, has_next = reader_chunks(db, version.id, chunk_id, window)
    has_content = version.parse_status == "ready" and source_path(version.id).is_file()
    preview_count = 0
    if has_content:
        if version.source_mime == "application/pdf":
            preview_count = min(version.page_count or 0, get_settings().max_pdf_page_preview)
        elif version.source_mime in IMAGE_MIMES:
            preview_count = 1
    locator = target.locator if target else {}
    target_page = locator.get("page", locator.get("image"))
    return MaterialReaderResponse(
        material_id=material.id,
        version_id=version.id,
        knowledge_base_id=material.knowledge_base_id,
        title=material.title,
        mime_type=version.source_mime,
        source_url=material.source_url,
        page_count=version.page_count,
        preview_page_count=preview_count,
        can_render_pages=preview_count > 0,
        content_url=f"/api/v1/material-versions/{version.id}/content" if has_content else None,
        target_chunk_id=target.id if target else None,
        target_page=target_page if isinstance(target_page, int) else None,
        target_quote=target.text if target else None,
        chunks=[
            ReaderChunk(
                id=row.id, ordinal=row.ordinal, text=row.text, locator=reader_locator(row.locator),
                location_label=locator_label(row.locator), is_target=row is target,
            )
            for row in rows
        ],
        total_chunks=count,
        has_prev=has_prev,
        has_next=has_next,
    )


def render_pdf_page(version_id: UUID, page_no: int) -> Path:
    import pypdfium2 as pdfium

    destination = page_path(version_id, page_no)
    source = source_path(version_id)
    # PDFium requires serial calls within a process, and a second request may
    # have populated this page while the first request held the lock.
    with PDFIUM_LOCK:
        if destination.is_file():
            return destination
        if not source.is_file():
            raise ApiError(404, "not_found", "Resource not found")
        temp = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
        try:
            with pdfium.PdfDocument(source) as document:
                if page_no > len(document):
                    raise ApiError(404, "not_found", "Resource not found")
                page = document[page_no - 1]
                try:
                    width, height = page.get_size()
                    scale = min(1.5, 2500 / max(width, height))
                    bitmap = page.render(scale=scale)
                    try:
                        with bitmap.to_pil() as image:
                            image.save(temp, format="PNG")
                    finally:
                        bitmap.close()
                finally:
                    page.close()
            os.replace(temp, destination)
        except pdfium.PdfiumError as exc:
            raise ApiError(422, "page_render_failed", "无法预览该 PDF 页面，请下载原件查看") from exc
        finally:
            temp.unlink(missing_ok=True)
    return destination
