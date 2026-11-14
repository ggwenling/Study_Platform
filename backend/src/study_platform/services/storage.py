"""Private, version-scoped storage."""

from pathlib import Path
from uuid import UUID

from study_platform.core.config import get_settings


def version_dir(version_id: UUID) -> Path:
    return Path(get_settings().storage_dir).resolve() / str(version_id)


def source_path(version_id: UUID) -> Path:
    return version_dir(version_id) / "source"


def page_path(version_id: UUID, page_no: int) -> Path:
    if page_no < 1:
        raise ValueError("Invalid page")
    return version_dir(version_id) / f"page-{page_no}.png"
