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


def test_txt_preserves_wrapped_paragraph_indentation_and_form_feed_boundaries(tmp_path):
    path = tmp_path / "wrapped.txt"
    path.write_bytes(b"\xef\xbb\xbfClause starts\r\n  and continues.\r\n\r\nfirst\fsecond\r\n\r\n")
    assert list(ingestion.extract_text(path)) == [
        ("Clause starts\n  and continues.", {"start_line": 1, "end_line": 2}),
        ("first", {"line": 4}),
        ("second", {"line": 4}),
    ]


def test_markdown_sections_split_headings_and_ignore_code_fences(tmp_path, monkeypatch):
    path = tmp_path / "notes.md"
    path.write_text(
        "导言说明\n\n"
        "# 第一章 概述 ##\n"
        "第一段内容\n"
        "```python\n"
        "# 这不是标题\n"
        "x = 1\n"
        "```\n\n"
        "## 1.1 子节\n"
        "子节正文\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(ingestion, "source_path", lambda _: path)
    version = SimpleNamespace(id=uuid4(), source_mime="text/markdown")
    sections = list(ingestion.extract(version, tmp_path, lambda _: None))
    assert sections == [
        ("导言说明", {"start_line": 1, "end_line": 1}),
        (
            "# 第一章 概述 ##\n第一段内容\n```python\n# 这不是标题\nx = 1\n```",
            {"start_line": 3, "end_line": 8, "heading": "第一章 概述", "level": 1},
        ),
        (
            "## 1.1 子节\n子节正文",
            {"start_line": 10, "end_line": 11, "heading": "1.1 子节", "level": 2},
        ),
    ]


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


def test_docx_rejects_negative_leading_grid_columns(tmp_path):
    doc = Document()
    row = doc.add_table(rows=1, cols=1).rows[0]
    row.cells[0].text = "evidence"
    before = OxmlElement("w:gridBefore")
    before.set(qn("w:val"), "-1")
    row._tr.get_or_add_trPr().append(before)
    path = tmp_path / "negative.docx"
    doc.save(path)

    with pytest.raises(ValueError, match="negative leading column count"):
        list(ingestion.extract_docx(path))


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
    monkeypatch.setattr(
        ingestion,
        "get_settings",
        lambda: SimpleNamespace(
            max_pdf_pages=50,
            pdf_ocr_min_effective_chars=40,
            pdf_ocr_image_page_max_chars=120,
        ),
    )
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


def test_docx_uncompressed_limit_rejects_oversized_archive(tmp_path, monkeypatch):
    import zipfile

    path = tmp_path / "source.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", "x" * 1024)
        archive.writestr("huge.bin", b"x" * (81 * 1024 * 1024))
    monkeypatch.setattr(
        ingestion,
        "get_settings",
        lambda: SimpleNamespace(max_docx_uncompressed_bytes=80 * 1024 * 1024),
    )
    with pytest.raises(ValueError, match="OOXML uncompressed"):
        ingestion.ensure_ooxml_uncompressed_limit(path)


def test_docx_semantic_v4_groups_flat_table_rows_with_headers_and_preserves_nested_fallback(
    tmp_path, monkeypatch
):
    from study_platform.services.material_reader import locator_label

    path = tmp_path / "matrix.docx"
    doc = Document()
    table = doc.add_table(rows=3, cols=2)
    table.rows[0]._tr.get_or_add_trPr().append(OxmlElement("w:tblHeader"))
    table.cell(0, 0).text = "检查项"
    table.cell(0, 1).text = "期望结果"
    table.cell(1, 0).text = "CT-8402"
    table.cell(1, 1).text = "样本验证状态 304"
    table.cell(2, 0).text = "KB-2920"
    table.cell(2, 1).text = "弹层关闭后回到触发按钮"

    nested_table = doc.add_table(rows=1, cols=1)
    cell = nested_table.cell(0, 0)
    cell.paragraphs[0].text = "outer"
    cell.add_table(rows=1, cols=1).cell(0, 0).text = "inner"
    doc.save(path)

    monkeypatch.setattr(ingestion, "source_path", lambda _: path)
    version = SimpleNamespace(
        id=uuid4(),
        source_mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    v4_units = list(ingestion.extract(version, tmp_path, lambda _: None, "semantic-v4"))
    assert v4_units[:3] == [
        ("检查项 | 期望结果", {"table_path": [{"table": 1, "row": 1}]}),
        (
            "检查项：CT-8402 | 期望结果：样本验证状态 304",
            {"table_path": [{"table": 1, "row": 2}]},
        ),
        (
            "检查项：KB-2920 | 期望结果：弹层关闭后回到触发按钮",
            {"table_path": [{"table": 1, "row": 3}]},
        ),
    ]
    assert v4_units[3:] == [
        ("outer", {"table_path": [{"table": 2, "row": 1, "column": 1}], "cell_paragraph": 1}),
        (
            "inner",
            {
                "table_path": [
                    {"table": 2, "row": 1, "column": 1},
                    {"table": 1, "row": 1},
                ],
            },
        ),
    ]
    assert locator_label(v4_units[1][1]) == "表格 1，第 2 行"
    assert locator_label(v4_units[3][1]) == "表格 2，第 1 行，第 1 列，段落 1"
    assert locator_label(v4_units[4][1]) == "表格 2，第 1 行，第 1 列 / 表格 1，第 1 行"

    # Default (semantic-v3) stays cell-by-cell for backward compatibility.
    v3_units = list(ingestion.extract(version, tmp_path, lambda _: None))
    assert v3_units[0] == (
        "检查项",
        {"table_path": [{"table": 1, "row": 1, "column": 1}], "cell_paragraph": 1},
    )


@pytest.mark.parametrize("explicitly_disabled", [False, True])
def test_docx_semantic_v4_keeps_short_unmarked_first_row_as_data(tmp_path, explicitly_disabled):
    doc = Document()
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "ALPHA-100"
    table.cell(0, 1).text = "62 minutes"
    table.cell(1, 0).text = "BETA-200"
    table.cell(1, 1).text = "91 minutes"
    if explicitly_disabled:
        header = OxmlElement("w:tblHeader")
        header.set(qn("w:val"), "false")
        table.rows[0]._tr.get_or_add_trPr().append(header)
    path = tmp_path / "records.docx"
    doc.save(path)

    assert list(ingestion.extract_docx(path, group_table_rows=True)) == [
        ("ALPHA-100 | 62 minutes", {"table_path": [{"table": 1, "row": 1}]}),
        ("BETA-200 | 91 minutes", {"table_path": [{"table": 1, "row": 2}]}),
    ]
