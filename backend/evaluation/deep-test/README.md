# Deep-test 合成语料与离线门禁

**`review_status`：全部为 `synthetic_not_human_curated`，不代表人工审校数据集。**

| 文件 | 说明 |
| --- | --- |
| `questions.jsonl` | 100 道标注题（可再生成） |
| `materials.json` | 15 份虚构资料定义 |
| `isolation_canaries.json` | 跨用户/SSRF 探针（集成矩阵引用） |
| `validate_dataset.py` | ID、证据、类别覆盖校验 |
| `generate_corpus.py` | 可重复生成语料（维护优先于手工复制） |
| `harness/` | 隔离环境与离线执行 |
| `fixtures/` | 小体积可提交样例 + 清单（大文件运行时生成） |

## 快速校验（无 DB、无模型）

```bash
python3 backend/evaluation/deep-test/generate_corpus.py   # 可选：重生成
python3 backend/evaluation/deep-test/validate_dataset.py
```

## 与现有 `study-eval-rag` 的关系

- 字段兼容 [`rag_evaluation.read_dataset`](../src/study_platform/services/rag_evaluation.py) 所需核心键。
- 扩展字段：`category`、`severity`、`allowed_boundary`、`review_status`、`support.page/section`。
- **Live 评测（会花钱）**：在审批模型预算后，使用独立 `TEST_DATABASE_URL` 与 `--dataset backend/evaluation/deep-test/questions.jsonl`；见 [test-environment.md](../../docs/testing/test-environment.md)。

## 结果输出

运行 `harness/run_offline_gate.py` 后，报告写入 **`artifacts/deep-test/<run-id>/`**（已 gitignore），不提交密钥或原始模型输出。