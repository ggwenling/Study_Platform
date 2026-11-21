import zipfile
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image
from pptx import Presentation
from pptx.util import Inches

from study_platform.services import ingestion


def _settings():
    return SimpleNamespace(
        max_docx_uncompressed_bytes=80 * 1024 * 1024,
        max_pdf_pages=50,
        max_image_pixels=1_000_000,
        max_pptx_ocr_images=2,
    )


def test_extracts_pptx_titles_tables_speaker_notes_and_image_ocr(tmp_path, monkeypatch):
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[5])
    slide.shapes.title.text = "Slide heading"
    textbox = slide.shapes.add_textbox(Inches(1), Inches(2), Inches(5), Inches(1))
    textbox.text_frame.text = "A visible sentence on this slide."
    table = slide.shapes.add_table(2, 2, Inches(1), Inches(3), Inches(5), Inches(1)).table
    table.cell(0, 0).text = "Name"
    table.cell(0, 1).text = "Value"
    table.cell(1, 0).text = "Alpha"
    table.cell(1, 1).text = "42"
    slide.notes_slide.notes_text_frame.text = "Speaker notes for the first slide."

    image_slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    image_data = BytesIO()
    Image.new("RGB", (20, 12), "white").save(image_data, format="PNG")
    image_slide.shapes.add_picture(BytesIO(image_data.getvalue()), Inches(1), Inches(1))
    path = tmp_path / "lesson.pptx"
    presentation.save(path)

    monkeypatch.setattr(ingestion, "get_settings", _settings)
    monkeypatch.setattr(ingestion, "ocr_image", lambda _image: [{"text": "Chart label"}])
    version = SimpleNamespace(id=uuid4(), page_count=None)
    sections = list(ingestion.extract_pptx(path, version))

    assert version.page_count == 2
    slide_text, slide_locator = sections[0]
    assert "Slide heading" in slide_text
    assert "A visible sentence" in slide_text
    assert "Name | Value" in slide_text
    assert slide_locator == {"slide": 1, "heading": "Slide heading"}
    assert sections[1] == ("Speaker notes for the first slide.", {"slide": 1, "notes": True})
    assert sections[2] == ("Chart label", {"slide": 2, "ocr": True})


def test_pptx_ocr_fallback_respects_image_count_limit(tmp_path, monkeypatch):
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    image_data = BytesIO()
    Image.new("RGB", (10, 10), "white").save(image_data, format="PNG")
    for left in (Inches(1), Inches(2), Inches(3)):
        slide.shapes.add_picture(BytesIO(image_data.getvalue()), left, Inches(1))
    path = tmp_path / "images.pptx"
    presentation.save(path)
    settings = _settings()
    settings.max_pptx_ocr_images = 2
    monkeypatch.setattr(ingestion, "get_settings", lambda: settings)
    calls = []
    monkeypatch.setattr(
        ingestion,
        "ocr_image",
        lambda _image: calls.append(1) or [{"text": f"label {len(calls)}"}],
    )

    sections = list(ingestion.extract_pptx(path))
    assert len(calls) == 2
    assert sections == [("label 1\n\nlabel 2", {"slide": 1, "ocr": True})]


def test_ooxml_uncompressed_limit_rejects_pptx_zip_bomb(tmp_path, monkeypatch):
    path = tmp_path / "oversized.pptx"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("ppt/presentation.xml", "x" * (81 * 1024 * 1024))
    monkeypatch.setattr(ingestion, "get_settings", _settings)
    with pytest.raises(ValueError, match="OOXML uncompressed"):
        ingestion.ensure_ooxml_uncompressed_limit(path)


def test_pptx_page_count_is_not_limited_by_pdf_page_setting(tmp_path, monkeypatch):
    presentation = Presentation()
    for _ in range(3):
        presentation.slides.add_slide(presentation.slide_layouts[6])
    path = tmp_path / "many.pptx"
    presentation.save(path)
    settings = _settings()
    settings.max_pdf_pages = 2
    monkeypatch.setattr(ingestion, "get_settings", lambda: settings)
    version = SimpleNamespace(page_count=None)
    assert list(ingestion.extract_pptx(path, version)) == []
    assert version.page_count == 3
