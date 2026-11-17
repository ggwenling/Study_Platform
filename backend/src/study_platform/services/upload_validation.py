"""Upload content sniffing aligned with allowed file extensions."""

from pathlib import Path

from study_platform.core.errors import ApiError

EXTENSION_MIMES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


def expected_mime(filename: str) -> str | None:
    return EXTENSION_MIMES.get(Path(filename).suffix.lower())


def validate_upload_content(header: bytes, mime: str, filename: str) -> None:
    if mime == "application/pdf":
        if not header.startswith(b"%PDF"):
            raise ApiError(400, "invalid_file_content", "文件内容与 PDF 类型不符")
        return
    if mime == "image/png":
        if not header.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ApiError(400, "invalid_file_content", "文件内容与 PNG 类型不符")
        return
    if mime == "image/jpeg":
        if not header.startswith(b"\xff\xd8\xff"):
            raise ApiError(400, "invalid_file_content", "文件内容与 JPEG 类型不符")
        return
    if mime.endswith("wordprocessingml.document"):
        if not header.startswith(b"PK\x03\x04"):
            raise ApiError(400, "invalid_file_content", "文件内容与 DOCX 类型不符")
        return
    if mime == "text/plain":
        sample = header[:4096]
        try:
            sample.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise ApiError(400, "invalid_file_content", "文本文件必须为 UTF-8 编码") from error
        return
    raise ApiError(400, "unsupported_file", "Unsupported file type")