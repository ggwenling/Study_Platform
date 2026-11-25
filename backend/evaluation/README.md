# 中文事实问答固定样例

`facts.jsonl` 包含 24 个事实组、48 道问题；`materials.json` 仅包含虚构资料。按事实组固定划分 calibration 与 heldout，每组的两种问法始终在同一集合。

评测命令根据文字定义生成 DOCX、TXT、PNG、JPEG 和扫描 PDF，经实际解析与分块建立隔离索引。另一知识库中的补充规定用于检测越界回答；无答案题不要求检索结果为空。

用法、标注格式、人工复核与启用规则见 [质量回归说明](../../docs/rag-quality.md)。检索阶段 Recall@k / nDCG@k 等见 [检索评测指标](../../docs/retrieval-evaluation.md)。`retrieval_mini.jsonl` 为七题中文小样。私人资料及运行报告不放在本目录。

`edge_cases/facts.jsonl` 是独立的合成边界回归集，包含 8 个事实组和 16 道问题，涵盖精确编号与术语、同库跨文档证据、跨库近似答案拒答、普通数字干扰、表格、公式和扫描 PDF。PDF 由评测 CLI 根据文字段落生成图像 PDF。运行离线结构校验（不连接数据库或模型）：

```powershell
cd backend
uv run --no-sync study-eval-rag --dataset evaluation/edge_cases/facts.jsonl
```

`long_documents/prepare.py` 生成长文档验收集。它包含三个 150 页 PDF（原生文本、全扫描、混合），350 行 DOCX 表格、1,200 行文本、20 道带证据位置的题及一个 151 页的拒绝样本。

```powershell
uv run --no-sync python evaluation/long_documents/prepare.py ../.local/long-documents
```

在隔离客户端上传 `original` 内的资料，等待索引完成，再根据 `questions.jsonl` 核对所有事实、引文和页码或表格位置。用 `over-pages.pdf` 验证超过页数上限时客户端展示明确原因。
