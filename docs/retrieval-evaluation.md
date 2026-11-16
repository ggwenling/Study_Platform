# 检索评测指标

本文说明向量检索阶段的 Recall@k、nDCG@k、MRR 与 Hit@k 的含义、标注数据格式及本地运行方式。指标实现借鉴 [Synapse](https://github.com/manav-2812/synapse) 的 `eval/metrics.py` 思路，但**不使用**「把资料原文直接当查询」的动态泄题评测；题目与支撑片段继续分开标注。

## 指标含义

指标在**资料 ID** 粒度计算（`materials.json` 里的 `id`）。检索返回的分块会映射到所属资料再参与计分；同一资料在排序中出现多次时，只保留最早一次排名，避免重复分块抬高分数。

| 指标 | 含义 |
| --- | --- |
| **Recall@k** | 相关资料集中，有多少比例出现在去重后的前 k 个检索结果里。多份相关支撑时，分母为全部相关资料数。 |
| **Hit@k** | 前 k 个结果里是否至少命中一份相关资料（0 或 1）。 |
| **nDCG@k** | 二值相关（命中=1）的折扣累计增益，并按理想排序归一化。 |
| **MRR** | 第一份相关资料出现位置的倒数；未命中为 0。 |

不可回答题（`answerable: false`）不参与宏平均，`n_scored` 只统计可答题。

片段级严格命中仍使用既有 `retrieval_support_hit`（原文子串校验）；文档级指标用于对比不同检索策略的排序质量，二者互补。

## 评测集格式

与事实问答回归共用一套格式，由同目录两个文件组成：

- **`*.jsonl`**：每行一题。必填 `id`、`fact_group`、`split`、`knowledge_base`、`question`、`answerable`；可答题另需 `expected_facts` 与 `support`。
- **`materials.json`**：虚构或私有资料的规格说明；评测时在隔离库中生成文件并建索引。

`support[].document` 列出相关资料的 ID；`support[].quote` 是人工标注的支撑原文（不得与 `question` 规范化后相同）。示例小样见 `backend/evaluation/retrieval_mini.jsonl`（与同目录 `materials.json` 搭配）。

## 本地运行

在 `backend` 目录执行（需先 `uv sync --locked`）。真实评测仍使用 **一次性 PostgreSQL `*_test` 库**，与应用库隔离；行为详见 [事实问答质量回归](rag-quality.md)。

```powershell
# 仅校验标注（无数据库、无模型）
uv run --no-sync study-eval-rag --dataset evaluation/retrieval_mini.jsonl

# 只跑检索与指标（嵌入会产生费用，不调用回答模型）
uv run --no-sync study-eval-rag --dataset evaluation/retrieval_mini.jsonl \
  --strategy baseline --retrieval-only --metric-k 1 3 6 \
  --metrics-format table --live --output ../.local/rag-retrieval/mini-01

# 向量 baseline 与混合（向量 + 中文关键词）对比
uv run --no-sync study-eval-rag --dataset evaluation/retrieval_mini.jsonl \
  --strategy hybrid_compare --retrieval-only --metric-k 1 3 6 \
  --metrics-format table --live --output ../.local/rag-retrieval/mini-hybrid-01

# 完整事实问答对照（检索指标写入 retrieval_metrics.json 与 summary.json）
uv run --no-sync study-eval-rag --strategy compare --live --output ../.local/rag-eval/run-01
```

`--retrieval-only` 时不写 `review.json` / `gate.json`（无生成答案可供复核）。`--metrics-format json` 可在标准输出打印完整汇总 JSON。

输出目录中的 `retrieval_metrics.json` 结构：

```json
{
  "k_values": [1, 3, 6, 10],
  "by_scope": {
    "overall": { "n_scored": 32, "mrr": 0.95, "recall@3": 0.98, "...": "..." },
    "A": { "...": "..." },
    "B": { "...": "..." }
  }
}
```

## 单元测试基线

`tests/unit/test_retrieval_metrics.py` 含手算用例（多相关项、无相关项、k 大于结果长度、重复 ID）。固定排序的汇总预期见 `tests/unit/test_rag_evaluation.py::test_retrieval_summary_from_mock_report`，不依赖外部 API。