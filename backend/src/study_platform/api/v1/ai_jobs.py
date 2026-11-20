"""Owner-scoped task progress and cancellation."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from study_platform.api.v1.auth import get_current_user
from study_platform.persistence.database import get_db
from study_platform.services import ai_jobs

router = APIRouter(prefix="/ai-jobs", tags=["AI jobs"])
GenerationType = Literal["notes", "flashcards", "quiz"]
JobType = Literal["notes", "flashcards", "quiz", "ingest", "classification", "reindex"]
JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]


class SubmittedJob(BaseModel):
    job_id: UUID
    job_type: GenerationType
    status: Literal["queued"]
    knowledge_base_id: UUID
    title: str


class AiJobDTO(BaseModel):
    id: UUID
    job_type: JobType
    knowledge_base_id: UUID | None
    status: JobStatus
    phase: str
    progress: int
    title: str
    result_resource_type: str | None
    result_resource_id: UUID | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


@router.get("", response_model=list[AiJobDTO])
def list_jobs(knowledge_base_id: UUID | None = None, job_types: list[JobType] | None = Query(None),
              status: Literal["active", "all"] = "active", limit: int = Query(20, ge=1, le=100),
              db: Session = Depends(get_db), user=Depends(get_current_user)):
    return ai_jobs.list_jobs(db, user.id, knowledge_base_id, job_types, status == "active", limit)


@router.get("/{job_id}", response_model=AiJobDTO)
def get_job(job_id: UUID, db: Session = Depends(get_db), user=Depends(get_current_user)):
    return ai_jobs.get(db, user.id, job_id)


@router.post("/{job_id}/cancel", response_model=AiJobDTO)
def cancel_job(job_id: UUID, db: Session = Depends(get_db), user=Depends(get_current_user)):
    return ai_jobs.cancel(db, user, job_id)
