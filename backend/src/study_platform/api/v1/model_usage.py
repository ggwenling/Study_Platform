"""Current-user model usage summary."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from study_platform.api.v1.auth import get_current_user
from study_platform.persistence.database import get_db
from study_platform.persistence.models import User
from study_platform.services.model_usage import summarize_usage

router = APIRouter()


class UsageBucketDTO(BaseModel):
    key: str
    calls: int
    successes: int
    failures: int
    fallback_calls: int
    total_tokens: int


class UsageTotalsDTO(BaseModel):
    calls: int
    successes: int
    failures: int
    fallback_calls: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ModelUsageSummaryDTO(BaseModel):
    days: int
    since: str
    totals: UsageTotalsDTO
    by_day: list[UsageBucketDTO]
    by_purpose: list[UsageBucketDTO]
    by_model: list[UsageBucketDTO]


@router.get("/me/model-usage", response_model=ModelUsageSummaryDTO)
def my_model_usage(
    days: int = Query(default=30, ge=1, le=365),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ModelUsageSummaryDTO:
    payload = summarize_usage(db, UUID(str(user.id)), days=days)
    return ModelUsageSummaryDTO.model_validate(payload)