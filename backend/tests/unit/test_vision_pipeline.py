from io import BytesIO
from uuid import uuid4

import pytest
from PIL import Image

from study_platform.core.config import Settings
from study_platform.core.errors import ApiError
from study_platform.services import model_calls, vision_pipeline


def test_explicit_image_interpretation_sends_normalized_image_and_marks_origin(tmp_path, monkeypatch):
    path = tmp_path / "source"
    with Image.new("RGBA", (32, 16), (0, 0, 0, 0)) as image:
        image.save(path, format="PNG")
    owner = uuid4()
    saved = {"id": str(uuid4()), "revision": 2, "kind": "vision"}
    witnessed = []

    def invoke(prompt, data, mime, *, context, saved):
        assert "Markdown" in prompt
        assert mime == "image/jpeg"
        with Image.open(BytesIO(data)) as image:
            assert image.size == (32, 16) and image.getpixel((0, 0)) == (255, 255, 255)
        witnessed.append((context.owner_user_id, context.purpose, saved))
        return "公式 $x^2$"

    phases = []
    monkeypatch.setattr(model_calls, "invoke_vision", invoke, raising=False)
    assert vision_pipeline.extract_vision(path, "image/png", phases.append, owner=owner, saved=saved) == (
        1, [("公式 $x^2$", {"image": 1, "extraction": "vlm"})],
    )
    assert witnessed == [(owner, "vision_ingest", saved)] and phases == ["vision"]


def test_visual_pdf_page_limit_rejects_before_any_model_call(tmp_path, monkeypatch):
    path = tmp_path / "source"
    with Image.new("RGB", (100, 100), "white") as image:
        image.save(path, format="PDF", save_all=True, append_images=[image])
    monkeypatch.setattr(
        vision_pipeline, "get_settings", lambda: Settings(_env_file=None, max_vlm_pages_per_material=1)
    )
    monkeypatch.setattr(model_calls, "invoke_vision", lambda *_a, **_k: pytest.fail("unexpected VLM request"), raising=False)
    for render in [
        lambda: vision_pipeline.vision_pdf_page_count(path),
        lambda: vision_pipeline.extract_vision(path, "application/pdf", lambda _: None, owner=uuid4(), saved={}),
    ]:
        with pytest.raises(ApiError) as error:
            render()
        assert error.value.code == "vision_page_limit"


def test_vision_grader_preserves_pending_and_rejects_invalid_output(monkeypatch):
    monkeypatch.setattr(
        vision_pipeline, "get_settings", lambda: Settings(_env_file=None, learning_confidence_threshold=0.85)
    )
    miss = '{"transcribed_text":"x", "verdict":"correct", "confidence":0.6, "feedback":"模型反馈"}'
    assert vision_pipeline.parse_vision_grade(miss)[:3] == ("x", "pending", 0.6)
    hit = miss.replace("0.6", "0.9")
    assert vision_pipeline.parse_vision_grade(hit) == ("x", "correct", 0.9, "模型反馈")
    assert vision_pipeline.parse_vision_grade(hit.replace("correct", "pending"))[1] == "pending"
    for invalid in [hit.replace("0.9", "NaN"), hit.replace("correct", "unknown"), "unstructured"]:
        with pytest.raises(ApiError) as error:
            vision_pipeline.parse_vision_grade(invalid)
        assert error.value.code == "vision_grade_invalid"
