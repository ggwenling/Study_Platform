"""Opt-in semantic-v4 geometry. Counts and splits are mechanism checks, not quality claims."""

import hashlib
import os
from pathlib import Path

import pytest

from study_platform.services.chunking import (
    SEMANTIC_PROFILE,
    SEMANTIC_V4_PROFILE,
    narrow_locator,
    plain_text_units,
    resolve_pipeline_profile,
    segment_unit,
    semantic_chunks,
    semantic_v4_chunks,
)
from study_platform.services.eval_embedding_resume import (
    batch_identity,
    load_cached_batch,
    prepare_embedding_cache,
    store_cached_batch,
)
from study_platform.services.reindex import _parts, pipeline_for_reindex

CLOCK = "\n".join([
    "When more than one suitable response is stored, a cache MUST use the",
    "most recent response (as determined by the Date header field).",
    "",
    "A cache that does not have a clock available MUST NOT use stored",
    "responses without revalidating them upon every use.",
])
RULE = "\n".join([
    "If a Transfer-Encoding header field is present in a request and",
    "the chunked transfer coding is not the final encoding, the",
    "message body length cannot be determined reliably; the server",
    "MUST respond with the 400 (Bad Request) status code and then",
    "close the connection.",
])
RFC_V3_COUNTS = {
    "rfc-7234.txt": 1558,
    "rfc-9110.txt": 8658,
    "rfc-9111.txt": 1498,
    "rfc-9112.txt": 1931,
}
RFC_SHA256 = {
    "rfc-7234.txt": "1d084238ae6692d1e3322fa0850dbb9596812332d92b76d9a815c5ed9b600dea",
    "rfc-9110.txt": "21c1cdce6ab0e5509b04d84a28000836c7a087cf786efe6f04877ebfff47232a",
    "rfc-9111.txt": "aeb52adb3279d5f23dae34f68af11bd5cef0a0aff7ffcd014c9ca93c5302cf3e",
    "rfc-9112.txt": "e4f426bac6206b67fdf9e0da826154f70588db2133a0a86b15cde4ff725d8937",
}


def _v4_chunks(text):
    return [
        (part, locator)
        for unit, locator in plain_text_units(text, SEMANTIC_V4_PROFILE)
        for part, locator in segment_unit(unit, locator, SEMANTIC_V4_PROFILE)
    ]


def test_missing_profile_stays_on_semantic_v3_and_unknown_is_rejected():
    assert resolve_pipeline_profile(None) == SEMANTIC_PROFILE
    assert resolve_pipeline_profile("") == SEMANTIC_PROFILE
    assert resolve_pipeline_profile(SEMANTIC_PROFILE) == SEMANTIC_PROFILE
    assert resolve_pipeline_profile(SEMANTIC_V4_PROFILE) == SEMANTIC_V4_PROFILE
    for value in ("semantic-v5", "semantic-v4 ", "Semantic-V4", "document-v1"):
        with pytest.raises(ValueError, match="unknown pipeline profile"):
            resolve_pipeline_profile(value)


def test_wrapped_condition_and_continuation_share_a_block_and_locator():
    chunks = _v4_chunks(CLOCK)
    clock = [item for item in chunks if "MUST NOT use stored" in item[0]]
    assert len(clock) == 1
    text, locator = clock[0]
    assert "responses without revalidating them upon every use." in text
    assert locator["start_line"] == 4 and locator["end_line"] == 5
    source = (
        "A cache that does not have a clock available MUST NOT use stored\n"
        "responses without revalidating them upon every use."
    )
    assert source[locator["start_char"]:locator["end_char"]] == text
    lines = CLOCK.splitlines()
    covered = "\n".join(lines[locator["start_line"] - 1:locator["end_line"]])
    assert text == covered

    rule = _v4_chunks(RULE)
    assert len(rule) == 1
    assert "present in a request" in rule[0][0]
    assert "close the connection." in rule[0][0]
    assert rule[0][1]["start_line"] == 1 and rule[0][1]["end_line"] == 5

    old = list(plain_text_units(CLOCK, None))
    assert [locator["line"] for _, locator in old] == [1, 2, 4, 5]
    assert not any("MUST NOT use stored" in text and "revalidating" in text for text, _ in old)
    assert not any(
        "present in a request" in text and "close the connection." in text
        for text, _ in plain_text_units(RULE, SEMANTIC_PROFILE)
    )


