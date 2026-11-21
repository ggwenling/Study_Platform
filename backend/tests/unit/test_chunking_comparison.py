from study_platform.services.chunking import semantic_chunks
from study_platform.services.ingestion import chunks as sliding_chunks
from study_platform.services.rag_evaluation import support_hit


def test_semantic_chunking_preserves_long_sentence_support_split_by_sliding_windows():
    prefix = ("甲" * 49 + "。") * 15 + "乙" * 29 + "。"
    quote = "蓝湾实验室的冷藏区温度上限为4摄氏度，" + "补充校验说明" * 120 + "。"
    text = prefix + quote
    case = {"support": [{"document": "material", "quote": quote}]}
    documents = {"version": "material"}

    old_evidence = [
        {"material_version_id": "version", "text": chunk} for chunk in sliding_chunks(text)
    ]
    new_evidence = [
        {"material_version_id": "version", "text": chunk} for chunk in semantic_chunks(text)
    ]

    assert not support_hit(case, old_evidence, documents)
    assert support_hit(case, new_evidence, documents)
