"""Answer OCR and verdict logic for learning (stub or LLM)."""

from __future__ import annotations

import re
import unicodedata
from io import BytesIO
from uuid import UUID

from PIL import Image, UnidentifiedImageError

from study_platform.core.config import get_settings
from study_platform.core.errors import ApiError
from study_platform.services.ingestion import ocr_image
from study_platform.services.learning_llm import grade_with_llm


def normalize_answer(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = re.sub(r"\s+", "", text.lower())
    return text


def ocr_answer_bytes(payload: bytes) -> tuple[str, float]:
    try:
        with Image.open(BytesIO(payload)) as image:
            if image.format not in ("PNG", "JPEG") or image.width * image.height > get_settings().max_image_pixels:
                raise ApiError(422, "invalid_image", "图片格式或尺寸无效")
            image.load()
            blocks = ocr_image(image)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise ApiError(422, "invalid_image", "图片格式或尺寸无效") from error
    if not blocks:
        return "", 0.0
    texts = [b["text"] for b in blocks if b.get("text")]
    confs = [float(b.get("confidence", 0.0)) for b in blocks if b.get("text")]
    joined = " ".join(texts).strip()
    mean_conf = sum(confs) / len(confs) if confs else 0.0
    if not joined:
        return "", 0.0
    return joined, mean_conf


def _apply_threshold(verdict: str, confidence: float, reason: str) -> tuple[str, float, str]:
    settings = get_settings()
    if confidence < settings.learning_confidence_threshold:
        return "pending", confidence, reason or "置信度不足，请手动确认。"
    return verdict, confidence, reason


def grade_text_answer(
    ocr_text: str,
    reference: str,
    ocr_confidence: float,
    *,
    stem: str = "",
    owner_user_id: UUID | None = None,
) -> tuple[str, float, str, str]:
    """Return verdict, match confidence, feedback, grader_profile."""
    settings = get_settings()
    ref = reference.strip()
    if not ocr_text.strip():
        return "pending", 0.0, "未能识别作答文字，请重拍或手动判题。", "learning_stub_v1"
    norm_ocr = normalize_answer(ocr_text)
    norm_ref = normalize_answer(ref)
    if not norm_ref:
        return "pending", 0.3, "题目暂无参考答案，请手动确认。", "learning_stub_v1"
    if norm_ocr == norm_ref:
        match_conf = max(0.0, min(1.0, ocr_confidence))
        verdict, confidence, feedback = _apply_threshold("correct", match_conf, "")
        return verdict, confidence, feedback or "与参考答案一致。", "learning_stub_v1"
    stub = settings.learning_stub_grader or not settings.deepseek_api_key.get_secret_value()
    if stub:
        match_conf = 0.4
        feedback = "自动比对未通过，请确认或手动判题。"
        profile = "learning_stub_v1"
        if match_conf >= settings.learning_confidence_threshold:
            return "incorrect", match_conf, feedback, profile
        return "pending", match_conf, feedback, profile
    graded = grade_with_llm(
        stem or "（无题干）", ref, ocr_text.strip(), owner_user_id=owner_user_id
    )
    profile = "learning_llm_v1"
    if graded is None:
        return "pending", 0.35, "自动判题暂不可用，请手动确认。", profile
    verdict, confidence, reason = _apply_threshold(graded.verdict, graded.confidence, graded.reason)
    return verdict, confidence, reason, profile