def test_long_page_wrap_is_not_a_sentence_break_and_real_ends_still_split():
    """A wrap stays with its sentence when that sentence fits. A real period still splits."""
    page = ("A" * 770) + ".\nCONDITION_HALF\nCONTINUATION_HALF."
    old = list(semantic_chunks(page))
    new = list(semantic_v4_chunks(page))
    # v3 can overlap a short previous line into the next chunk. The failure mode is
    # the earlier chunk, which still ends on the wrap and omits the continuation.
    assert any("CONDITION_HALF" in part and "CONTINUATION_HALF" not in part for part in old)
    assert any("CONDITION_HALF" in part and "CONTINUATION_HALF." in part for part in new)
    assert not any(
        "CONDITION_HALF" in part and "CONTINUATION_HALF" not in part for part in new
    )
    assert all(len(part) <= 800 for part in old)
    assert all(len(part) <= 800 for part in new)
    assert any(part.startswith("A" * 40) and "CONTINUATION_HALF" not in part for part in new)
    located = list(segment_unit(
        page, {"page": 7, "ocr": [{"text": "whole page"}]}, SEMANTIC_V4_PROFILE,
    ))
    holder = next(
        locator for part, locator in located
        if "CONDITION_HALF" in part and "CONTINUATION_HALF" in part
    )
    assert holder["page"] == 7 and "ocr" not in holder
    assert page[holder["start_char"]:holder["end_char"]].count("CONDITION_HALF") == 1
    assert holder["end_char"] - holder["start_char"] < len(page)
    for part, locator in located:
        assert locator["page"] == 7
        assert page[locator["start_char"]:locator["end_char"]] == part


def test_page_boundary_is_not_joined():
    first = list(segment_unit("condition stays here\n", {"page": 1}, SEMANTIC_V4_PROFILE))
    second = list(segment_unit("continuation is the next page.", {"page": 2}, SEMANTIC_V4_PROFILE))
    assert first and second
    assert all(locator["page"] == 1 for _, locator in first)
    assert all(locator["page"] == 2 for _, locator in second)
    assert not any("continuation" in text for text, _ in first)


def test_blank_lines_unicode_and_abbreviations():
    units = list(plain_text_units("left\n\n\nright\n", SEMANTIC_V4_PROFILE))
    assert [(text, locator["start_line"], locator["end_line"]) for text, locator in units] == [
        ("left", 1, 1), ("right", 4, 4),
    ]
    spaced = list(plain_text_units("a\n \n\u3000\nb\n", SEMANTIC_V4_PROFILE))
    assert [text for text, _ in spaced] == ["a", "b"]
    joined = _v4_chunks("条件折行\n续句。")
    assert len(joined) == 1
    assert joined[0][0] == "条件折行\n续句。"
    assert joined[0][1]["start_line"] == 1 and joined[0][1]["end_line"] == 2

    core = "See e.g. the cache and U.S. rules and 3.14 exactly."
    parts = list(semantic_v4_chunks(core + " " + ("pad " * 160)))
    assert any(core in part for part in parts)
    short = list(semantic_v4_chunks("This sentence ends. Next stays apart.", chunk_size=24, overlap=0))
    assert short[0] == "This sentence ends."
    assert "Next stays apart." in short[1]
    assert all(len(part) <= 24 for part in short)


def test_eight_hundred_bound_and_overlap_limits_are_explicit():
    """A sentence past 800 splits. Overlap repeats a suffix only when whitespace can anchor it."""
    spaced = ("the condition continues across the wrap " * 80).strip() + "."
    parts = list(semantic_v4_chunks(spaced))
    assert len(parts) > 1
    assert all(len(part) <= 800 for part in parts)
    assert parts[0].startswith("the condition continues")
    assert parts[-1].endswith(".")
    assert parts[1][:40] in parts[0]
    assert sum(len(part) for part in parts) <= len(spaced) + 120 * (len(parts) - 1)

    solid = "甲" * 2000 + "。"
    pieces = list(semantic_v4_chunks(solid))
    assert len(pieces) > 1
    assert all(len(part) <= 800 for part in pieces)
    assert pieces[0] == "甲" * 800
    assert pieces[-1].endswith("。")
    assert sum(len(part) for part in pieces) == len(solid)


