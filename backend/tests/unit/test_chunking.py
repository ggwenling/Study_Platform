from study_platform.services.chunking import semantic_chunks


def test_code_tables_and_block_math_are_kept_atomic():
    code = "```python\n" + "print('long line')\n" * 4 + "```"
    table = "| 字段 | 值 |\n| --- | --- |\n" + "| 项目 | 项目说明项目说明 |\n" * 30
    formula = "$$\n\\sum_{i=1}^{n} x_i = y\n$$"

    chunks = list(semantic_chunks(f"前言。\n\n{code}\n\n{table}\n\n{formula}\n\n结语。"))

    assert 500 <= len(table.strip()) <= 800
    assert code in chunks
    assert table.strip() in chunks
    assert formula in chunks
    assert chunks[0] == "前言。"
    assert chunks[-1] == "结语。"


def test_long_paragraph_chunks_overlap_on_sentence_boundaries():
    sentences = [f"句子{number}" + "甲" * 45 + "。" for number in range(20)]
    chunks = list(semantic_chunks("".join(sentences)))

    assert len(chunks) > 1
    assert all(len(chunk) <= 800 for chunk in chunks)
    for previous, following in zip(chunks, chunks[1:]):
        overlap = [sentence for sentence in sentences if sentence in previous and sentence in following]
        assert previous.endswith("。")
        assert overlap and following.startswith("".join(overlap))


def test_headings_form_chunk_boundaries():
    chunks = list(semantic_chunks("# 第一章\n章节内容。\n\n# 第二章\n另一章节内容。"))

    assert chunks == ["# 第一章\n章节内容。", "# 第二章\n另一章节内容。"]


def test_oversized_code_and_table_split_by_lines_and_keep_wrappers():
    code_lines = [f"value_{number} = '{'x' * 100}'" for number in range(30)]
    code = "```python\n" + "\n".join(code_lines) + "\n```"
    table_rows = [f"| row {number} | {'x' * 60} |" for number in range(30)]
    table = "| key | value |\n| --- | --- |\n" + "\n".join(table_rows)

    code_chunks = list(semantic_chunks(code))
    table_chunks = list(semantic_chunks(table))

    assert len(code_chunks) > 1
    assert all(chunk.startswith("```python\n") and chunk.endswith("\n```") for chunk in code_chunks)
    assert all(len(chunk) <= 1600 for chunk in code_chunks)
    assert len(table_chunks) > 1
    assert all(chunk.startswith("| key | value |\n| --- | --- |\n") for chunk in table_chunks)
    assert all(len(chunk) <= 1600 for chunk in table_chunks)
    assert all(row in "\n".join(table_chunks) for row in table_rows)


def test_oversized_math_block_keeps_formula_content_and_delimiters():
    formula = "$$ x_0 +\n" + "x" * 1700 + "\n+ x_2 $$"

    chunks = list(semantic_chunks(formula))

    assert len(chunks) > 1
    assert all(chunk.startswith("$$\n") and chunk.endswith("\n$$") for chunk in chunks)
    assert all(len(chunk) <= 1600 for chunk in chunks)
    assert "x_0 +" in chunks[0] and "+ x_2" in chunks[-1]


def test_long_single_line_is_bounded_without_splitting_words():
    text = "word " * 300
    chunks = list(semantic_chunks(text))

    assert len(chunks) > 1
    assert all(len(chunk) <= 800 for chunk in chunks)
    assert all(not chunk.startswith("ord") for chunk in chunks[1:])
