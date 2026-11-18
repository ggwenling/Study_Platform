"""REST endpoints for research notes, NotebookLM-style studio artifacts, and flashcards."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from study_platform.api.v1.auth import get_current_user
from study_platform.persistence.database import get_db
from study_platform.persistence.models import User
from study_platform.services import studio

router = APIRouter(tags=["studio"])


class CreateNoteBody(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    content_md: str = Field(min_length=1, max_length=200000)
    note_type: Literal["summary", "chapter_summary", "formula_sheet", "exam_guide"] = "summary"
    message_id: UUID | None = None


class UpdateNoteBody(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    content_md: str | None = Field(default=None, max_length=200000)


class GenerateArtifactBody(BaseModel):
    kind: Literal["study_guide", "mindmap", "formula_sheet"]
    material_ids: list[UUID] | None = Field(default=None, max_length=50)
    title: str | None = Field(default=None, max_length=255)


class GenerateFlashcardsBody(BaseModel):
    count: int = Field(default=8, ge=1, le=25)
    material_ids: list[UUID] | None = Field(default=None, max_length=50)
    title: str | None = Field(default=None, max_length=255)


class ReviewFlashcardBody(BaseModel):
    rating: int = Field(ge=0, le=3)
    idempotency_key: str | None = Field(default=None, max_length=128)


@router.get("/knowledge-bases/{kb_id}/notes")
def list_kb_notes(
    kb_id: UUID,
    limit: int = Query(default=50, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    studio._owned_kb(db, user.id, kb_id)
    return {"items": studio.list_notes(db, user.id, knowledge_base_id=kb_id, limit=limit)}


@router.post("/knowledge-bases/{kb_id}/notes", status_code=201)
def create_kb_note(
    kb_id: UUID,
    body: CreateNoteBody,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    dto = studio.create_note(
        db,
        owner_user_id=user.id,
        knowledge_base_id=kb_id,
        title=body.title,
        content_md=body.content_md,
        note_type=body.note_type,
        message_id=body.message_id,
    )
    db.commit()
    return dto


@router.post("/knowledge-bases/{kb_id}/notes/generate", status_code=201)
def generate_kb_note_artifact(
    kb_id: UUID,
    body: GenerateArtifactBody,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    dto = studio.generate_studio_artifact(
        db,
        owner_user_id=user.id,
        knowledge_base_id=kb_id,
        kind=body.kind,
        material_ids=body.material_ids,
        title=body.title,
    )
    db.commit()
    return dto


@router.get("/notes/{note_id}")
def get_single_note(
    note_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return studio.get_note(db, user.id, note_id)


@router.patch("/notes/{note_id}")
def patch_single_note(
    note_id: UUID,
    body: UpdateNoteBody,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    dto = studio.update_note(
        db,
        owner_user_id=user.id,
        note_id=note_id,
        title=body.title,
        content_md=body.content_md,
    )
    db.commit()
    return dto


@router.delete("/notes/{note_id}", status_code=204)
def delete_single_note(
    note_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    studio.delete_note(db, owner_user_id=user.id, note_id=note_id)
    db.commit()
    return Response(status_code=204)


@router.post("/notes/{note_id}/ingest", status_code=202)
def ingest_note(
    note_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    result = studio.ingest_note_to_library(db, user=user, note_id=note_id)
    db.commit()
    return result


@router.get("/flashcard-decks")
def list_all_decks(
    knowledge_base_id: UUID | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if knowledge_base_id is not None:
        studio._owned_kb(db, user.id, knowledge_base_id)
    return {"items": studio.list_flashcard_decks(db, user.id, knowledge_base_id=knowledge_base_id)}


@router.post("/knowledge-bases/{kb_id}/flashcard-decks/generate", status_code=201)
def generate_deck(
    kb_id: UUID,
    body: GenerateFlashcardsBody,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    dto = studio.generate_flashcard_deck(
        db,
        owner_user_id=user.id,
        knowledge_base_id=kb_id,
        count=body.count,
        material_ids=body.material_ids,
        title=body.title,
    )
    db.commit()
    return dto


@router.get("/flashcard-decks/{deck_id}")
def get_deck(
    deck_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return studio.get_flashcard_deck(db, user.id, deck_id)


@router.delete("/flashcard-decks/{deck_id}", status_code=204)
def remove_deck(
    deck_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    studio.delete_flashcard_deck(db, user.id, deck_id)
    db.commit()
    return Response(status_code=204)


@router.post("/flashcards/{card_id}/review")
def review_card(
    card_id: UUID,
    body: ReviewFlashcardBody,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    dto = studio.review_flashcard(
        db,
        owner_user_id=user.id,
        card_id=card_id,
        rating=body.rating,
        idempotency_key=body.idempotency_key,
    )
    db.commit()
    return dto
