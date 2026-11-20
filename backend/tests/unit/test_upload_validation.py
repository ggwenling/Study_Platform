import zipfile

import pytest

from study_platform.core.errors import ApiError
from study_platform.services import upload_validation as validation


def test_validate_pdf_header():
    validation.validate_upload_content(b"%PDF-1.7", "application/pdf", "a.pdf")


def test_reject_mismatched_pdf():
    with pytest.raises(ApiError) as error:
        validation.validate_upload_content(b"<html>", "application/pdf", "bad.pdf")
    assert error.value.code == "invalid_file_content"


def test_validate_png_header():
    validation.validate_upload_content(b"\x89PNG\r\n\x1a\n", "image/png", "a.png")


def test_validate_docx_zip_header():
    validation.validate_upload_content(b"PK\x03\x04", validation.EXTENSION_MIMES[".docx"], "a.docx")


def test_pptx_requires_a_valid_zip_with_presentation_entry(tmp_path):
    path = tmp_path / "lesson.pptx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("ppt/presentation.xml", "<presentation/>")
    validation.validate_upload_content(
        path.read_bytes()[:8192],
        validation.EXTENSION_MIMES[".pptx"],
        path.name,
        path=path,
    )

    forged = tmp_path / "forged.pptx"
    with zipfile.ZipFile(forged, "w") as archive:
        archive.writestr("not-a-presentation.txt", "hello")
    with pytest.raises(ApiError) as error:
        validation.validate_upload_content(
            forged.read_bytes()[:8192],
            validation.EXTENSION_MIMES[".pptx"],
            forged.name,
            path=forged,
        )
    assert error.value.code == "invalid_file_content"


def test_rejects_corrupt_pptx_zip(tmp_path):
    path = tmp_path / "bad.pptx"
    path.write_bytes(b"PK\x03\x04invalid")
    with pytest.raises(ApiError) as error:
        validation.validate_upload_content(
            path.read_bytes(), validation.EXTENSION_MIMES[".pptx"], path.name, path=path
        )
    assert error.value.code == "invalid_file_content"


def test_validate_markdown_extension_and_utf8_content():
    assert validation.expected_mime("notes.md") == "text/markdown"
    assert validation.expected_mime("README.MARKDOWN") == "text/markdown"
    validation.validate_upload_content("# 标题\n正文".encode("utf-8"), "text/markdown", "notes.md")
    with pytest.raises(ApiError) as error:
        validation.validate_upload_content(b"\xff\xfe", "text/markdown", "bad.md")
    assert error.value.code == "invalid_file_content"


def test_validate_text_sample_with_utf8_character_split_at_boundary():
    content = b"a" * 4095 + "中".encode("utf-8") + b"tail"
    validation.validate_upload_content(content[:4096], "text/plain", "a.txt")

    validator = validation.Utf8TextValidator()
    validator.feed(content[:4096])
    validator.feed(content[4096:])
    validator.finish()

    with_bom = validation.Utf8TextValidator()
    with_bom.feed(b"\xef\xbb\xbftext")
    with_bom.finish()


def test_validate_uploaded_text_split_at_8192_byte_sample_boundary():
    content = b"a" * 8191 + "中".encode("utf-8") + b"tail"
    validator = validation.Utf8TextValidator()
    validator.feed(content[:8192])
    validator.feed(content[8192:])
    validator.finish()


def test_reject_invalid_utf8_after_sample():
    validator = validation.Utf8TextValidator()
    validator.feed(b"a" * 4096)
    with pytest.raises(ApiError) as error:
        validator.feed(b"\xff")
    assert error.value.code == "invalid_file_content"


def test_reject_truncated_utf8_at_end_of_file():
    validator = validation.Utf8TextValidator()
    validator.feed(b"a" * 4095 + b"\xe4")
    validator.feed(b"\xb8")
    with pytest.raises(ApiError) as error:
        validator.finish()
    assert error.value.code == "invalid_file_content"


@pytest.mark.parametrize("truncated_bom", [b"\xef", b"\xef\xbb"])
def test_reject_truncated_utf8_bom_at_end_of_file(truncated_bom):
    validator = validation.Utf8TextValidator()
    validator.feed(truncated_bom)
    with pytest.raises(ApiError) as error:
        validator.finish()
    assert error.value.code == "invalid_file_content"
