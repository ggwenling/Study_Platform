"""Stable historical message and citation representations."""

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


def citation_dto(db, citation) -> dict:
    chunk = db.get(MaterialChunk, citation.chunk_id)
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
    label = next(
        (
            (f"第 {locator[key]} 页" if key == "page" else f"{label} {locator[key]}")
            for key, label in (("page", "第页"), ("paragraph", "段落"), ("line", "行"))
            if key in locator
        ),
        "原文",
    )
    base = f"/api/v1/material-versions/{version.id}"
    page = locator.get("page", locator.get("image"))
    preview = f"{base}/preview"
    if isinstance(page, int) and page > 0 and locator.get("ocr") and page_path(version.id, page).is_file():
        preview = f"{base}/pages/{page}"
    return {
        "id": citation.id,
        "source_label": f"S{citation.ordinal + 1}",
        "material_id": material.id,
        "material_version_id": version.id,
        "knowledge_base_id": kb.id,
        "title": material.title,
        "quote": chunk.text,
        "locator": locator,
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
        scope = {
            "knowledge_base_id": snapshot["knowledge_base_id"],
            "knowledge_base_name": snapshot.get(
                "knowledge_base_name", snapshot.get("name_snapshot", "")
            ),
            "answer_mode": snapshot.get("answer_mode", "knowledge_only"),
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