def test_markdown_atoms_and_old_profile_behavior_stay():
    text = "\n".join([
        "intro line",
        "",
        "```",
        "code wraps",
        "inside the fence",
        "```",
        "",
        "| a | b |",
        "| --- | --- |",
        "| 1 | 2 |",
        "",
        "$$",
        "x",
        "+ y",
        "$$",
    ])
    assert list(semantic_chunks(text)) == list(semantic_v4_chunks(text))
    parts = list(semantic_v4_chunks(text))
    assert any(part.startswith("```") and "code wraps" in part and "inside the fence" in part for part in parts)
    assert any("| a | b |" in part and "| 1 | 2 |" in part for part in parts)
    assert any(part.startswith("$$") and "+ y" in part for part in parts)
    assert list(plain_text_units("first\n\nthird\n", None)) == [
        ("first", {"line": 1}), ("third", {"line": 3}),
    ]
    assert list(plain_text_units("first\n\nthird\n", SEMANTIC_PROFILE)) == [
        ("first", {"line": 1}), ("third", {"line": 3}),
    ]


def test_missing_fragment_does_not_keep_the_parent_line_span():
    locator, _cursor = narrow_locator(
        "alpha\nbeta", "not in the source", {"start_line": 1, "end_line": 2, "page": 3},
    )
    assert locator == {"page": 3}


def test_rechunk_does_not_relabel_old_sections_as_v4():
    rows = [
        ("MUST NOT use stored", {"line": 4}, "s1", None, True, "MUST NOT use stored"),
        ("without revalidating", {"line": 5}, "s2", None, True, "without revalidating"),
    ]
    parts = list(_parts(rows, True, SEMANTIC_PROFILE))
    assert [part[0] for part in parts] == ["MUST NOT use stored", "without revalidating"]
    assert [part[1] for part in parts] == [{"line": 4}, {"line": 5}]
    assert pipeline_for_reindex(True, SEMANTIC_PROFILE) == SEMANTIC_PROFILE
    assert pipeline_for_reindex(True, "document-v1") == SEMANTIC_PROFILE
    assert pipeline_for_reindex(True, "semantic-v2") == SEMANTIC_PROFILE
    paragraph = "MUST NOT use stored\nwithout revalidating"
    v4_rows = [(paragraph, {"start_line": 4, "end_line": 5}, "s", None, True, paragraph)]
    reproduced = list(_parts(v4_rows, True, SEMANTIC_V4_PROFILE))
    assert len(reproduced) == 1
    assert "MUST NOT use stored" in reproduced[0][0] and "without revalidating" in reproduced[0][0]
    assert reproduced[0][1]["start_line"] == 4 and reproduced[0][1]["end_line"] == 5
    assert pipeline_for_reindex(True, SEMANTIC_V4_PROFILE) == SEMANTIC_V4_PROFILE
    assert pipeline_for_reindex(False, SEMANTIC_V4_PROFILE) == SEMANTIC_V4_PROFILE


def test_embedding_cache_rejects_a_v3_batch_presented_as_v4(tmp_path):
    cache = prepare_embedding_cache(tmp_path / "cache")
    texts = ["same stored text"]
    common = {
        "source_sha256": "ab" * 32,
        "embedding_model": "unit-embed",
        "embedding_base_url": "https://example.test/v1",
        "embedding_dim": 1024,
        "texts": texts,
    }
    key_v3, identity_v3 = batch_identity(pipeline_profile=SEMANTIC_PROFILE, **common)
    key_v4, identity_v4 = batch_identity(pipeline_profile=SEMANTIC_V4_PROFILE, **common)
    assert key_v3 != key_v4
    vector = [[0.2] * 1024]
    store_cached_batch(cache, key_v3, identity_v3, vector, None, "unknown", None)
    assert load_cached_batch(cache, key_v3, identity_v3, 1)["vectors"] == vector
    assert load_cached_batch(cache, key_v4, identity_v4, 1) is None
    assert load_cached_batch(cache, key_v3, identity_v4, 1) is None
    copied = cache / "batches" / f"{key_v4}.json"
    copied.write_bytes((cache / "batches" / f"{key_v3}.json").read_bytes())
    os.chmod(copied, 0o600)
    assert load_cached_batch(cache, key_v4, identity_v4, 1) is None

