"""Private model connection CRUD, live tests and independent defaults."""

from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID, uuid4

import sqlalchemy as sa
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, SecretStr, field_validator
from sqlalchemy.orm import Session

from study_platform.api.v1.auth import get_current_user
from study_platform.core.config import get_settings
from study_platform.core.errors import ApiError
from study_platform.persistence.database import get_db, session_factory
from study_platform.persistence.models.knowledge import KnowledgeBase
from study_platform.persistence.models.model_connections import (
    ModelConnection,
    ModelConnectionLease,
)
from study_platform.persistence.models.operations import AiJob, AiWorkflowRun
from study_platform.services import model_connections as service
from study_platform.services.model_network import normalize_url

router = APIRouter(prefix="/me/model-connections", tags=["model connections"])


class ConnectionInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    kind: Literal["llm", "embedding"]
    base_url: str = Field(min_length=1, max_length=500)
    model: str = Field(min_length=1, max_length=120)
    api_key: SecretStr | None = Field(default=None, max_length=4096)

    @field_validator("name", "model")
    @classmethod
    def stripped(cls, value):
        value = value.strip()
        if not value or any(ord(c) < 32 for c in value):
            raise ValueError("Empty or invalid value")
        return value


class ConnectionDTO(BaseModel):
    id: UUID
    name: str
    kind: Literal["llm", "embedding"]
    base_url: str
    model: str
    dimensions: int | None
    key_configured: bool
    key_suffix: str
    revision: int
    status: str
    is_default: bool
    tested_at: datetime | None
    error_code: str | None
    libraries: list[dict]


class ConnectionsDTO(BaseModel):
    items: list[ConnectionDTO]
    encryption_available: bool
    platform_llm: str | None
    platform_embedding: str | None


def dto(db, row):
    config = row.versions[str(row.revision)]
    libraries = db.execute(
        sa.select(KnowledgeBase.id, KnowledgeBase.name).where(
            KnowledgeBase.embedding_connection_id == row.id, KnowledgeBase.deleted_at.is_(None)
        )
    ).all()
    return ConnectionDTO(
        id=row.id,
        name=row.name,
        kind=row.kind,
        base_url=config["base_url"],
        model=config["model"],
        dimensions=1024 if row.kind == "embedding" else None,
        key_configured=True,
        key_suffix=config["suffix"],
        revision=row.revision,
        status=row.status,
        is_default=row.is_default,
        tested_at=row.tested_at,
        error_code=row.error_code,
        libraries=[{"id": str(key), "name": name} for key, name in libraries],
    )


@router.get("", response_model=ConnectionsDTO)
def list_connections(db: Session = Depends(get_db), user=Depends(get_current_user)):
    settings = get_settings()
    try:
        service.cipher()
        encryption_available = True
    except ApiError:
        encryption_available = False
    rows = db.scalars(
        sa.select(ModelConnection)
        .where(ModelConnection.owner_user_id == user.id, ModelConnection.deleted_at.is_(None))
        .order_by(ModelConnection.created_at)
    ).all()
    return ConnectionsDTO(
        items=[dto(db, row) for row in rows],
        encryption_available=encryption_available,
        platform_llm=settings.chat_model if settings.deepseek_api_key.get_secret_value() else None,
        platform_embedding=settings.embedding_model
        if settings.embedding_api_key.get_secret_value()
        else None,
    )


def busy(db, row):
    current = datetime.now(timezone.utc)
    lease = db.scalar(
        sa.select(ModelConnectionLease.id)
        .where(
            ModelConnectionLease.connection_id == row.id, ModelConnectionLease.expires_at > current
        )
        .limit(1)
    )
    run = db.scalar(
        sa.select(AiWorkflowRun.id)
        .where(
            AiWorkflowRun.owner_user_id == row.owner_user_id,
            AiWorkflowRun.status.in_(("created", "running")),
            AiWorkflowRun.lease_expires_at > current,
            AiWorkflowRun.source_scope["llm_connection"]["id"].astext == str(row.id),
        )
        .limit(1)
    )
    job = db.scalar(
        sa.select(AiJob.id)
        .where(
            AiJob.owner_user_id == row.owner_user_id,
            AiJob.status.in_(("queued", "running")),
            AiJob.payload["llm_connection"]["id"].astext == str(row.id),
        )
        .limit(1)
    )
    return bool(
        lease
        or run
        or job
        or (
            row.status == "testing"
            and row.tested_at
            and row.tested_at > current - timedelta(minutes=3)
        )
    )


