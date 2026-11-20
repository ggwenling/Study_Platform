"""Downloadable personal study data."""

import re
from datetime import UTC, datetime
from html import escape
from io import BytesIO
from urllib.parse import quote
from zipfile import ZIP_DEFLATED, ZipFile

from study_platform.core.errors import ApiError

MAX_NOTE_EXPORT_BYTES = 32 * 1024 * 1024


def export_filename(title: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "-", title).replace("..", "-")
    return cleaned[:55].strip(" .") or "Untitled"


def attachment(filename: str) -> dict[str, str]:
    return {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename, safe='')}",
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
    }


def _anki_field(text: str) -> str:
    value = escape(text).replace("\r\n", "\n").replace("\r", "\n")
    value = value.replace("\n", "<br>").replace("\t", "&#9;")
    return "&#35;" + value[1:] if value.startswith("#") else value


def format_flashcard_deck(deck: dict, format: str) -> str:
    if format == "anki_tsv":
        tags = " ".join(
            re.sub(r"[^\w-]+", "_", name).strip("_")
            for name in (deck["knowledge_base_name"] or "", deck["title"])
        ).strip()
        rows = ["#separator:tab", "#html:true", "#columns:Front\tBack\tTags", "#tags column:3"]
        rows.extend(f"{_anki_field(row['front'])}\t{_anki_field(row['back'])}\t{tags}" for row in deck["cards"])
        return "\n".join(rows) + "\n"

    rows = [f"# {deck['title']}"]
    for idx, card in enumerate(deck["cards"], 1):
        sources = "、".join(dict.fromkeys(source["title"] for source in card["sources"]))
        rows.extend([
            f"## Q{idx} {card['front']}",
            card["back"],
            f"复习间隔：{card['interval_days']} 天",
        ])
        if sources:
            rows.append(f"来源：{sources}")
    return "\n\n".join(rows) + "\n"


def notes_archive(library: str, notes) -> bytes:
    out = BytesIO()
    index = [f"# {library}", f"导出时间：{datetime.now(UTC).isoformat()}", "## 笔记目录"]
    total = 0
    with ZipFile(out, "w", compression=ZIP_DEFLATED) as archive:
        for sequence, note in enumerate(notes, 1):
            filename = f"{sequence:03d}-{export_filename(note['title'])}.md"
            lines = [f"# {note['title']}", note["content_md"]]
            if note["citations"]:
                lines.append("## 关联来源")
                lines.extend(
                    f"- [{cite['source_label']}] {cite['title']} · {cite['location_label']}"
                    for cite in note["citations"]
                )
            content = ("\n\n".join(lines) + "\n").encode("utf-8")
            total += len(content) + len(filename.encode("utf-8"))
            if total > MAX_NOTE_EXPORT_BYTES:
                raise ApiError(413, "note_export_too_large", "笔记超过单次导出上限。")
            archive.writestr(filename, content)
            index.append(f"- [{note['title']}](<{filename}>)")
        if total == 0:
            raise ApiError(409, "no_notes", "该知识库没有可导出的笔记。")
        archive.writestr("INDEX.md", "\n\n".join(index) + "\n")
    return out.getvalue()
