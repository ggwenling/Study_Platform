"""Learning sessions: quiz generation, image answers, verdicts, wrong-book."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import Session

from study_platform.core.config import get_settings
from study_platform.core.errors import ApiError
from study_platform.persistence.models import (
    KnowledgeBase,
    MaterialChunk,
    Quiz,
    QuizAttempt,
    QuizItem,
    QuizItemSource,
    QuizResponse,
    WrongQuestion,
)
from study_platform.services.chat import retrieve, visible_indices
from study_platform.services.learning_anchor import ensure_course_anchor
from study_platform.services.learning_grade import grade_text_answer, ocr_answer_bytes

_QUESTION_LINE = re.compile(r"^\s*(?:\d+[\.\)、．]|第\s*\d+\s*题)", re.M)


def _reference_from_answer_key(answer_key) -> str:
    if isinstance(answer_key, str):
        return answer_key
    if isinstance(answer_key, dict):
        for key in ("text", "value", "answer"):
            if key in answer_key:
                return str(answer_key[key])
        return json.dumps(answer_key, ensure_ascii=False)
    return str(answer_key)


def _answer_storage_path(response_id: UUID) -> Path:
    base = Path(get_settings().storage_dir).resolve() / "learning-answers" / str(response_id)
    base.mkdir(parents=True, exist_ok=True)
    return base / "answer.png"


def generate_learning_quiz(
    db: Session,
    *,
    owner_user_id: UUID,
    knowledge_base_id: UUID,
    count: int = 5,
) -> Quiz:
    kb = (
        db.execute(
            sa.select(KnowledgeBase).where(
                KnowledgeBase.id == knowledge_base_id,
                KnowledgeBase.owner_user_id == owner_user_id,
                KnowledgeBase.deleted_at.is_(None),
            )
        )
        .scalars()
        .first()
    )
    if kb is None:
        raise ApiError(404, "knowledge_base_not_found", "知识库不存在或无权访问")
    ensure_course_anchor(db, kb)
    scope_ids = visible_indices(db, owner_user_id, knowledge_base_id)
    if not scope_ids:
        raise ApiError(409, "library_not_ready", "当前知识库尚无可用索引，请先上传并完成处理。")
    scope = {"index_version_ids": scope_ids}
    hits = retrieve(scope, "重要概念、定义、公式与例题", owner_user_id)
    if not hits:
        raise ApiError(409, "no_chunks", "没有可用于出题的资料片段")
    quiz = Quiz(
        owner_user_id=owner_user_id,
        course_id=knowledge_base_id,
        title="练习",
        status="ready",
        difficulty=3,
        source_scope={"knowledge_base_id": str(knowledge_base_id)},
        generator_profile="learning_v1",
    )
    db.add(quiz)
    db.flush()
    ordinal = 0
    used_chunks: set[UUID] = set()
    for hit in hits:
        if ordinal >= count:
            break
        chunk_id = UUID(str(hit["id"]))
        if chunk_id in used_chunks:
            continue
        used_chunks.add(chunk_id)
        chunk = db.get(MaterialChunk, chunk_id)
        if chunk is None or not chunk.text.strip():
            continue
        excerpt = chunk.text.strip()
        stem, ref = _stem_from_chunk(excerpt)
        if not ref:
            continue
        item = QuizItem(
            quiz_id=quiz.id,
            ordinal=ordinal,
            item_type="short_answer",
            stem=stem,
            options=[],
            answer_key={"text": ref},
            rubric={},
            explanation=None,
            difficulty=3,
            max_score=Decimal("1"),
        )
        db.add(item)
        db.flush()
        db.add(
            QuizItemSource(
                quiz_item_id=item.id,
                chunk_id=chunk.id,
                material_version_id=chunk.material_version_id,
                index_version_id=chunk.index_version_id,
                locator=chunk.locator if isinstance(chunk.locator, dict) else {},
            )
        )
        ordinal += 1
    if ordinal == 0:
        raise ApiError(409, "no_questions", "无法从资料生成题目")
    db.flush()
    db.refresh(quiz)
    return quiz


def _stem_from_chunk(text: str) -> tuple[str, str]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    body = " ".join(lines)
    if len(body) < 20:
        return "", ""
    question_match = _QUESTION_LINE.search(body)
    if question_match:
        stem = body[question_match.start() :].split("？")[0].split("?")[0]
        if len(stem) > 400:
            stem = stem[:400] + "…"
        ref = _guess_answer(body)
        return stem + ("？" if not stem.endswith(("？", "?")) else ""), ref
    ref = body[:120].rstrip("。．.")
    stem = f"请根据资料作答：{ref[:80]}…" if len(ref) > 80 else f"请根据资料作答：{ref}"
    return stem, ref


def _guess_answer(body: str) -> str:
    for token in re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,20}", body):
        if len(token) >= 4:
            return token
    return body[:60]


def start_attempt(db: Session, *, owner_user_id: UUID, quiz_id: UUID) -> QuizAttempt:
    quiz = (
        db.execute(
            sa.select(Quiz).where(
                Quiz.id == quiz_id,
                Quiz.owner_user_id == owner_user_id,
                Quiz.deleted_at.is_(None),
            )
        )
        .scalars()
        .first()
    )
    if quiz is None:
        raise ApiError(404, "quiz_not_found", "练习不存在")
    items = list(
        db.execute(sa.select(QuizItem).where(QuizItem.quiz_id == quiz.id).order_by(QuizItem.ordinal))
        .scalars()
        .all()
    )
    if not items:
        raise ApiError(409, "quiz_empty", "练习中没有题目")
    now = datetime.now(UTC)
    attempt = QuizAttempt(
        owner_user_id=owner_user_id,
        quiz_id=quiz.id,
        status="in_progress",
        started_at=now,
        max_score=Decimal(len(items)),
        total_score=Decimal("0"),
    )
    db.add(attempt)
    db.flush()
    for item in items:
        db.add(
            QuizResponse(
                attempt_id=attempt.id,
                quiz_id=quiz.id,
                quiz_item_id=item.id,
                answer={"type": "pending"},
            )
        )
    db.flush()
    db.refresh(attempt)
    return attempt


def submit_image_answer(
    db: Session,
    *,
    owner_user_id: UUID,
    response_id: UUID,
    image_bytes: bytes,
) -> QuizResponse:
    row = (
        db.execute(
            sa.select(QuizResponse)
            .join(QuizAttempt, QuizAttempt.id == QuizResponse.attempt_id)
            .where(
                QuizResponse.id == response_id,
                QuizAttempt.owner_user_id == owner_user_id,
            )
        )
        .scalars()
        .first()
    )
    if row is None:
        raise ApiError(404, "response_not_found", "作答记录不存在")
    item = db.get(QuizItem, row.quiz_item_id)
    if item is None:
        raise ApiError(404, "item_not_found", "题目不存在")
    path = _answer_storage_path(row.id)
    path.write_bytes(image_bytes)
    ocr_text, ocr_conf = ocr_answer_bytes(image_bytes)
    reference = row.reference_answer_effective or _reference_from_answer_key(item.answer_key)
    verdict, conf, feedback = grade_text_answer(ocr_text, reference, ocr_conf)
    row.answer = {
        "type": "image",
        "storage_key": str(path.relative_to(Path(get_settings().storage_dir).resolve())),
        "ocr_text": ocr_text,
        "ocr_confidence": ocr_conf,
    }
    row.reference_answer_effective = reference
    row.confidence = Decimal(str(round(conf, 4)))
    row.verdict = verdict
    row.verdict_source = "auto"
    row.is_correct = True if verdict == "correct" else False if verdict == "incorrect" else None
    row.feedback = feedback
    row.grader_profile = "learning_stub_v1"
    row.graded_at = datetime.now(UTC)
    db.flush()
    _sync_wrong_book(db, owner_user_id, item.id, row)
    db.flush()
    db.refresh(row)
    return row


def override_verdict(
    db: Session,
    *,
    owner_user_id: UUID,
    response_id: UUID,
    verdict: str,
    reference_answer: str | None,
) -> QuizResponse:
    if verdict not in {"correct", "incorrect", "pending"}:
        raise ApiError(400, "invalid_verdict", "判题结果无效")
    row = (
        db.execute(
            sa.select(QuizResponse)
            .join(QuizAttempt, QuizAttempt.id == QuizResponse.attempt_id)
            .where(
                QuizResponse.id == response_id,
                QuizAttempt.owner_user_id == owner_user_id,
            )
        )
        .scalars()
        .first()
    )
    if row is None:
        raise ApiError(404, "response_not_found", "作答记录不存在")
    if reference_answer is not None and reference_answer.strip():
        row.reference_answer_effective = reference_answer.strip()
        item = db.get(QuizItem, row.quiz_item_id)
        if item is not None:
            item.answer_key = {"text": reference_answer.strip()}
    row.verdict = verdict
    row.verdict_source = "user"
    row.is_correct = True if verdict == "correct" else False if verdict == "incorrect" else None
    row.graded_at = datetime.now(UTC)
    db.flush()
    item = db.get(QuizItem, row.quiz_item_id)
    if item is not None:
        _sync_wrong_book(db, owner_user_id, item.id, row)
    db.flush()
    db.refresh(row)
    return row


def _sync_wrong_book(db: Session, owner_user_id: UUID, item_id: UUID, response: QuizResponse) -> None:
    existing = (
        db.execute(
            sa.select(WrongQuestion).where(
                WrongQuestion.owner_user_id == owner_user_id,
                WrongQuestion.quiz_item_id == item_id,
            )
        )
        .scalars()
        .first()
    )
    now = datetime.now(UTC)
    if response.verdict == "incorrect":
        if existing is None:
            db.add(
                WrongQuestion(
                    owner_user_id=owner_user_id,
                    quiz_item_id=item_id,
                    last_response_id=response.id,
                    wrong_count=1,
                    status="open",
                    last_wrong_at=now,
                )
            )
        else:
            existing.wrong_count += 1
            existing.last_response_id = response.id
            existing.last_wrong_at = now
            existing.status = "open"
    elif response.verdict == "correct" and existing is not None:
        existing.status = "reviewing"


def list_wrong_questions(db: Session, owner_user_id: UUID) -> list[dict]:
    rows = (
        db.execute(
            sa.select(WrongQuestion, QuizItem)
            .join(QuizItem, QuizItem.id == WrongQuestion.quiz_item_id)
            .where(
                WrongQuestion.owner_user_id == owner_user_id,
                WrongQuestion.status != "mastered",
            )
            .order_by(WrongQuestion.last_wrong_at.desc())
        )
        .all()
    )
    out = []
    for wrong, item in rows:
        out.append(
            {
                "id": str(wrong.id),
                "quiz_item_id": str(item.id),
                "stem": item.stem,
                "status": wrong.status,
                "wrong_count": wrong.wrong_count,
                "last_wrong_at": wrong.last_wrong_at.isoformat(),
            }
        )
    return out


def mark_wrong_mastered(db: Session, owner_user_id: UUID, wrong_id: UUID) -> None:
    row = (
        db.execute(
            sa.select(WrongQuestion).where(
                WrongQuestion.id == wrong_id,
                WrongQuestion.owner_user_id == owner_user_id,
            )
        )
        .scalars()
        .first()
    )
    if row is None:
        raise ApiError(404, "wrong_not_found", "错题记录不存在")
    row.status = "mastered"
    db.flush()


def attempt_payload(db: Session, attempt_id: UUID, owner_user_id: UUID) -> dict:
    attempt = (
        db.execute(
            sa.select(QuizAttempt).where(
                QuizAttempt.id == attempt_id,
                QuizAttempt.owner_user_id == owner_user_id,
            )
        )
        .scalars()
        .first()
    )
    if attempt is None:
        raise ApiError(404, "attempt_not_found", "练习会话不存在")
    quiz = db.get(Quiz, attempt.quiz_id)
    responses = list(
        db.execute(
            sa.select(QuizResponse, QuizItem)
            .join(QuizItem, QuizItem.id == QuizResponse.quiz_item_id)
            .where(QuizResponse.attempt_id == attempt.id)
            .order_by(QuizItem.ordinal)
        ).all()
    )
    items = []
    for resp, item in responses:
        ref = resp.reference_answer_effective or _reference_from_answer_key(item.answer_key)
        items.append(
            {
                "response_id": str(resp.id),
                "quiz_item_id": str(item.id),
                "ordinal": item.ordinal,
                "stem": item.stem,
                "item_type": item.item_type,
                "reference_answer": ref,
                "verdict": resp.verdict,
                "verdict_source": resp.verdict_source,
                "confidence": float(resp.confidence) if resp.confidence is not None else None,
                "feedback": resp.feedback,
                "answer": resp.answer,
            }
        )
    return {
        "id": str(attempt.id),
        "quiz_id": str(attempt.quiz_id),
        "knowledge_base_id": str(quiz.course_id) if quiz else None,
        "status": attempt.status,
        "items": items,
    }