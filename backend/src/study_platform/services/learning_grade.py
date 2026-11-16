"""Answer OCR and verdict logic for learning (stub or LLM)."""

from __future__ import annotations

import re
import unicodedata
from io import BytesIO

from PIL import Image

from study_platform.core.config import get_settings
from study_platform.services.ingestion import ocr_image


def normalize_answer(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = re.sub(r"\s+", "", text.lower())
    return text


def ocr_answer_bytes(payload: bytes) -> tuple[str, float]:
    image = Image.open(BytesIO(payload))
    blocks = ocr_image(image)
    if not blocks:
        return "", 0.0
    texts = [b["text"] for b in blocks if b.get("text")]
    confs = [float(b.get("confidence", 0.0)) for b in blocks if b.get("text")]
    joined = " ".join(texts).strip()
    mean_conf = sum(confs) / len(confs) if confs else 0.0
    if not joined:
        return "", 0.0
    return joined, mean_conf


def grade_text_answer(
    ocr_text: str, reference: str, ocr_confidence: float
) -> tuple[str, float, str]:
    """Return verdict, match confidence, feedback."""
    settings = get_settings()
    ref = reference.strip()
    if not ocr_text.strip():
        return "pending", 0.0, "未能识别作答文字，请重拍或手动判题。"
    norm_ocr = normalize_answer(ocr_text)
    norm_ref = normalize_answer(ref)
    if not norm_ref:
        return "pending", 0.3, "题目暂无参考答案，请手动确认。"
    if norm_ocr == norm_ref or norm_ref in norm_ocr or norm_ocr in norm_ref:
        match_conf = max(0.9, min(1.0, ocr_confidence))
        return "correct", match_conf, "与参考答案一致。"
    stub = settings.learning_stub_grader or not settings.deepseek_api_key.get_secret_value()
    if stub:
        match_conf = 0.4
        feedback = "自动比对未通过，请确认或手动判题。"
    else:
        match_conf = 0.5
        feedback = "模型未达置信度门槛，请手动确认。"
    if match_conf >= settings.learning_confidence_threshold:
        return "incorrect", match_conf, feedback
    return "pending", match_conf, feedback