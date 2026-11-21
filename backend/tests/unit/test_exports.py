from io import BytesIO
from zipfile import ZipFile

import pytest

from study_platform.core.errors import ApiError
from study_platform.services import study_exports


def test_anki_metadata_html_fields_and_tags():
    deck = {
        "title": "期末 复习",
        "knowledge_base_name": "数学 / 一",
        "cards": [{
            "front": "#问\t题",
            "back": "第一行\r\n第二行 <b> & $x>2$",
            "interval_days": 4,
            "sources": [{"title": "线性代数"}],
        }],
    }
    out = study_exports.format_flashcard_deck(deck, "anki_tsv").splitlines()
    assert out[:4] == ["#separator:tab", "#html:true", "#columns:Front\tBack\tTags", "#tags column:3"]
    assert out[4].split("\t") == ["&#35;问&#9;题", "第一行<br>第二行 &lt;b&gt; &amp; $x&gt;2$", "数学_一 期末_复习"]
    md = study_exports.format_flashcard_deck(deck, "markdown")
    assert "## Q1" in md and "复习间隔：4 天" in md and "来源：线性代数" in md


def test_note_zip_has_unique_safe_unicode_names_and_citations(monkeypatch):
    note = {
        "title": "../研究\\笔记: 例",
        "content_md": "保留 Markdown 正文。",
        "citations": [{"source_label": "S1", "title": "讲义", "location_label": "第 3 页"}],
    }
    with ZipFile(BytesIO(study_exports.notes_archive("我的库", iter([note, note])))) as archive:
        names = archive.namelist()
        assert len(set(names)) == 3 and names[-1] == "INDEX.md"
        assert all(".." not in name and "/" not in name and "\\" not in name for name in names)
        assert "研究" in names[0]
        assert "我的库" in archive.read("INDEX.md").decode()
        assert "[S1] 讲义 · 第 3 页" in archive.read(names[0]).decode()
    monkeypatch.setattr(study_exports, "MAX_NOTE_EXPORT_BYTES", 10)
    with pytest.raises(ApiError) as too_large:
        study_exports.notes_archive("我的库", iter([note]))
    assert too_large.value.status_code == 413
    with pytest.raises(ApiError) as empty:
        study_exports.notes_archive("我的库", iter([]))
    assert empty.value.code == "no_notes"
