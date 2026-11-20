"""Resolve immutable user model snapshots and protect in-flight references."""

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import sqlalchemy as sa
from cryptography.fernet import Fernet, InvalidToken

from study_platform.core.config import get_settings
from study_platform.core.errors import ApiError
from study_platform.persistence.database import session_factory
from study_platform.persistence.models.knowledge import KnowledgeBase
from study_platform.persistence.models.model_connections import (
    ModelConnection,
    ModelConnectionLease,
)

selection = ContextVar("model_selection", default=None)


def cipher():
    try:
        return Fernet(get_settings().model_connections_key.get_secret_value().encode())
    except (ValueError, TypeError):
        raise ApiError(
            503, "model_encryption_unavailable", "管理员尚未配置有效的模型凭据加密密钥。"
        ) from None


def encrypt(key):
    return cipher().encrypt(key.encode()).decode()


def decrypt(value):
    try:
        return cipher().decrypt(value.encode()).decode()
    except InvalidToken:
        raise ApiError(
            503, "model_decryption_failed", "模型凭据无法解密，请联系管理员或重新填写 Key。"
        ) from None


def owned(db, owner, connection_id, lock=False):
    stmt = sa.select(ModelConnection).where(
        ModelConnection.id == connection_id,
        ModelConnection.owner_user_id == owner,
        ModelConnection.deleted_at.is_(None),
    )
    row = db.scalar(stmt.with_for_update() if lock else stmt)
    if row is None:
        raise ApiError(404, "not_found", "模型连接不存在。")
    return row


def snapshot(row):
    return {"id": str(row.id), "revision": row.revision, "kind": row.kind}


def default_snapshot(db, owner, kind):
    # Same owner advisory lock serializes default selection, references and deletion.
    lock_owner(db, owner)
    row = db.scalar(
        sa.select(ModelConnection).where(
            ModelConnection.owner_user_id == owner,
            ModelConnection.kind == kind,
            ModelConnection.deleted_at.is_(None),
            ModelConnection.is_default.is_(True),
        )
    )
    if row and row.status != "ready":
        raise ApiError(409, "model_test_required", "当前默认连接已变更，请测试通过后重新使用。")
    return snapshot(row) if row else {"id": None, "kind": kind}


def vision_snapshot(db, owner):
    try:
        saved = default_snapshot(db, owner, "vision")
    except ApiError as error:
        if error.code == "model_test_required":
            raise ApiError(409, "vision_not_configured", "请先配置并测试视觉理解模型。") from None
        raise
    if not saved.get("id"):
        raise ApiError(409, "vision_not_configured", "请先配置并测试视觉理解模型。")
    return saved


def lock_owner(db, owner):
    db.execute(
        sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:owner, 0))"),
        {"owner": f"models:{owner}"},
    )


def platform_binding():
    settings = get_settings()
    return {
        "base_url": settings.embedding_base_url.rstrip("/"),
        "model": settings.embedding_model,
        "dimensions": 1024,
        "profile": f"{settings.embedding_model}:1024",
    }


def bind_library(db, owner):
    binding = default_snapshot(db, owner, "embedding")
    if not binding["id"]:
        return None, platform_binding()
    row = owned(db, owner, UUID(binding["id"]))
    version = row.versions[str(row.revision)]
    return row.id, {
        "base_url": version["base_url"],
        "model": version["model"],
        "dimensions": 1024,
        "profile": f"connection:{row.id}:1024",
    }


def library_snapshot(db, owner, kb_id):
    kb = db.scalar(
        sa.select(KnowledgeBase).where(
            KnowledgeBase.id == kb_id,
            KnowledgeBase.owner_user_id == owner,
            KnowledgeBase.deleted_at.is_(None),
        )
    )
    if not kb:
        raise ApiError(404, "not_found", "知识库不存在。")
    if kb.embedding_connection_id:
        row = owned(db, owner, kb.embedding_connection_id)
        if row.status != "ready":
            raise ApiError(409, "model_test_required", "知识库绑定的向量连接需要测试通过。")
        return {**snapshot(row), "binding": kb.embedding_binding}
    return {"id": None, "kind": "embedding", "binding": kb.embedding_binding or platform_binding()}


def resolved(owner, kind, saved=None):
    current = selection.get()
    if saved is None and current and current["owner"] == owner:
        saved = current.get(kind)
    if saved is None:
        saved = {"id": None, "kind": kind}
    if not saved.get("id"):
        return {**saved, "source": "platform"}
    with session_factory()() as db:
        row = owned(db, owner, UUID(saved["id"]))
        if row.kind != kind:
            raise ApiError(409, "model_connection_kind_mismatch", "模型连接类型与任务不匹配。")
        version = row.versions.get(str(saved["revision"]))
        if not version:
            raise ApiError(409, "model_connection_changed", "模型连接版本不可用。")
        return {
            **version,
            **saved,
            "api_key": decrypt(version["key"]),
            "source": f"connection:{row.id}:{saved['revision']}",
        }


def llm_available(owner):
    current = selection.get()
    if current and current["owner"] == owner:
        return bool(current.get("llm", {}).get("id")) or bool(
            get_settings().deepseek_api_key.get_secret_value()
        )
    with session_factory()() as db:
        personal = default_snapshot(db, owner, "llm") if owner else {}
    return bool(personal.get("id")) or bool(get_settings().deepseek_api_key.get_secret_value())


@contextmanager
def task(owner, *, llm=None, embedding=None, vision=None, rerank=None, kb_id=None):
    existing = selection.get()
    if existing and existing["owner"] == owner:
        yield existing
        return
    lease_ids = []
    with session_factory()() as db:
        llm = llm if llm is not None else default_snapshot(db, owner, "llm")
        embedding = (
            embedding
            if embedding is not None
            else library_snapshot(db, owner, kb_id)
            if kb_id
            else None
        )
        for config in (llm, embedding, vision, rerank):
            if config and config.get("id"):
                row = owned(db, owner, UUID(config["id"]), lock=True)
                lease = ModelConnectionLease(
                    id=uuid4(),
                    connection_id=row.id,
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
                )
                db.add(lease)
                lease_ids.append(lease.id)
        db.commit()
    token = selection.set({"owner": owner, "llm": llm, "embedding": embedding, "vision": vision, "rerank": rerank})
    try:
        yield selection.get()
    finally:
        selection.reset(token)
        if lease_ids:
            with session_factory()() as db:
                db.execute(
                    sa.delete(ModelConnectionLease).where(ModelConnectionLease.id.in_(lease_ids))
                )
                db.commit()


def embedding_profile(owner=None, kb_id=None, db=None):
    current = selection.get()
    config = current.get("embedding") if current and current["owner"] == owner else None
    if config is None and kb_id is not None:
        if db is not None:
            config = library_snapshot(db, owner, kb_id)
        else:
            with session_factory()() as session:
                config = library_snapshot(session, owner, kb_id)
    return (config or {}).get("binding", platform_binding())["profile"]
