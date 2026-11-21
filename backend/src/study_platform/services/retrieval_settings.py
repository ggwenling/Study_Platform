"""Knowledge-base retrieval overrides and their platform defaults."""

from uuid import UUID

import sqlalchemy as sa

from study_platform.core.config import get_settings
from study_platform.core.errors import ApiError
from study_platform.persistence.models.model_connections import ModelConnection
from study_platform.services import model_connections


def effective_settings(overrides=None, settings=None):
    settings = settings or get_settings()
    result = {
        "top_k": settings.retrieval_top_k,
        "lexical_weight": settings.retrieval_lexical_weight,
        "min_similarity": settings.retrieval_min_similarity,
        "rerank_enabled": False,
        "rerank_connection_id": None,
        "rerank_candidate_k": settings.retrieval_candidate_k,
    }
    if isinstance(overrides, dict):
        for key in result:
            if key in overrides:
                result[key] = overrides[key]
    return result


def rerank_snapshot(db, owner, settings):
    if not settings.get("rerank_enabled"):
        return {"id": None, "kind": "rerank"}
    connection_id = settings.get("rerank_connection_id")
    try:
        if connection_id:
            row = model_connections.owned(db, owner, UUID(str(connection_id)))
            if row.kind != "rerank" or row.status != "ready":
                return {"id": None, "kind": "rerank"}
            return model_connections.snapshot(row)
        return model_connections.default_snapshot(db, owner, "rerank")
    except (ApiError, ValueError):
        # Retrieval is user-facing and reranking is optional; unresolved connections
        # are reported by the hit test or counted as a chat fallback.
        return {"id": None, "kind": "rerank"}


def validate_rerank_connection(db, owner, connection_id):
    if connection_id is None:
        return
    row = db.scalar(
        sa.select(ModelConnection).where(
            ModelConnection.id == connection_id,
            ModelConnection.owner_user_id == owner,
            ModelConnection.deleted_at.is_(None),
        )
    )
    if row is None or row.kind != "rerank":
        raise ApiError(422, "rerank_connection_invalid", "请选择当前账户下的 Rerank 连接。")
