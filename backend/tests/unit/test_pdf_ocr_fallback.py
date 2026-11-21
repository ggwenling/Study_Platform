import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image

from study_platform.services import ingestion


class FakeBitmap:
    def to_pil(self):
        return Image.new("RGB", (100, 100), "white")

    def close(self):
        pass


class FakeTextPage:
    def __init__(self, text):
        self.text = text

    def get_text_range(self):
        return self.text

    def close(self):
        pass


class FakePage:
    def __init__(self, text, bounds):
        self.text = text
        self.bounds = bounds

    def get_textpage(self):
        return FakeTextPage(self.text)

    def get_size(self):
        return 100, 100

    def get_objects(self, *, filter):
        assert filter == [3]
        return [SimpleNamespace(get_bounds=lambda: self.bounds)] if self.bounds else []

    def render(self, *, scale):
        assert scale > 0
        return FakeBitmap()

    def close(self):
        pass


class FakeDocument:
    def __init__(self, page):
        self.page = page

    def __len__(self):
        return 1

    def __getitem__(self, index):
        assert index == 0
        return self.page

    def close(self):
        pass


def setup_pdf(tmp_path, monkeypatch, text, bounds=None):
    path = tmp_path / "source.pdf"
    path.write_bytes(b"fake PDF")
    page = FakePage(text, bounds)
    monkeypatch.setattr(ingestion, "source_path", lambda _: path)
    monkeypatch.setattr(
        ingestion,
        "get_settings",
        lambda: SimpleNamespace(
            max_pdf_pages=50,
            pdf_ocr_min_effective_chars=40,
            pdf_ocr_image_page_max_chars=120,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "pypdfium2",
        SimpleNamespace(raw=SimpleNamespace(FPDF_PAGEOBJ_IMAGE=3), PdfDocument=lambda _: FakeDocument(page)),
    )
    version = SimpleNamespace(id=uuid4(), source_mime="application/pdf", page_count=None)
    return version


def test_sparse_watermarked_page_uses_ocr_and_saves_page_image(tmp_path, monkeypatch):
    version = setup_pdf(tmp_path, monkeypatch, "第 3 页 水印", (0, 0, 100, 100))
    ocr_text = "扫描页正文：青竹项目安全阈值为48帕，本文由知循系统提供服务，最终以原始资料正文为准。"
    lines = [{"text": ocr_text, "box": [[1, 2], [3, 4]], "confidence": 0.9}]
    phases = []
    monkeypatch.setattr(ingestion, "ocr_image", lambda _: lines)

    extracted = list(ingestion.extract(version, tmp_path, phases.append))

    assert extracted == [(ocr_text, {"page": 1, "ocr": lines})]
    assert phases == ["parsing", "ocr"]
    assert version.page_count == 1
    assert (tmp_path / "page-1.png").is_file()


def test_short_text_layer_is_kept_when_ocr_does_not_improve_it(tmp_path, monkeypatch):
    version = setup_pdf(tmp_path, monkeypatch, "目录")
    calls = []
    monkeypatch.setattr(ingestion, "ocr_image", lambda _: calls.append(True) or [{"text": "目录"}])

    extracted = list(ingestion.extract(version, tmp_path, lambda _: None))

    assert extracted == [("目录", {"page": 1})]
    assert calls == [True]
    assert not (tmp_path / "page-1.png").exists()


@pytest.mark.parametrize(("text", "error", "falls_back"), [
    ("目录", RuntimeError, True), ("", RuntimeError, False), ("目录", ingestion.LostLease, False),
])
def test_ocr_failure_preserves_usable_text_but_propagates_lost_lease(tmp_path, monkeypatch, text, error, falls_back):
    version = setup_pdf(tmp_path, monkeypatch, text)

    def fail(_):
        raise error()

    monkeypatch.setattr(ingestion, "ocr_image", fail)
    if falls_back:
        assert list(ingestion.extract(version, tmp_path, lambda _: None)) == [(text, {"page": 1})]
    else:
        with pytest.raises(error):
            list(ingestion.extract(version, tmp_path, lambda _: None))
    assert not (tmp_path / "page-1.png").exists()


@pytest.mark.parametrize(("text", "error", "falls_back"), [
    ("目录", OSError, True), ("", OSError, False), ("目录", ingestion.LostLease, False),
])
def test_render_failure_uses_pdf_text_only_when_usable(tmp_path, monkeypatch, text, error, falls_back):
    version = setup_pdf(tmp_path, monkeypatch, text)
    phases = []

    def fail(_page, *, scale):
        raise error()

    monkeypatch.setattr(FakePage, "render", fail)
    if falls_back:
        assert list(ingestion.extract(version, tmp_path, phases.append)) == [(text, {"page": 1})]
    else:
        with pytest.raises(error):
            list(ingestion.extract(version, tmp_path, phases.append))
    assert phases == ["parsing"]


@pytest.mark.parametrize("text_layer", ["水印 1", ""])
def test_ocr_text_survives_preview_write_failure(tmp_path, monkeypatch, text_layer):
    version = setup_pdf(tmp_path, monkeypatch, text_layer)
    ocr_text = "扫描页正文：青竹项目安全阈值为48帕，本文由知循系统提供服务，最终以原始资料正文为准。"
    lines = [{"text": ocr_text, "box": [[1, 2], [3, 4]], "confidence": 0.9}]
    monkeypatch.setattr(ingestion, "ocr_image", lambda _: lines)

    def fail(_image, path, **_):
        path.write_bytes(b"incomplete")
        raise OSError("preview unavailable")

    monkeypatch.setattr(Image.Image, "save", fail)
    assert list(ingestion.extract(version, tmp_path, lambda _: None)) == [
        (ocr_text, {"page": 1, "ocr": lines}),
    ]
    assert not (tmp_path / "page-1.png").exists()


def test_large_image_page_with_sparse_text_triggers_ocr(tmp_path, monkeypatch):
    text_layer = "A" * 60
    version = setup_pdf(tmp_path, monkeypatch, text_layer, (0, 0, 60, 60))
    ocr_text = "B" * 100
    monkeypatch.setattr(ingestion, "ocr_image", lambda _: [{"text": ocr_text}])

    extracted = list(ingestion.extract(version, tmp_path, lambda _: None))

    assert extracted[0][0] == ocr_text
    assert extracted[0][1]["ocr"][0]["text"] == ocr_text


def test_large_image_threshold_uses_page_area():
    pdfium = SimpleNamespace(raw=SimpleNamespace(FPDF_PAGEOBJ_IMAGE=3))
    page = FakePage("text", (0, 0, 50, 50))
    assert ingestion._has_large_image(page, pdfium)
    page.bounds = (0, 0, 49, 49)
    assert not ingestion._has_large_image(page, pdfium)
