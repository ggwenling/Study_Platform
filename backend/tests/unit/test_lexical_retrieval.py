from study_platform.services.lexical import lexical_index_text


def test_lexical_index_drops_noise_and_segment_chinese():
    indexed = lexical_index_text("项目编号 QZ-731 位于“雪松”仓库")
    assert "731" in indexed
    assert "雪松" in indexed