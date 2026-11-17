"""Deterministic checks for RAG grounding prompts and citation publication (no models)."""

import pytest

from study_platform.core.errors import ApiError
from study_platform.services import chat


def evidence(count: int = 2) -> list[dict]:
    return [
        {
            "id": f"chunk-{index}",
            "title": f"Doc {index}",
            "text": f"Supporting fact {index}.",
        }
        for index in range(1, count + 1)
    ]


def _system_message(scope, chunks):
    return chat.prompt(scope, chunks, [])[0].content


def test_prompt_states_material_only_grounding_rules():
    scope = {"answer_mode": "knowledge_only", "intent": "qa", "content": "问题？"}
    system = _system_message(scope, evidence())
    assert "只使用本轮提供的资料作为资料依据" in system
    assert "不编造引用" in system
    assert "资料不足" in system


def test_prompt_knowledge_only_forbids_general_knowledge_supplement():
    scope = {"answer_mode": "knowledge_only", "intent": "qa", "content": "问题？"}
    system = _system_message(scope, evidence())
    assert "禁止用自身知识补充资料外的事实" in system
    assert "【通用补充】" not in system


def test_prompt_with_general_requires_section_markers():
    scope = {"answer_mode": "with_general", "intent": "qa", "content": "问题？"}
    system = _system_message(scope, evidence())
    assert "【知识库依据】" in system and "【通用补充】" in system


def test_prompt_serializes_one_source_block_per_evidence_chunk():
    scope = {"answer_mode": "knowledge_only", "intent": "qa", "content": "问题？"}
    system = _system_message(scope, evidence(3))
    assert '"source": "S1"' in system
    assert '"source": "S3"' in system
    assert '"source": "S4"' not in system
    assert "Supporting fact 2." in system


def test_cited_chunks_preserves_citation_order_and_indexes_evidence():
    rows = chat.cited_chunks("依据 [S2] 与 [S1]", evidence(2))
    assert [row["source_number"] for row in rows] == [2, 1]
    assert rows[0]["id"] == "chunk-2"


def test_cited_chunks_rejects_invalid_source_numbers():
    with pytest.raises(ApiError) as error:
        chat.cited_chunks("引用 [S9]", evidence(1))
    assert error.value.code == "citation_invalid"


@pytest.mark.parametrize(
    "answer",
    [
        "没有标注的回答",
        "General knowledge answer without markers",
    ],
)
def test_cited_chunks_requires_markers_when_evidence_exists(answer):
    with pytest.raises(ApiError) as error:
        chat.cited_chunks(answer, evidence(1))
    assert error.value.code == "citation_missing"
    assert error.value.retryable is True


def test_cited_chunks_allows_explicit_insufficient_material_without_markers():
    assert chat.cited_chunks("当前资料不足，无法根据所选知识库回答。", evidence(1)) == []


def test_cited_chunks_allows_empty_evidence_without_markers():
    assert chat.cited_chunks("简短回答", []) == []