"""Learning sessions: quiz generation, text/MCQ/image answers, verdicts, wrong-book & history."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.orm import Session

from study_platform.core.config import get_settings
from study_platform.core.errors import ApiError
from study_platform.persistence.models import (
    IndexVersion,
    KnowledgeBase,
    Material,
    MaterialChunk,
    MaterialVersion,
    Quiz,
    QuizAttempt,
    QuizItem,
    QuizItemSource,
    QuizResponse,
    WrongQuestion,
)
from study_platform.services import model_connections
from study_platform.services.chat import chunk_dto, chunk_query, retrieve, visible_indices
from study_platform.services.learning_anchor import ensure_course_anchor
from study_platform.services.learning_grade import (
    _apply_threshold,
    grade_text_answer,
    normalize_answer,
    ocr_answer_bytes,
)
from study_platform.services.learning_llm import (
    fragment_excerpt,
    generate_questions_with_llm,
    index_to_fragment,
    map_fragment_hits,
    resolve_mcq_choice,
)
from study_platform.services.material_reader import locator_label, reader_locator
from study_platform.services.model_connections import llm_available
from study_platform.services.model_task import learning_task
from study_platform.services.vision_pipeline import grade_vision_image

_QUESTION_LINE = re.compile(r"^\s*(?:\d+[\.\)、．]|第\s*\d+\s*题)", re.M)
_OPTION_LETTER = re.compile(r"^\s*([A-Fa-f])(?:[\.\)、:：\s]|$)")
_QUIZ_RETRIEVAL_QUERY = (
    "重要概念、定义、公式、规则与例题; key concepts, definitions, rules, formulas, examples"
)


def _sample_scoped_chunks(
    db: Session,
    scope_ids: list[str],
    owner_user_id: UUID,
    limit: int,
    *,
    exclude_ids: set[UUID] | None = None,
    max_chars: int = 16000,
) -> list[dict]:
    indices = [UUID(x) if isinstance(x, str) else x for x in scope_ids]
    document_rank = sa.func.row_number().over(
        partition_by=Material.id,
        order_by=(MaterialChunk.ordinal, MaterialChunk.id),
    )
    query = (
        chunk_query(indices, owner_user_id)
        .where(MaterialChunk.text.regexp_match("[^[:space:]]"))
        .order_by(document_rank, Material.id)
        .limit(limit)
    )
    if exclude_ids:
        query = query.where(MaterialChunk.id.not_in(exclude_ids))
    rows = db.execute(query).all()
    sampled: list[dict] = []
    total_chars = 0
    for row, title in rows:
        if total_chars + len(row.text) > max_chars and sampled:
            break
        sampled.append(chunk_dto(row, title))
        total_chars += len(row.text)
    return sampled


def _collect_quiz_hits(
    db: Session,
    scope: dict,
    scope_ids: list[str],
    owner_user_id: UUID,
    count: int,
    *,
    stub_mode: bool,
) -> list[dict]:
    target_limit = max(count * 3, 12)
    if stub_mode:
        indices = [UUID(x) if isinstance(x, str) else x for x in scope_ids]
        rows = db.execute(
            chunk_query(indices, owner_user_id)
            .order_by(MaterialChunk.ordinal)
            .limit(target_limit)
        ).all()
        return [chunk_dto(row, title) for row, title in rows]
    retrieved = retrieve(scope, _QUIZ_RETRIEVAL_QUERY, owner_user_id)
    hits: list[dict] = []
    seen_ids: set[UUID] = set()
    for item in retrieved:
        if not (item.get("text") or "").strip():
            continue
        chunk_id = item.get("id")
        if isinstance(chunk_id, str):
            chunk_id = UUID(chunk_id)
        if chunk_id in seen_ids:
            continue
        if isinstance(chunk_id, UUID):
            seen_ids.add(chunk_id)
        hits.append(item)
    min_hits = min(max(count * 2, 6), target_limit)
    if len(hits) < min_hits:
        extra = _sample_scoped_chunks(
            db,
            scope_ids,
            owner_user_id,
            target_limit - len(hits),
            exclude_ids=seen_ids,
        )
        hits.extend(extra)
    return hits


def _reference_from_answer_key(answer_key) -> str:
    if isinstance(answer_key, str):
        return answer_key
    if isinstance(answer_key, dict):
        for key in ("text", "choice", "value", "answer"):
            if key in answer_key and answer_key[key]:
                return str(answer_key[key])
        return json.dumps(answer_key, ensure_ascii=False)
    return str(answer_key)


def _extract_choice_letter(text: str) -> str | None:
    match = _OPTION_LETTER.match(text or "")
    return match.group(1).upper() if match else None


def _load_sources_by_item(
    db: Session, item_ids: list[UUID], *, include_quotes: set[UUID] | None = None
) -> dict[UUID, list[dict]]:
    if not isinstance(db, Session) or not item_ids:
        return {}
    rows = db.execute(
        sa.select(QuizItemSource, MaterialChunk, MaterialVersion, Material, KnowledgeBase, IndexVersion)
        .join(MaterialChunk, MaterialChunk.id == QuizItemSource.chunk_id)
        .join(MaterialVersion, MaterialVersion.id == QuizItemSource.material_version_id)
        .join(Material, Material.id == MaterialVersion.material_id)
        .join(KnowledgeBase, KnowledgeBase.id == Material.knowledge_base_id)
        .outerjoin(IndexVersion, IndexVersion.id == QuizItemSource.index_version_id)
        .where(QuizItemSource.quiz_item_id.in_(item_ids))
        .order_by(QuizItemSource.created_at)
    ).all()
    grouped: dict[UUID, list[dict]] = {item_id: [] for item_id in item_ids}
    for src, chunk, version, material, kb, index in rows:
        available = (
            kb.deleted_at is None
            and material.deleted_at is None
            and material.status != "deleted"
            and index is not None
            and index.status in ("ready", "retired")
        )
        loc = reader_locator(src.locator or {}) if available else {}
        revealed = include_quotes is None or src.quiz_item_id in include_quotes
        reveal_quote = available and revealed
        quote_status = ("verified" if src.quote_text is not None else "unverified") if reveal_quote else (
            "unavailable" if revealed and not available else None
        )
        grouped.setdefault(src.quiz_item_id, []).append(
            {
                "chunk_id": str(chunk.id),
                "material_id": str(material.id),
                "material_version_id": str(version.id),
                "knowledge_base_id": str(kb.id),
                "locator": loc,
                "source_url": material.source_url if available else None,
                "title": material.title,
                "location_label": locator_label(loc) if available else "来源已不可用",
                "availability": "available" if available else "unavailable",
                "quote": (src.quote_text if src.quote_text is not None else chunk.text) if reveal_quote else None,
                "quote_status": quote_status,
            }
        )
    return grouped


def _answer_storage_path(response_id: UUID) -> Path:
    base = Path(get_settings().storage_dir).resolve() / "learning-answers" / str(response_id)
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{uuid4().hex}.image"


def _stub_mcq_options(target_ref: str, all_excerpts: list[str], ordinal: int) -> tuple[list[str], str]:
    clean_ref = target_ref[:60].strip()
    distractors: list[str] = []
    for idx, text in enumerate(all_excerpts):
        candidate = " ".join(ln.strip() for ln in text.splitlines() if ln.strip())[:60].strip()
        if candidate and candidate != clean_ref and candidate not in distractors:
            distractors.append(candidate)
        if len(distractors) >= 3:
            break
    fallbacks = ["与原资料所述结论相反", "资料中未提及该条件", "仅在特定边界条件下成立"]
    for fb in fallbacks:
        if len(distractors) >= 3:
            break
        if fb not in distractors and fb != clean_ref:
            distractors.append(fb)
    correct_pos = ordinal % 4
    raw_Pool = distractors[:3]
    raw_Pool.insert(correct_pos, clean_ref)
    letters = ("A", "B", "C", "D")
    options = [f"{letters[i]}. {raw_Pool[i]}" for i in range(4)]
    return options, letters[correct_pos]


@learning_task
def generate_learning_quiz(
    db: Session,
    *,
    owner_user_id: UUID,
    knowledge_base_id: UUID,
    count: int = 5,
    mode: str = "short_answer",
    material_ids: list[UUID] | None = None,
    index_ids: list[str] | None = None,
    before_publish: Callable[[], object] | None = None,
) -> Quiz:
    if mode not in ("short_answer", "mcq", "mixed"):
        raise ApiError(422, "invalid_mode", "不支持的题型模式")
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
    scope_ids = index_ids if index_ids is not None else visible_indices(db, owner_user_id, knowledge_base_id, material_ids)
    if not scope_ids:
        raise ApiError(409, "library_not_ready", "当前知识库尚无可用索引，请先上传并完成处理。")
    scope = {"index_version_ids": scope_ids}
    settings = get_settings()
    hits = _collect_quiz_hits(
        db, scope, scope_ids, owner_user_id, count, stub_mode=settings.learning_stub_grader
    )
    if not hits:
        raise ApiError(409, "no_chunks", "没有可用于出题的资料片段")
    if before_publish:
        db.commit()
    use_llm = not settings.learning_stub_grader and llm_available(owner_user_id)
    if use_llm:
        fragments = map_fragment_hits(hits)
        if not fragments:
            raise ApiError(409, "no_chunks", "没有可用于出题的资料片段")
        generated, insufficient = generate_questions_with_llm(
            fragments, count, mode=mode, owner_user_id=owner_user_id
        )
        if insufficient:
            raise ApiError(409, "insufficient_material", insufficient)
        by_index = index_to_fragment(fragments)
    mode_label = {"mcq": "单选练习", "mixed": "混合练习"}.get(mode, "简答练习")
    scope_meta: dict = {"knowledge_base_id": str(knowledge_base_id), "mode": mode}
    if material_ids:
        scope_meta["material_ids"] = [str(mid) for mid in material_ids]
    if before_publish:
        before_publish()
    ensure_course_anchor(db, kb)
    quiz = Quiz(
        owner_user_id=owner_user_id,
        course_id=knowledge_base_id,
        title=f"{kb.name} · {mode_label}",
        status="ready",
        difficulty=3,
        source_scope=scope_meta,
        generator_profile="learning_llm_v2" if use_llm else "learning_stub_v2",
    )
    db.add(quiz)
    db.flush()
    ordinal = 0
    if use_llm:
        for question in generated:
            if ordinal >= count:
                break
            if question.item_type not in ("mcq", "short_answer") or (
                mode != "mixed" and question.item_type != mode
            ):
                continue
            source_refs = [by_index[idx] for idx in question.source_indices if idx in by_index]
            if not source_refs:
                continue
            item_type = question.item_type
            options_list = list(question.options) if item_type == "mcq" else []
            choice = (
                resolve_mcq_choice(question.reference_answer, options_list)
                if item_type == "mcq"
                else None
            )
            answer_key = (
                {"choice": choice, "text": question.reference_answer}
                if choice
                else {"text": question.reference_answer}
            )
            item = QuizItem(
                quiz_id=quiz.id,
                ordinal=ordinal,
                item_type=item_type,
                stem=question.stem,
                options=options_list,
                answer_key=answer_key,
                rubric={},
                explanation=question.explanation,
                difficulty=3,
                max_score=Decimal("1"),
            )
            db.add(item)
            db.flush()
            for ref in source_refs:
                db.add(
                    QuizItemSource(
                        quiz_item_id=item.id,
                        chunk_id=ref.chunk_id,
                        material_version_id=ref.material_version_id,
                        index_version_id=ref.index_version_id,
                        locator=ref.locator,
                        quote_text=fragment_excerpt(ref.text),
                    )
                )
            ordinal += 1
    else:
        used_chunks: set[UUID] = set()
        all_texts = [str(h.get("text", "")) for h in hits if h.get("text")]
        for hit in hits:
            if ordinal >= count:
                break
            chunk_id = UUID(str(hit["id"]))
            if chunk_id in used_chunks:
                continue
            used_chunks.add(chunk_id)
            excerpt = hit["text"].strip()
            if not excerpt:
                continue
            stem, ref = _stem_from_chunk(excerpt)
            if not ref:
                continue
            make_mcq = mode == "mcq" or (mode == "mixed" and ordinal % 2 == 0)
            if make_mcq:
                options_list, letter = _stub_mcq_options(ref, all_texts, ordinal)
                item_type = "mcq"
                mcq_stem = f"根据资料，下列哪一项表述正确？（围绕：{stem[:60]}）"
                answer_key = {"choice": letter, "text": f"{letter}. {ref[:60].strip()}"}
                explanation = f"依据原资料片段：{ref[:100]}"
            else:
                options_list = []
                item_type = "short_answer"
                mcq_stem = stem
                answer_key = {"text": ref}
                explanation = None
            item = QuizItem(
                quiz_id=quiz.id,
                ordinal=ordinal,
                item_type=item_type,
                stem=mcq_stem,
                options=options_list,
                answer_key=answer_key,
                rubric={},
                explanation=explanation,
                difficulty=3,
                max_score=Decimal("1"),
            )
            db.add(item)
            db.flush()
            db.add(
                QuizItemSource(
                    quiz_item_id=item.id,
                    chunk_id=chunk_id,
                    material_version_id=hit["material_version_id"],
                    index_version_id=hit["index_version_id"],
                    locator=hit["locator"] if isinstance(hit["locator"], dict) else {},
                    quote_text=excerpt,
                )
            )
            ordinal += 1
    if ordinal == 0:
        raise ApiError(
            409,
            "no_questions",
            "无法从资料生成题目，请补充资料后重试。",
        )
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


def start_attempt(db: Session, *, owner_user_id: UUID, quiz_id: UUID, resume_existing: bool = False) -> QuizAttempt:
    query = sa.select(Quiz).where(
        Quiz.id == quiz_id, Quiz.owner_user_id == owner_user_id, Quiz.deleted_at.is_(None),
    )
    quiz = db.scalar(query.with_for_update() if resume_existing else query)
    if quiz is None:
        raise ApiError(404, "quiz_not_found", "练习不存在")
    if resume_existing:
        existing = db.scalar(sa.select(QuizAttempt).where(
            QuizAttempt.quiz_id == quiz.id, QuizAttempt.owner_user_id == owner_user_id,
        ).order_by(QuizAttempt.created_at.desc(), QuizAttempt.id.desc()).limit(1))
        if existing is not None:
            return existing
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


def _refresh_attempt_progress(db: Session, attempt_id: UUID) -> None:
    attempt = db.get(QuizAttempt, attempt_id)
    if attempt is None:
        return
    responses = list(
        db.scalars(sa.select(QuizResponse).where(QuizResponse.attempt_id == attempt_id)).all()
    )
    if not responses:
        return
    correct = sum(1 for r in responses if r.verdict == "correct")
    attempt.total_score = Decimal(correct)
    now = datetime.now(UTC)
    if all(r.verdict in ("correct", "incorrect") for r in responses):
        attempt.status = "graded"
        attempt.submitted_at = attempt.submitted_at or now
        attempt.graded_at = now
    else:
        attempt.status = "in_progress"


def _previous_answer_image_path(row: QuizResponse) -> Path | None:
    previous = row.answer.get("storage_key") if isinstance(row.answer, dict) else None
    if not isinstance(previous, str) or not previous:
        return None
    base = Path(get_settings().storage_dir).resolve()
    previous_dir = (base / "learning-answers" / str(row.id)).resolve()
    previous_path = (base / previous).resolve()
    return previous_path if previous_path.parent == previous_dir else None


def _grade_mcq_choice(item: QuizItem, student_text: str) -> tuple[str, float, str, str, str]:
    answer_key = item.answer_key if isinstance(item.answer_key, dict) else {"text": str(item.answer_key)}
    raw_options = list(item.options or [])
    expected_choice = resolve_mcq_choice(str(answer_key.get("choice") or "")) or resolve_mcq_choice(
        str(answer_key.get("text") or ""), raw_options
    )
    reference = _reference_from_answer_key(item.answer_key)
    student_choice = resolve_mcq_choice(student_text, raw_options)
    matched = False
    if expected_choice and student_choice:
        matched = expected_choice == student_choice
    else:
        norm_student = normalize_answer(student_text)
        norm_ref = normalize_answer(reference)
        matched = bool(norm_student and norm_student == norm_ref)
    verdict = "correct" if matched else "incorrect"
    label = expected_choice or reference
    base_fb = "回答正确。" if matched else f"回答不正确，参考答案为 {label}。"
    if item.explanation:
        base_fb = f"{base_fb} {item.explanation}"
    return verdict, 1.0, base_fb, "learning_mcq_v1", reference


def _lock_answer(db: Session, owner_user_id: UUID, response_id: UUID) -> QuizResponse | None:
    attempt_id = db.scalar(
        sa.select(QuizAttempt.id)
        .join(QuizResponse, QuizResponse.attempt_id == QuizAttempt.id)
        .where(QuizResponse.id == response_id, QuizAttempt.owner_user_id == owner_user_id)
        .with_for_update(of=QuizAttempt)
    )
    if attempt_id is None:
        return None
    return db.scalar(sa.select(QuizResponse).where(QuizResponse.id == response_id).with_for_update())


def submit_text_answer(
    db: Session,
    *,
    owner_user_id: UUID,
    response_id: UUID,
    answer_text: str,
) -> QuizResponse:
    cleaned = (answer_text or "").strip()
    if not cleaned:
        raise ApiError(422, "empty_answer", "请输入作答内容")
    row = _lock_answer(db, owner_user_id, response_id)
    if row is None:
        raise ApiError(404, "response_not_found", "作答记录不存在")
    row._previous_image_path = _previous_answer_image_path(row)
    item = db.get(QuizItem, row.quiz_item_id)
    if item is None:
        raise ApiError(404, "item_not_found", "题目不存在")
    if item.item_type == "mcq":
        verdict, conf, feedback, grader_profile, reference = _grade_mcq_choice(item, cleaned)
    else:
        reference = row.reference_answer_effective or _reference_from_answer_key(item.answer_key)
        with model_connections.task(owner_user_id):
            verdict, conf, feedback, grader_profile = grade_text_answer(
                cleaned, reference, 1.0, stem=item.stem, owner_user_id=owner_user_id
            )
        if item.explanation and item.explanation not in feedback:
            feedback = f"{feedback} {item.explanation}".strip()
    row.answer = {"type": "text", "text": cleaned}
    row.reference_answer_effective = reference
    row.confidence = Decimal(str(round(conf, 4)))
    row.verdict = verdict
    row.verdict_source = "auto"
    row.is_correct = True if verdict == "correct" else False if verdict == "incorrect" else None
    row.feedback = feedback
    row.grader_profile = grader_profile
    row.graded_at = datetime.now(UTC)
    db.flush()
    _sync_wrong_book(db, owner_user_id, item.id, row)
    _refresh_attempt_progress(db, row.attempt_id)
    db.flush()
    db.refresh(row)
    return row


def submit_image_answer(
    db: Session,
    *,
    owner_user_id: UUID,
    response_id: UUID,
    image_bytes: bytes,
    vision_mime: str | None = None,
) -> QuizResponse:
    row = _lock_answer(db, owner_user_id, response_id)
    if row is None:
        raise ApiError(404, "response_not_found", "作答记录不存在")
    row._previous_image_path = _previous_answer_image_path(row)
    item = db.get(QuizItem, row.quiz_item_id)
    if item is None:
        raise ApiError(404, "item_not_found", "题目不存在")
    if vision_mime:
        reference = row.reference_answer_effective or _reference_from_answer_key(item.answer_key)
        saved = model_connections.vision_snapshot(db, owner_user_id)
        grader_profile = "learning_vision_v1"
        try:
            with model_connections.task(
                owner_user_id, vision=saved, llm={"id": None, "kind": "llm"},
            ):
                transcribed, verdict, conf, feedback = grade_vision_image(
                    image_bytes, vision_mime, item.stem, reference, owner=owner_user_id, saved=saved,
                )
            answer_data = {"extraction": "vlm", "transcribed_text": transcribed}
            if item.item_type == "mcq" and verdict != "pending":
                verdict, _, feedback, _, reference = _grade_mcq_choice(item, transcribed)
        except ApiError as exc:
            if exc.code == "invalid_image":
                raise
            verdict, conf, feedback = "pending", 0.0, "视觉模型暂不可用，请手动判题或改用本地识别重试。"
            answer_data = {"extraction": "vlm_failed"}
    else:
        ocr_text, ocr_conf = ocr_answer_bytes(image_bytes)
        answer_data = {"ocr_text": ocr_text, "ocr_confidence": ocr_conf}
        if item.item_type == "mcq":
            reference = row.reference_answer_effective or _reference_from_answer_key(item.answer_key)
            if not ocr_text.strip():
                verdict, conf, feedback, grader_profile = (
                    "pending", 0.0, "未能识别作答文字，请重拍或手动判题。", "learning_mcq_v1",
                )
            else:
                raw_verdict, _, raw_fb, grader_profile, reference = _grade_mcq_choice(item, ocr_text)
                verdict, conf, feedback = _apply_threshold(
                    raw_verdict, max(0.0, min(1.0, ocr_conf)), raw_fb
                )
        else:
            reference = row.reference_answer_effective or _reference_from_answer_key(item.answer_key)
            with model_connections.task(owner_user_id):
                verdict, conf, feedback, grader_profile = grade_text_answer(
                    ocr_text, reference, ocr_conf, stem=item.stem, owner_user_id=owner_user_id
                )
            if item.explanation and item.explanation not in feedback:
                feedback = f"{feedback} {item.explanation}".strip()
    path = _answer_storage_path(row.id)
    try:
        path.write_bytes(image_bytes)
        row.answer = {
            "type": "image",
            "storage_key": str(path.relative_to(Path(get_settings().storage_dir).resolve())),
            **answer_data,
        }
        row.reference_answer_effective = reference
        row.confidence = Decimal(str(round(conf, 4)))
        row.verdict = verdict
        row.verdict_source = "auto"
        row.is_correct = True if verdict == "correct" else False if verdict == "incorrect" else None
        row.feedback = feedback
        row.grader_profile = grader_profile
        row.graded_at = datetime.now(UTC)
        db.flush()
        _sync_wrong_book(db, owner_user_id, item.id, row)
        _refresh_attempt_progress(db, row.attempt_id)
        db.flush()
        db.refresh(row)
    except Exception:
        path.unlink(missing_ok=True)
        raise
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
    row = _lock_answer(db, owner_user_id, response_id)
    if row is None:
        raise ApiError(404, "response_not_found", "作答记录不存在")
    item = db.get(QuizItem, row.quiz_item_id)
    if reference_answer is not None and reference_answer.strip():
        cleaned_ref = reference_answer.strip()
        row.reference_answer_effective = cleaned_ref
        if item is not None:
            choice = (
                resolve_mcq_choice(cleaned_ref, list(item.options or []))
                if item.item_type == "mcq"
                else None
            )
            item.answer_key = {"choice": choice, "text": cleaned_ref} if choice else {"text": cleaned_ref}
    elif not row.reference_answer_effective and item is not None:
        row.reference_answer_effective = _reference_from_answer_key(item.answer_key)
    row.verdict = verdict
    row.verdict_source = "user"
    row.is_correct = True if verdict == "correct" else False if verdict == "incorrect" else None
    row.graded_at = datetime.now(UTC)
    db.flush()
    if item is not None:
        _sync_wrong_book(db, owner_user_id, item.id, row)
    _refresh_attempt_progress(db, row.attempt_id)
    db.flush()
    db.refresh(row)
    return row


def _sync_wrong_book(db: Session, owner_user_id: UUID, item_id: UUID, response: QuizResponse) -> None:
    # Serialize first-wrong creation for the same quiz item before checking the unique row.
    item = db.scalar(
        sa.select(QuizItem).where(QuizItem.id == item_id).with_for_update()
    )
    origin_wrong_id = None
    if item and isinstance(item.rubric, dict) and item.rubric.get("origin_wrong_id"):
        try:
            origin_wrong_id = UUID(str(item.rubric["origin_wrong_id"]))
        except ValueError:
            origin_wrong_id = None

    existing = None
    if origin_wrong_id is not None:
        existing = db.scalar(
            sa.select(WrongQuestion).where(
                WrongQuestion.id == origin_wrong_id,
                WrongQuestion.owner_user_id == owner_user_id,
            ).with_for_update()
        )
    if existing is None:
        existing = (
            db.execute(
                sa.select(WrongQuestion).where(
                    WrongQuestion.owner_user_id == owner_user_id,
                    WrongQuestion.quiz_item_id == item_id,
                ).with_for_update()
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
                    next_review_at=now,
                )
            )
        else:
            existing.wrong_count += 1
            if existing.quiz_item_id == item_id:
                existing.last_response_id = response.id
            existing.last_wrong_at = now
            existing.status = "open"
            existing.next_review_at = now
    elif response.verdict == "correct" and existing is not None:
        interval = 7 if existing.status == "reviewing" and existing.next_review_at is not None else 3
        existing.status = "reviewing"
        existing.next_review_at = now + timedelta(days=interval)


def list_attempts_page(
    db: Session,
    owner_user_id: UUID,
    *,
    knowledge_base_id: UUID | None = None,
    limit: int = 20,
    cursor: UUID | None = None,
) -> dict:
    stmt = (
        sa.select(QuizAttempt, Quiz, KnowledgeBase.name)
        .join(Quiz, Quiz.id == QuizAttempt.quiz_id)
        .outerjoin(
            KnowledgeBase,
            sa.and_(
                KnowledgeBase.id == Quiz.course_id,
                KnowledgeBase.owner_user_id == owner_user_id,
                KnowledgeBase.deleted_at.is_(None),
            ),
        )
        .where(
            QuizAttempt.owner_user_id == owner_user_id,
            Quiz.owner_user_id == owner_user_id,
            Quiz.deleted_at.is_(None),
        )
    )
    if knowledge_base_id is not None:
        stmt = stmt.where(Quiz.course_id == knowledge_base_id)
    if cursor is not None:
        cursor_stmt = (
            sa.select(QuizAttempt.updated_at)
            .join(Quiz, Quiz.id == QuizAttempt.quiz_id)
            .where(
                QuizAttempt.id == cursor,
                QuizAttempt.owner_user_id == owner_user_id,
                Quiz.owner_user_id == owner_user_id,
                Quiz.deleted_at.is_(None),
            )
        )
        if knowledge_base_id is not None:
            cursor_stmt = cursor_stmt.where(Quiz.course_id == knowledge_base_id)
        cursor_updated_at = db.scalar(cursor_stmt)
        if cursor_updated_at is None:
            raise ApiError(400, "invalid_cursor", "Invalid cursor")
        stmt = stmt.where(
            sa.tuple_(QuizAttempt.updated_at, QuizAttempt.id) < (cursor_updated_at, cursor)
        )
    rows = db.execute(
        stmt.order_by(QuizAttempt.updated_at.desc(), QuizAttempt.id.desc()).limit(limit + 1)
    ).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    if not rows:
        return {"items": [], "next_cursor": None}
    attempt_ids = [att.id for att, _, _ in rows]
    resp_rows = db.execute(
        sa.select(
            QuizResponse.attempt_id,
            sa.func.count(),
            sa.func.count().filter(QuizResponse.verdict.is_not(None)),
            sa.func.count().filter(QuizResponse.verdict == "correct"),
        )
        .where(QuizResponse.attempt_id.in_(attempt_ids))
        .group_by(QuizResponse.attempt_id)
    ).all()
    counts = {aid: (total, answered, correct) for aid, total, answered, correct in resp_rows}
    out = []
    for att, quiz, kb_name in rows:
        total, answered, correct = counts.get(att.id, (0, 0, 0))
        out.append(
            {
                "id": str(att.id),
                "quiz_id": str(quiz.id),
                "title": quiz.title,
                "knowledge_base_id": str(quiz.course_id),
                "knowledge_base_name": kb_name,
                "status": att.status,
                "item_count": int(total),
                "answered_count": int(answered),
                "correct_count": int(correct),
                "started_at": att.started_at.isoformat(),
                "updated_at": att.updated_at.isoformat(),
            }
        )
    return {"items": out, "next_cursor": rows[-1][0].id if has_more else None}


def list_attempts(
    db: Session,
    owner_user_id: UUID,
    *,
    knowledge_base_id: UUID | None = None,
    limit: int = 15,
) -> list[dict]:
    """Compatibility wrapper for callers that still need the first history page as a list."""
    return list_attempts_page(
        db,
        owner_user_id,
        knowledge_base_id=knowledge_base_id,
        limit=limit,
    )["items"]


def _wrong_due_condition(now: datetime):
    return sa.and_(
        WrongQuestion.status != "mastered",
        sa.or_(
            sa.and_(
                WrongQuestion.next_review_at.is_not(None),
                WrongQuestion.next_review_at <= now,
            ),
            sa.and_(
                WrongQuestion.next_review_at.is_(None),
                sa.or_(
                    WrongQuestion.status == "open",
                    sa.and_(
                        WrongQuestion.status == "reviewing",
                        WrongQuestion.last_wrong_at <= now - timedelta(days=3),
                    ),
                ),
            ),
        ),
    )


def _wrong_question_is_due(wrong: WrongQuestion, now: datetime) -> bool:
    if wrong.status == "mastered":
        return False
    if wrong.next_review_at is not None:
        return wrong.next_review_at <= now
    if wrong.status == "open":
        return True
    return wrong.status == "reviewing" and wrong.last_wrong_at <= now - timedelta(days=3)


def count_due_wrong_questions(db: Session, owner_user_id: UUID, now: datetime) -> int:
    return int(
        db.scalar(
            sa.select(sa.func.count(WrongQuestion.id)).where(
                WrongQuestion.owner_user_id == owner_user_id,
                _wrong_due_condition(now),
            )
        )
        or 0
    )


def list_wrong_questions(
    db: Session,
    owner_user_id: UUID,
    *,
    knowledge_base_id: UUID | None = None,
    due_only: bool = False,
    now: datetime | None = None,
) -> list[dict]:
    now = now or datetime.now(UTC)
    due_condition = _wrong_due_condition(now)
    stmt = (
        sa.select(WrongQuestion, QuizItem, Quiz, KnowledgeBase.name, QuizResponse)
        .join(QuizItem, QuizItem.id == WrongQuestion.quiz_item_id)
        .join(Quiz, Quiz.id == QuizItem.quiz_id)
        .outerjoin(
            KnowledgeBase,
            sa.and_(
                KnowledgeBase.id == Quiz.course_id,
                KnowledgeBase.owner_user_id == owner_user_id,
                KnowledgeBase.deleted_at.is_(None),
            ),
        )
        .outerjoin(QuizResponse, QuizResponse.id == WrongQuestion.last_response_id)
        .where(
            WrongQuestion.owner_user_id == owner_user_id,
            WrongQuestion.status != "mastered",
        )
    )
    if knowledge_base_id is not None:
        stmt = stmt.where(Quiz.course_id == knowledge_base_id)
    if due_only:
        stmt = stmt.where(due_condition)
    rows = db.execute(
        stmt.order_by(
            sa.case((due_condition, 0), else_=1),
            WrongQuestion.last_wrong_at.desc(),
            WrongQuestion.id.desc(),
        )
    ).all()
    item_ids = [item.id for _, item, *_ in rows]
    sources_map = _load_sources_by_item(db, item_ids)
    latest_retry_by_wrong: dict[str, QuizResponse] = {}
    if rows:
        wrong_id_strs = [str(w.id) for w, *_ in rows]
        retry_origin = QuizItem.rubric["origin_wrong_id"].astext
        retry_rows = db.execute(
            sa.select(retry_origin, QuizResponse)
            .join(QuizItem, QuizItem.id == QuizResponse.quiz_item_id)
            .join(QuizAttempt, QuizAttempt.id == QuizResponse.attempt_id)
            .where(
                QuizAttempt.owner_user_id == owner_user_id,
                QuizResponse.verdict == "incorrect",
                retry_origin.in_(wrong_id_strs),
            )
            .order_by(
                QuizResponse.graded_at.desc().nullslast(),
                QuizResponse.updated_at.desc(),
            )
        ).all()
        for origin_id, r_resp in retry_rows:
            if origin_id and origin_id not in latest_retry_by_wrong:
                latest_retry_by_wrong[origin_id] = r_resp

    def _resp_ts(r: QuizResponse) -> datetime:
        return r.graded_at or r.updated_at or r.created_at or datetime.min.replace(tzinfo=UTC)

    out = []
    for wrong, item, quiz, kb_name, last_resp in rows:
        retry_resp = latest_retry_by_wrong.get(str(wrong.id))
        effective_resp = (
            retry_resp
            if retry_resp is not None and (last_resp is None or _resp_ts(retry_resp) >= _resp_ts(last_resp))
            else last_resp
        )
        ref = (
            (effective_resp.reference_answer_effective if effective_resp else None)
            or _reference_from_answer_key(item.answer_key)
        )
        last_answer_text = None
        if effective_resp and isinstance(effective_resp.answer, dict):
            last_answer_text = (
                effective_resp.answer.get("text")
                or effective_resp.answer.get("transcribed_text")
                or effective_resp.answer.get("ocr_text")
            )
        out.append(
            {
                "id": str(wrong.id),
                "quiz_item_id": str(item.id),
                "knowledge_base_id": str(quiz.course_id),
                "knowledge_base_name": kb_name,
                "item_type": item.item_type,
                "stem": item.stem,
                "options": list(item.options or []),
                "reference_answer": ref,
                "explanation": item.explanation,
                "last_feedback": effective_resp.feedback if effective_resp else None,
                "last_answer": last_answer_text,
                "sources": sources_map.get(item.id, []),
                "status": wrong.status,
                "wrong_count": wrong.wrong_count,
                "last_wrong_at": wrong.last_wrong_at.isoformat(),
                "next_review_at": wrong.next_review_at.isoformat() if wrong.next_review_at else None,
                "is_due": _wrong_question_is_due(wrong, now),
            }
        )
    return out


def retry_wrong_questions(
    db: Session,
    *,
    owner_user_id: UUID,
    wrong_ids: list[UUID] | None = None,
    knowledge_base_id: UUID | None = None,
    due_only: bool = False,
    now: datetime | None = None,
) -> QuizAttempt:
    now = now or datetime.now(UTC)
    stmt = (
        sa.select(WrongQuestion, QuizItem, Quiz)
        .join(QuizItem, QuizItem.id == WrongQuestion.quiz_item_id)
        .join(Quiz, Quiz.id == QuizItem.quiz_id)
        .where(
            WrongQuestion.owner_user_id == owner_user_id,
            WrongQuestion.status != "mastered",
        )
    )
    if wrong_ids is not None:
        stmt = stmt.where(WrongQuestion.id.in_(wrong_ids))
    elif due_only:
        stmt = stmt.where(_wrong_due_condition(now))
    if knowledge_base_id is not None:
        stmt = stmt.where(Quiz.course_id == knowledge_base_id)
    rows = db.execute(stmt.order_by(WrongQuestion.last_wrong_at.desc()).limit(20)).all()
    if not rows:
        raise ApiError(409, "no_wrong_questions", "当前没有可重练的错题")
    anchor_course_id = knowledge_base_id or rows[0][2].course_id
    kb = db.scalar(
        sa.select(KnowledgeBase).where(
            KnowledgeBase.id == anchor_course_id,
            KnowledgeBase.owner_user_id == owner_user_id,
        )
    )
    if kb is not None:
        ensure_course_anchor(db, kb)
    retry_quiz = Quiz(
        owner_user_id=owner_user_id,
        course_id=anchor_course_id,
        title=f"{kb.name} · 错题重练" if kb else "错题重练",
        status="ready",
        difficulty=3,
        source_scope={
            "knowledge_base_id": str(anchor_course_id),
            "retry_wrong_ids": [str(w.id) for w, _, _ in rows],
        },
        generator_profile="wrong_retry_v1",
    )
    db.add(retry_quiz)
    db.flush()
    orig_item_ids = [item.id for _, item, _ in rows]
    orig_sources = db.scalars(
        sa.select(QuizItemSource).where(QuizItemSource.quiz_item_id.in_(orig_item_ids))
    ).all()
    sources_by_orig: dict[UUID, list[QuizItemSource]] = {}
    for s in orig_sources:
        sources_by_orig.setdefault(s.quiz_item_id, []).append(s)

    for ordinal, (wrong, orig_item, _) in enumerate(rows):
        cloned = QuizItem(
            quiz_id=retry_quiz.id,
            ordinal=ordinal,
            item_type=orig_item.item_type,
            stem=orig_item.stem,
            options=list(orig_item.options or []),
            answer_key=orig_item.answer_key,
            rubric={**(orig_item.rubric or {}), "origin_wrong_id": str(wrong.id)},
            explanation=orig_item.explanation,
            difficulty=orig_item.difficulty or 3,
            max_score=orig_item.max_score,
        )
        db.add(cloned)
        db.flush()
        for src in sources_by_orig.get(orig_item.id, []):
            db.add(
                QuizItemSource(
                    quiz_item_id=cloned.id,
                    chunk_id=src.chunk_id,
                    material_version_id=src.material_version_id,
                    index_version_id=src.index_version_id,
                    locator=src.locator,
                    quote_text=src.quote_text,
                )
            )
    db.flush()
    return start_attempt(db, owner_user_id=owner_user_id, quiz_id=retry_quiz.id)


def mark_wrong_mastered(db: Session, owner_user_id: UUID, wrong_id: UUID) -> None:
    row = (
        db.execute(
            sa.select(WrongQuestion).where(
                WrongQuestion.id == wrong_id,
                WrongQuestion.owner_user_id == owner_user_id,
            ).with_for_update()
        )
        .scalars()
        .first()
    )
    if row is None:
        raise ApiError(404, "wrong_not_found", "错题记录不存在")
    row.status = "mastered"
    row.next_review_at = None
    db.flush()


def _reference_revealed(response: QuizResponse) -> bool:
    if response.verdict is not None:
        return True
    answer = response.answer if isinstance(response.answer, dict) else {}
    return answer.get("type") in ("image", "text")


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
    item_ids = [item.id for _, item in responses]
    revealed_ids = {item.id for resp, item in responses if _reference_revealed(resp)}
    sources_map = _load_sources_by_item(db, item_ids, include_quotes=revealed_ids)
    origin_ids = []
    origin_by_item: dict[UUID, str] = {}
    for _, item in responses:
        origin = (item.rubric or {}).get("origin_wrong_id")
        if origin:
            try:
                origin_id = UUID(str(origin))
                origin_ids.append(origin_id)
                origin_by_item[item.id] = str(origin_id)
            except ValueError:
                pass
    origin_libraries = {
        str(wrong_id): str(kb_id) for wrong_id, kb_id in db.execute(
            sa.select(WrongQuestion.id, Quiz.course_id)
            .join(QuizItem, QuizItem.id == WrongQuestion.quiz_item_id)
            .join(Quiz, Quiz.id == QuizItem.quiz_id)
            .join(KnowledgeBase, KnowledgeBase.id == Quiz.course_id)
            .where(
                WrongQuestion.owner_user_id == owner_user_id,
                WrongQuestion.id.in_(origin_ids),
                KnowledgeBase.owner_user_id == owner_user_id,
                KnowledgeBase.deleted_at.is_(None),
            )
        ).all()
    } if origin_ids else {}
    items = []
    for resp, item in responses:
        ref = resp.reference_answer_effective or _reference_from_answer_key(item.answer_key)
        revealed = _reference_revealed(resp)
        raw_options = getattr(item, "options", None)
        row = {
            "response_id": str(resp.id),
            "quiz_item_id": str(item.id),
            "origin_wrong_id": origin_by_item.get(item.id),
            "knowledge_base_id": (
                origin_libraries.get(str(item.rubric["origin_wrong_id"]))
                if (item.rubric or {}).get("origin_wrong_id")
                else str(quiz.course_id) if quiz else None
            ),
            "ordinal": item.ordinal,
            "stem": item.stem,
            "item_type": item.item_type,
            "options": list(raw_options) if isinstance(raw_options, list) else [],
            "verdict": resp.verdict,
            "verdict_source": resp.verdict_source,
            "confidence": float(resp.confidence) if resp.confidence is not None else None,
            "feedback": resp.feedback,
            "answer": resp.answer,
            "sources": sources_map.get(item.id, []),
        }
        if revealed:
            row["reference_answer"] = ref
            explanation = getattr(item, "explanation", None)
            if explanation:
                row["explanation"] = explanation
        items.append(row)
    return {
        "id": str(attempt.id),
        "quiz_id": str(attempt.quiz_id),
        "title": getattr(quiz, "title", None),
        "knowledge_base_id": str(quiz.course_id) if quiz else None,
        "status": attempt.status,
        "items": items,
    }
