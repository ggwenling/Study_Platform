"""Learning entry: generate quiz, image answers, verdict overrides, wrong book."""

from uuid import UUID

from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from study_platform.api.v1.auth import get_current_user
from study_platform.api.v1.materials import owned_kb
from study_platform.core.config import get_settings
from study_platform.core.errors import ApiError
from study_platform.persistence.database import get_db
from study_platform.persistence.models import User
from study_platform.services import learning

router = APIRouter(prefix="/learning", tags=["learning"])

_ALLOWED_IMAGE = {"image/png", "image/jpeg"}


class GenerateBody(BaseModel):
    count: int = Field(default=5, ge=1, le=20)


class AttemptBody(BaseModel):
    quiz_id: UUID


class VerdictBody(BaseModel):
    verdict: str = Field(pattern="^(correct|incorrect|pending)$")
    reference_answer: str | None = Field(default=None, max_length=8000)


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
    )
    db.commit()
    return {"quiz_id": str(quiz.id), "title": quiz.title, "item_count": body.count}


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


@router.post("/responses/{response_id}/answer-image")
async def answer_image(
    response_id: UUID,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if file.content_type not in _ALLOWED_IMAGE:
        raise ApiError(422, "invalid_type", "请上传 PNG 或 JPEG 图片")
    payload = await file.read()
    if len(payload) > get_settings().max_upload_bytes:
        raise ApiError(413, "too_large", "图片过大")
    row = learning.submit_image_answer(db, owner_user_id=user.id, response_id=response_id, image_bytes=payload)
    db.commit()
    return {
        "response_id": str(row.id),
        "verdict": row.verdict,
        "verdict_source": row.verdict_source,
        "confidence": float(row.confidence) if row.confidence is not None else None,
        "feedback": row.feedback,
        "answer": row.answer,
        "reference_answer": row.reference_answer_effective,
    }


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
    return {
        "response_id": str(row.id),
        "verdict": row.verdict,
        "verdict_source": row.verdict_source,
        "reference_answer": row.reference_answer_effective,
    }


@router.get("/wrong-questions")
def wrong_questions(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return {"items": learning.list_wrong_questions(db, user.id)}


@router.post("/wrong-questions/{wrong_id}/mastered")
def mastered_wrong(
    wrong_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    learning.mark_wrong_mastered(db, user.id, wrong_id)
    db.commit()
    return {"id": str(wrong_id), "status": "mastered"}