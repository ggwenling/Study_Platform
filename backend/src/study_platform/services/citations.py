"""Stable historical message and citation representations."""

from hashlib import sha256

from sqlalchemy import select

from study_platform.persistence.models import (
    AiWorkflowRun,
    IndexVersion,
    KnowledgeBase,
    Material,
    MaterialChunk,
    MaterialVersion,
    MessageCitation,
)
from study_platform.services.storage import page_path, source_path


def citation_quote(db, citation) -> tuple[str, str]:
    if citation.quote_text is not None:
        return citation.quote_text, "verified"
    text = db.get(MaterialChunk, citation.chunk_id).text
    if citation.quote_sha256 is None:
        return text, "unverified"
    if sha256(text.encode()).hexdigest() == citation.quote_sha256:
        return text, "verified"
    return "", "unavailable"


def citation_dto(db, citation) -> dict:
    version = db.get(MaterialVersion, citation.material_version_id)
    material = db.get(Material, version.material_id)
    kb = db.get(KnowledgeBase, material.knowledge_base_id)
    index = db.get(IndexVersion, citation.index_version_id)
    availability = "available"
    if kb.deleted_at:
        availability = "knowledge_base_deleted"
    elif material.deleted_at or material.status == "deleted":
        availability = "material_deleted"
    elif not index or index.status not in ("ready", "retired"):
        availability = "index_unavailable"
    locator = citation.locator or {}
    if isinstance(locator.get("slide"), int) and locator["slide"] > 0:
        label = f"第 {locator['slide']} 页幻灯片"
        if locator.get("notes"):
            label += " · 备注"
    elif locator.get("heading"):
        line_no = locator.get("start_line")
        label = (
            f"{locator['heading']} · 第 {line_no} 行"
            if isinstance(line_no, int)
            else str(locator["heading"])
        )
    else:
        label = next(
            (
                (
                    f"第 {locator[key]} 页"
                    if key == "page"
                    else f"第 {locator[key]} 行"
                    if key == "start_line"
                    else f"{prefix} {locator[key]}"
                )
                for key, prefix in (
                    ("page", "第页"),
                    ("paragraph", "段落"),
                    ("start_line", "第行"),
                    ("line", "行"),
                )
                if key in locator
            ),
            "原文",
        )
    if locator.get("table_path"):
        label = " / ".join(
            f"表格 {part['table']}，第 {part['row']} 行，第 {part['column']} 列"
            for part in locator["table_path"]
        )
        label += f"，段落 {locator['cell_paragraph']}"
    base = f"/api/v1/material-versions/{version.id}"
    page = locator.get("page", locator.get("image"))
    preview = f"{base}/preview"
    if isinstance(page, int) and page > 0 and locator.get("ocr") and page_path(version.id, page).is_file():
        preview = f"{base}/pages/{page}"
    quote, quote_status = citation_quote(db, citation) if availability == "available" else ("", "unavailable")
    return {
        "id": citation.id,
        "chunk_id": citation.chunk_id,
        "source_label": f"S{citation.ordinal + 1}",
        "material_id": material.id,
        "material_version_id": version.id,
        "knowledge_base_id": kb.id,
        "title": material.title,
        "source_url": material.source_url,
        "quote": quote,
        "quote_status": quote_status,
        "locator": locator if availability == "available" else {},
        "location_label": label,
        "version_no": version.version_no,
        "availability": "available" if availability == "available" else "unavailable",
        "unavailable_reason": None if availability == "available" else availability,
        "content_url": f"{base}/content"
        if availability == "available" and source_path(version.id).is_file()
        else None,
        "preview_url": preview if availability == "available" else None,
    }


def message_dto(db, message) -> dict:
    run = db.get(AiWorkflowRun, message.workflow_run_id) if message.workflow_run_id else None
    snapshot = run.source_scope if run else {}
    scope = None
    if snapshot.get("knowledge_base_id"):
        raw_ids = snapshot.get("material_ids")
        scope = {
            "knowledge_base_id": snapshot["knowledge_base_id"],
            "knowledge_base_name": snapshot.get(
                "knowledge_base_name", snapshot.get("name_snapshot", "")
            ),
            "answer_mode": snapshot.get("answer_mode", "knowledge_only"),
            "intent": snapshot.get("intent", "qa"),
            "selected_material_ids": raw_ids if isinstance(raw_ids, list) and raw_ids else None,
            "index_version_ids": snapshot.get("index_version_ids", []),
        }
    citations = db.scalars(
        select(MessageCitation)
        .where(MessageCitation.message_id == message.id)
        .order_by(MessageCitation.ordinal)
    ).all()
    return {
        "id": message.id,
        "conversation_id": message.conversation_id,
        "role": message.role,
        "content": message.content,
        "status": "stopped" if message.status == "cancelled" else message.status,
        "sequence_no": message.sequence_no,
        "turn_id": message.workflow_run_id,
        "created_at": message.created_at,
        "scope": scope,
        "citations": [citation_dto(db, c) for c in citations],
    }
