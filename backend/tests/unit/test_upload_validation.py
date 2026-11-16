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