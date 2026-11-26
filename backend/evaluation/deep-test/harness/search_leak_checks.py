"""Shared ownership checks for workspace /search results (deep-test harness)."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Session

from study_platform.persistence.models.conversations import Conversation, Note
from study_platform.persistence.models.identity import Material
from study_platform.persistence.models.knowledge import KnowledgeBase
from study_platform.persistence.models.materials import MaterialChunk, MaterialVersion


def classify_search_items(
    db: Session,
    items: list[dict],
    *,
    requester_user_id: str,
    peer_user_id: str,
    peer_knowledge_base_id: str,
    peer_material_id: str,
    peer_exact_canary: str,
) -> tuple[list[dict], list[dict]]:
    """Return (classified_rows, leak_rows). A leak is peer-owned resource or exact peer canary text."""
    req_uid = sa.UUID(requester_user_id)
    peer_uid = sa.UUID(peer_user_id)
    peer_kb = str(peer_knowledge_base_id)
    peer_mat = str(peer_material_id)
    classified: list[dict] = []
    for it in items:
        owner_uid = None
        material_id = None
        kb_id = it.get("knowledge_base_id")
        if it.get("type") == "fragment":
            ch = db.scalar(sa.select(MaterialChunk).where(MaterialChunk.id == it["id"]))
            if ch:
                ver = db.get(MaterialVersion, ch.material_version_id)
                if ver:
                    mat = db.get(Material, ver.material_id)
                    if mat:
                        material_id = str(mat.id)
                        kb_id = str(mat.knowledge_base_id)
                        owner_uid = mat.owner_user_id
        elif it.get("type") == "material":
            mat = db.get(Material, it["id"])
            if mat:
                material_id = str(mat.id)
                owner_uid = mat.owner_user_id
        elif it.get("type") == "knowledge_base":
            kb = db.get(KnowledgeBase, it["id"])
            if kb:
                owner_uid = kb.owner_user_id
        elif it.get("type") == "note":
            note = db.get(Note, it["id"])
            if note:
                owner_uid = note.owner_user_id
        elif it.get("type") == "conversation":
            conv = db.get(Conversation, it["id"])
            if conv:
                owner_uid = conv.owner_user_id

        text_blob = (it.get("title") or "") + " " + (it.get("excerpt") or "")
        row = {
            "type": it.get("type"),
            "id": str(it.get("id")),
            "knowledge_base_id": str(kb_id) if kb_id else None,
            "db_material_id": material_id,
            "db_owner_user_id": str(owner_uid) if owner_uid else None,
            "contains_peer_exact_canary": peer_exact_canary in text_blob,
            "is_peer_owned": owner_uid == peer_uid,
            "is_requester_owned": owner_uid == req_uid,
            "foreign_kb": kb_id is not None and str(kb_id) == peer_kb,
            "foreign_material": material_id is not None and str(material_id) == peer_mat,
        }
        classified.append(row)

    leaks = [
        c
        for c in classified
        if c["is_peer_owned"] or c["foreign_kb"] or c["foreign_material"] or c["contains_peer_exact_canary"]
    ]
    return classified, leaks