def version(body, previous=None):
    service.cipher()
    key = body.api_key.get_secret_value().strip() if body.api_key else None
    if not key and previous is None:
        raise ApiError(422, "model_key_required", "请填写 API Key。")
    base_url = normalize_url(body.base_url)
    if not key and previous is not None and base_url != previous["base_url"]:
        raise ApiError(422, "model_key_required", "更换模型地址时请填写 API Key。")
    return {
        "base_url": base_url,
        "model": body.model,
        "key": service.encrypt(key) if key else previous["key"],
        "suffix": (key[-4:] if len(key) > 4 else "") if key else previous["suffix"],
    }


@router.post("", response_model=ConnectionDTO, status_code=201)
def create(body: ConnectionInput, db: Session = Depends(get_db), user=Depends(get_current_user)):
    row = ModelConnection(
        id=uuid4(),
        owner_user_id=user.id,
        kind=body.kind,
        name=body.name,
        revision=1,
        versions={"1": version(body)},
        status="draft",
        is_default=False,
    )
    db.add(row)
    db.flush()
    result = dto(db, row)
    db.commit()
    return result


@router.patch("/{connection_id}", response_model=ConnectionDTO)
def update(
    connection_id: UUID,
    body: ConnectionInput,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    service.lock_owner(db, user.id)
    row = service.owned(db, user.id, connection_id, lock=True)
    if body.kind != row.kind:
        raise ApiError(422, "model_kind_immutable", "连接类型不可修改，请添加新连接。")
    old = row.versions[str(row.revision)]
    new = version(body, old)
    libraries = dto(db, row).libraries
    if libraries and (old["base_url"], old["model"]) != (new["base_url"], new["model"]):
        raise ApiError(
            409, "model_connection_bound", "连接已绑定知识库；更换地址或模型请另存新连接。"
        )
    row.name = body.name
    if new != old:
        row.revision += 1
        row.versions = {**row.versions, str(row.revision): new}
        row.status, row.tested_at, row.error_code = "draft", None, None
    db.flush()
    result = dto(db, row)
    db.commit()
    return result


@router.delete("/{connection_id}", status_code=204)
def delete(connection_id: UUID, db: Session = Depends(get_db), user=Depends(get_current_user)):
    service.lock_owner(db, user.id)
    row = service.owned(db, user.id, connection_id, lock=True)
    if busy(db, row):
        raise ApiError(409, "model_connection_busy", "运行中的任务正在使用此连接。")
    if dto(db, row).libraries:
        names = "、".join(item["name"] for item in dto(db, row).libraries)
        raise ApiError(409, "model_connection_bound", f"连接正被知识库使用，无法删除：{names}")
    if row.is_default:
        raise ApiError(409, "model_connection_default", "请先选择其他默认连接或恢复平台默认。")
    row.deleted_at = datetime.now(timezone.utc)
    db.commit()


class DefaultInput(BaseModel):
    connection_id: UUID | None


@router.put("/defaults/{kind}", status_code=204)
def set_default(
    kind: Literal["llm", "embedding"],
    body: DefaultInput,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    service.lock_owner(db, user.id)
    row = service.owned(db, user.id, body.connection_id, lock=True) if body.connection_id else None
    if row and (row.kind != kind or row.status != "ready"):
        raise ApiError(409, "model_test_required", "请选择类型匹配且已测试成功的连接。")
    db.execute(
        sa.update(ModelConnection)
        .where(ModelConnection.owner_user_id == user.id, ModelConnection.kind == kind)
        .values(is_default=False)
    )
    if row:
        row.is_default = True
    db.commit()


@router.post("/{connection_id}/test", response_model=ConnectionDTO)
def test(connection_id: UUID, db: Session = Depends(get_db), user=Depends(get_current_user)):
    service.lock_owner(db, user.id)
    row = service.owned(db, user.id, connection_id, lock=True)
    if (
        row.status == "testing"
        and row.tested_at
        and row.tested_at > datetime.now(timezone.utc) - timedelta(minutes=3)
    ):
        raise ApiError(409, "model_connection_busy", "连接正在测试。")
    row.status, row.error_code, row.tested_at = "testing", None, datetime.now(timezone.utc)
    started_at = row.tested_at
    saved = service.snapshot(row)
    db.commit()
    try:
        from study_platform.services.model_probe import probe_connection

        probe_connection(user.id, saved)
        error_code = None
    except ApiError as error:
        error_code = error.code
    with session_factory()() as publish:
        current = service.owned(publish, user.id, connection_id, lock=True)
        if current.revision != saved["revision"] or current.tested_at != started_at:
            raise ApiError(409, "model_connection_changed", "测试期间配置已更新，请重试。")
        current.status = "failed" if error_code else "ready"
        current.error_code = error_code
        current.tested_at = datetime.now(timezone.utc)
        result = dto(publish, current)
        publish.commit()
        return result
