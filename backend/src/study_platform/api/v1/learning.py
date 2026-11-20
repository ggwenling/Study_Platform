"""Learning entry: generate quiz, text/image answers, verdict overrides, history & wrong book."""

from pathlib import Path
from typing import Literal
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Depends, File, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from study_platform.api.v1.auth import get_current_user
from study_platform.api.v1.materials import owned_kb
from study_platform.core.config import get_settings
from study_platform.core.errors import ApiError
from study_platform.persistence.database import get_db
from study_platform.persistence.models import KnowledgeBase, QuizItem, User
from study_platform.services import learning

router = APIRouter(prefix="/learning", tags=["learning"])

_ALLOWED_IMAGE = {"image/png", "image/jpeg"}


class GenerateBody(BaseModel):
    count: int = Field(default=5, ge=1, le=20)
    mode: Literal["short_answer", "mcq", "mixed"] = "short_answer"
    material_ids: list[UUID] | None = Field(default=None, max_length=50)


class AttemptBody(BaseModel):
    quiz_id: UUID


class AnswerTextBody(BaseModel):
    answer_text: str = Field(min_length=1, max_length=4000)


class VerdictBody(BaseModel):
    verdict: str = Field(pattern="^(correct|incorrect|pending)$")
    reference_answer: str | None = Field(default=None, max_length=8000)


class RetryWrongBody(BaseModel):
    wrong_ids: list[UUID] | None = Field(default=None, max_length=20)
    knowledge_base_id: UUID | None = None
    due_only: bool = False


def _response_payload(db: Session, row) -> dict:
    item = (
        db.get(QuizItem, row.quiz_item_id)
        if isinstance(db, Session) and hasattr(row, "quiz_item_id")
        else None
    )
    sources = learning._load_sources_by_item(db, [item.id]) if item else {}
    ref = getattr(row, "reference_answer_effective", None)
    if not ref and item is not None and learning._reference_revealed(row):
        ref = learning._reference_from_answer_key(item.answer_key)
    return {
        "response_id": str(row.id),
        "verdict": row.verdict,
        "verdict_source": getattr(row, "verdict_source", None),
        "confidence": float(row.confidence) if getattr(row, "confidence", None) is not None else None,
        "feedback": getattr(row, "feedback", None),
        "answer": getattr(row, "answer", None),
        "reference_answer": ref,
        "explanation": item.explanation if item else None,
        "sources": sources.get(item.id, []) if item else [],
    }


@router.post("/knowledge-bases/{kb_id}/quizzes/generate")
def generate_quiz(
    kb_id: UUID,
    body: GenerateBody,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owned_kb(db, user, kb_id)
    get_settings()
    quiz = learning.generate_learning_quiz(
        db,
        owner_user_id=user.id,
        knowledge_base_id=kb_id,
        count=body.count,
        mode=body.mode,
        material_ids=body.material_ids,
    )
    db.flush()
    item_count = db.scalar(
        sa.select(sa.func.count()).select_from(QuizItem).where(QuizItem.quiz_id == quiz.id)
    )
    db.commit()
    return {"quiz_id": str(quiz.id), "title": quiz.title, "item_count": int(item_count or 0)}


@router.get("/attempts")
def list_attempts(
    knowledge_base_id: UUID | None = None,
    limit: int = Query(default=20, ge=1, le=50),
    cursor: UUID | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if knowledge_base_id is not None:
        owned_kb(db, user, knowledge_base_id)
    return learning.list_attempts_page(
        db,
        user.id,
        knowledge_base_id=knowledge_base_id,
        limit=limit,
        cursor=cursor,
    )


@router.post("/attempts")
def create_attempt(
    body: AttemptBody,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    attempt = learning.start_attempt(db, owner_user_id=user.id, quiz_id=body.quiz_id)
    db.commit()
    return learning.attempt_payload(db, attempt.id, user.id)


@router.get("/attempts/{attempt_id}")
def get_attempt(
    attempt_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return learning.attempt_payload(db, attempt_id, user.id)


@router.post("/responses/{response_id}/answer-text")
def answer_text(
    response_id: UUID,
    body: AnswerTextBody,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = learning.submit_text_answer(
        db, owner_user_id=user.id, response_id=response_id, answer_text=body.answer_text
    )
    db.commit()
    previous = getattr(row, "_previous_image_path", None)
    if previous:
        try:
            previous.unlink(missing_ok=True)
        except OSError:
            pass
    return _response_payload(db, row)


@router.post("/responses/{response_id}/answer-image")
def answer_image(
    response_id: UUID,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if file.content_type not in _ALLOWED_IMAGE:
        raise ApiError(422, "invalid_type", "请上传 PNG 或 JPEG 图片")
    payload = file.file.read(get_settings().max_upload_bytes + 1)
    if len(payload) > get_settings().max_upload_bytes:
        raise ApiError(413, "too_large", "图片过大")
    row = learning.submit_image_answer(
        db, owner_user_id=user.id, response_id=response_id, image_bytes=payload
    )
    path = Path(get_settings().storage_dir).resolve() / row.answer["storage_key"]
    try:
        db.commit()
    except Exception:
        db.rollback()
        path.unlink(missing_ok=True)
        raise
    previous = getattr(row, "_previous_image_path", None)
    if previous and previous != path:
        try:
            previous.unlink(missing_ok=True)
        except OSError:
            pass
    return _response_payload(db, row)


@router.patch("/responses/{response_id}/verdict")
def patch_verdict(
    response_id: UUID,
    body: VerdictBody,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = learning.override_verdict(
        db,
        owner_user_id=user.id,
        response_id=response_id,
        verdict=body.verdict,
        reference_answer=body.reference_answer,
    )
    db.commit()
    return _response_payload(db, row)


@router.get("/wrong-questions")
def wrong_questions(
    knowledge_base_id: UUID | None = None,
    due_only: bool = False,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if knowledge_base_id is not None:
        owned_kb(db, user, knowledge_base_id)
    return {
        "items": learning.list_wrong_questions(
            db, user.id, knowledge_base_id=knowledge_base_id, due_only=due_only
        )
    }


@router.post("/wrong-questions/retry")
def retry_wrong(
    body: RetryWrongBody,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if body.knowledge_base_id is not None:
        kb_exists = db.scalar(
            sa.select(KnowledgeBase.id).where(
                KnowledgeBase.id == body.knowledge_base_id,
                KnowledgeBase.owner_user_id == user.id,
            )
        )
        if kb_exists is None:
            raise ApiError(404, "not_found", "知识库不存在")
    attempt = learning.retry_wrong_questions(
        db,
        owner_user_id=user.id,
        wrong_ids=body.wrong_ids,
        knowledge_base_id=body.knowledge_base_id,
        due_only=body.due_only,
    )
    db.commit()
    return learning.attempt_payload(db, attempt.id, user.id)


@router.post("/wrong-questions/{wrong_id}/mastered")
def mastered_wrong(
    wrong_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    learning.mark_wrong_mastered(db, user.id, wrong_id)
    db.commit()
    return {"id": str(wrong_id), "status": "mastered"}
