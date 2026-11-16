# 中文事实问答固定样例

`facts.jsonl` 包含 24 个事实组、48 道问题；`materials.json` 仅包含虚构资料。按事实组固定划分 calibration 与 heldout，每组的两种问法始终在同一集合。

评测命令根据文字定义生成 DOCX、TXT、PNG、JPEG 和扫描 PDF，经实际解析与分块建立隔离索引。另一知识库中的补充规定用于检测越界回答；无答案题不要求检索结果为空。

用法、标注格式、人工复核与启用规则见 [质量回归说明](../../docs/rag-quality.md)。检索阶段 Recall@k / nDCG@k 等见 [检索评测指标](../../docs/retrieval-evaluation.md)。`retrieval_mini.jsonl` 为三题中文小样。私人资料及运行报告不放在本目录。
