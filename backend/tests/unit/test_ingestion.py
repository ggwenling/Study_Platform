from types import SimpleNamespace
from uuid import uuid4

import pytest
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from PIL import Image

from study_platform.services import ingestion


def test_text_line_locators_and_chunk_overlap(tmp_path, monkeypatch):
    path = tmp_path / "source"
    path.write_text("first\n\nthird\n", encoding="utf-8")
    monkeypatch.setattr(ingestion, "source_path", lambda _: path)
    version = SimpleNamespace(id=uuid4(), source_mime="text/plain")
    assert list(ingestion.extract(version, tmp_path, lambda _: None)) == [
        ("first", {"line": 1}),
        ("third", {"line": 3}),
    ]
    parts = list(ingestion.chunks("a" * 900))
    assert list(map(len, parts)) == [800, 220]
    assert parts[0][-120:] == parts[1][:120]


def test_docx_paragraph_numbers_preserve_empty_paragraphs(tmp_path, monkeypatch):
    path = tmp_path / "source"
    doc = Document()
    doc.add_paragraph("first")
    doc.add_paragraph("")
    doc.add_paragraph("third")
    doc.save(path)
    monkeypatch.setattr(ingestion, "source_path", lambda _: path)
    version = SimpleNamespace(
        id=uuid4(),
        source_mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    assert list(ingestion.extract(version, tmp_path, lambda _: None)) == [
        ("first", {"paragraph": 1}),
        ("third", {"paragraph": 3}),
    ]


def test_docx_table_only_text_and_merged_cells(tmp_path, monkeypatch):
    path = tmp_path / "source"
    doc = Document()
    table = doc.add_table(rows=1, cols=2)
    table.cell(0, 0).merge(table.cell(0, 1)).text = "merged fact"
    doc.save(path)
    monkeypatch.setattr(ingestion, "source_path", lambda _: path)
    version = SimpleNamespace(
        id=uuid4(),
        source_mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    assert list(ingestion.extract(version, tmp_path, lambda _: None)) == [
        (
            "merged fact",
            {"table_path": [{"table": 1, "row": 1, "column": 1}], "cell_paragraph": 1},
        )
    ]


def test_docx_omitted_leading_grid_column_with_merge_and_nested_table(tmp_path):
    doc = Document()
    row = doc.add_table(rows=1, cols=3).rows[0]
    first, second, third = row.cells
    merged = second.merge(third)
    merged.text = "merged at column two"
    merged.add_table(rows=1, cols=1).cell(0, 0).text = "nested"
    row._tr.remove(first._tc)
    before = OxmlElement("w:gridBefore")
    before.set(qn("w:val"), "1")
    row._tr.get_or_add_trPr().append(before)
    path = tmp_path / "omitted.docx"
    doc.save(path)
    outer = {"table": 1, "row": 1, "column": 2}
    inner = {"table": 1, "row": 1, "column": 1}
    assert list(ingestion.extract_docx(path)) == [
        ("merged at column two", {"table_path": [outer], "cell_paragraph": 1}),
        ("nested", {"table_path": [outer, inner], "cell_paragraph": 1}),
    ]


def test_docx_body_and_nested_tables_follow_document_order(tmp_path, monkeypatch):
    path = tmp_path / "source"
    doc = Document()
    doc.add_paragraph("before")
    cell = doc.add_table(rows=1, cols=1).cell(0, 0)
    cell.paragraphs[0].text = "cell before"
    cell.add_table(rows=1, cols=1).cell(0, 0).text = "nested"
    cell.add_paragraph("cell after")
    doc.add_paragraph("after")
    doc.save(path)
    monkeypatch.setattr(ingestion, "source_path", lambda _: path)
    version = SimpleNamespace(
        id=uuid4(),
        source_mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    outer = {"table": 1, "row": 1, "column": 1}
    nested = {"table": 1, "row": 1, "column": 1}
    assert list(ingestion.extract(version, tmp_path, lambda _: None)) == [
        ("before", {"paragraph": 1}),
        ("cell before", {"table_path": [outer], "cell_paragraph": 1}),
        ("nested", {"table_path": [outer, nested], "cell_paragraph": 1}),
        ("cell after", {"table_path": [outer], "cell_paragraph": 3}),
        ("after", {"paragraph": 2}),
    ]


def test_image_format_validation_and_ocr_locations(tmp_path, monkeypatch):
    path = tmp_path / "source"
    Image.new("RGB", (20, 10), "white").save(path, format="PNG")
    monkeypatch.setattr(ingestion, "source_path", lambda _: path)
    monkeypatch.setattr(ingestion, "get_settings", lambda: SimpleNamespace(max_image_pixels=1000))
    line = {"text": "hello", "box": [[1, 2], [3, 4]], "confidence": 0.9}
    monkeypatch.setattr(ingestion, "ocr_image", lambda _: [line])
    version = SimpleNamespace(id=uuid4(), source_mime="image/png")
    assert list(ingestion.extract(version, tmp_path, lambda _: None)) == [
        ("hello", {"image": 1, "ocr": [line]})
    ]
    assert (tmp_path / "page-1.png").is_file()
    version.source_mime = "image/jpeg"
    with pytest.raises(ValueError, match="Invalid image"):
        list(ingestion.extract(version, tmp_path, lambda _: None))


def test_stale_or_cancelled_worker_cannot_publish():
    from datetime import timedelta

    class Database:
        def __init__(self, job):
            self.job = job

        def execute(self, _):
            return SimpleNamespace(first=lambda: (uuid4(), uuid4()))

        def scalar(self, statement):
            entity = statement.column_descriptions[0]["entity"]
            if entity in (ingestion.KnowledgeBase, ingestion.Material):
                return SimpleNamespace(deleted_at=None)
            return self.job

    job = SimpleNamespace(
        status="running",
        fencing=2,
        cancel_requested_at=None,
        lease_expires_at=ingestion.now() + timedelta(minutes=1),
    )
    with pytest.raises(ingestion.LostLease):
        ingestion.locked_job(Database(job), uuid4(), 1)
    job.cancel_requested_at = ingestion.now()
    with pytest.raises(ingestion.LostLease):
        ingestion.locked_job(Database(job), uuid4(), 2)
    job.cancel_requested_at = None
    job.lease_expires_at = ingestion.now() - timedelta(seconds=1)
    with pytest.raises(ingestion.LostLease):
        ingestion.locked_job(Database(job), uuid4(), 2)


def test_scanned_pdf_renders_page_and_keeps_original(tmp_path, monkeypatch):
    path = tmp_path / "source"
    Image.new("RGB", (200, 100), "white").save(path, format="PDF", resolution=72)
    original = path.read_bytes()
    monkeypatch.setattr(ingestion, "source_path", lambda _: path)
    monkeypatch.setattr(ingestion, "get_settings", lambda: SimpleNamespace(max_pdf_pages=50))
    observed = []

    def fake_ocr(image):
        observed.append(image.size)
        return [{"text": "text", "box": [[1, 2], [3, 4]], "confidence": 0.9}]

    monkeypatch.setattr(ingestion, "ocr_image", fake_ocr)
    version = SimpleNamespace(id=uuid4(), source_mime="application/pdf", page_count=None)
    result = list(ingestion.extract(version, tmp_path, lambda _: None))
    assert version.page_count == 1
    assert result[0][1]["page"] == 1
    assert result[0][1]["ocr"][0]["box"] == [[1, 2], [3, 4]]
    assert observed == [(556, 278)]
    assert (tmp_path / "page-1.png").is_file()
    assert path.read_bytes() == original
