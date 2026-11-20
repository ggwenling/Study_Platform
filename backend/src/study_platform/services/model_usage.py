"""Best-effort persistence of model call metrics; failures never block callers."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import Session

from study_platform.persistence.database import session_factory
from study_platform.persistence.models.model_connections import ModelConnection
from study_platform.persistence.models.model_usage import ModelCallRecord

logger = logging.getLogger(__name__)

PURPOSES = frozenset(
    {
        "chat",
        "learning_quiz",
        "learning_grade",
        "query_rewrite",
        "embedding",
        "classification",
        "connection_test",
        "vision_ingest",
        "vision_grade",
        "rerank",
    }
)


@dataclass(frozen=True)
class ModelUsageContext:
    owner_user_id: UUID
    purpose: str
    connection_source: str = "platform"

    def __post_init__(self) -> None:
        if self.purpose not in PURPOSES:
            raise ValueError(f"Unknown model usage purpose: {self.purpose}")


def _normalize_usage(usage: dict[str, Any] | None) -> dict[str, Any] | None:
    if not usage:
        return None
    out: dict[str, Any] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens", "input_tokens", "output_tokens"):
        value = usage.get(key)
        if isinstance(value, (int, float)) and value >= 0:
            out[key] = int(value)
    error_code = usage.get("error_code")
    if isinstance(error_code, str):
        out["error_code"] = error_code[:80]
    return out or None


def record_model_call(
    context: ModelUsageContext | None,
    *,
    model_name: str,
    used_fallback: bool,
    elapsed_ms: float | None,
    success: bool,
    usage: dict[str, Any] | None = None,
) -> None:
    if context is None:
        return
    try:
        with session_factory()() as db:
            db.add(
                ModelCallRecord(
                    owner_user_id=context.owner_user_id,
                    purpose=context.purpose,
                    connection_source=context.connection_source,
                    model_name=model_name[:120],
                    used_fallback=used_fallback,
                    usage=_normalize_usage(usage),
                    elapsed_ms=Decimal(str(round(elapsed_ms, 3))) if elapsed_ms is not None else None,
                    success=success,
                )
            )
            db.commit()
    except Exception:
        logger.warning("model usage record failed", exc_info=True)


def _token_sum(usage: dict | None, *keys: str) -> int:
    if not usage:
        return 0
    for key in keys:
        value = usage.get(key)
        if isinstance(value, (int, float)):
            return int(value)
    return 0


def summarize_usage(db: Session, owner_user_id: UUID, *, days: int) -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(days=days)
    rows = db.scalars(
        sa.select(ModelCallRecord).where(
            ModelCallRecord.owner_user_id == owner_user_id,
            ModelCallRecord.created_at >= since,
        )
    ).all()
    totals = {
        "calls": 0,
        "successes": 0,
        "failures": 0,
        "fallback_calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }
    by_day: dict[str, dict[str, int]] = {}
    by_purpose: dict[str, dict[str, int]] = {}
    by_model: dict[str, dict[str, int]] = {}
    by_connection: dict[str, dict[str, int]] = {}

    def bucket(store: dict[str, dict[str, int]], key: str, row: ModelCallRecord) -> None:
        item = store.setdefault(
            key,
            {
                "calls": 0,
                "successes": 0,
                "failures": 0,
                "fallback_calls": 0,
                "total_tokens": 0,
            },
        )
        item["calls"] += 1
        if row.success:
            item["successes"] += 1
        else:
            item["failures"] += 1
        if row.used_fallback:
            item["fallback_calls"] += 1
        item["total_tokens"] += _token_sum(row.usage, "total_tokens")

    for row in rows:
        totals["calls"] += 1
        totals["successes"] += 1 if row.success else 0
        totals["failures"] += 0 if row.success else 1
        totals["fallback_calls"] += 1 if row.used_fallback else 0
        totals["prompt_tokens"] += _token_sum(row.usage, "prompt_tokens", "input_tokens")
        totals["completion_tokens"] += _token_sum(row.usage, "completion_tokens", "output_tokens")
        totals["total_tokens"] += _token_sum(row.usage, "total_tokens")
        day = row.created_at.astimezone(UTC).date().isoformat()
        bucket(by_day, day, row)
        bucket(by_purpose, row.purpose, row)
        bucket(by_model, row.model_name, row)
        bucket(by_connection, f"{row.connection_source}|{row.model_name}", row)

    def sort_keys(store: dict[str, dict[str, int]]) -> list[dict[str, Any]]:
        return [
            {"key": key, **store[key]}
            for key in sorted(store.keys(), reverse=True)
        ]

    names = dict(db.execute(sa.select(ModelConnection.id, ModelConnection.name).where(
        ModelConnection.owner_user_id == owner_user_id)).all())
    connections = sort_keys(by_connection)
    for item in connections:
        source, model = item["key"].split("|", 1)
        parts = source.split(":")
        label = "平台默认" if source == "platform" else f"个人 · {names.get(UUID(parts[1]), '已删除连接')} · v{parts[2]}"
        item["label"] = f"{label} · {model}"
    return {
        "days": days,
        "since": since.isoformat(),
        "totals": totals,
        "by_day": sort_keys(by_day),
        "by_purpose": sort_keys(by_purpose),
        "by_model": sort_keys(by_model),
        "by_connection": connections,
    }
