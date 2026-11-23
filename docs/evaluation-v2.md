# Evaluation V2 离线报告

`study-eval-report-v2` 只读取已经保存的报告、冻结题目，以及可选的块清单和审查注释。它不连接数据库、网络或模型。缺输入的字段保持 unknown，不补造。

```powershell
uv run --no-sync study-eval-report-v2 --report report.json --questions questions.jsonl --manifest chunks.json --annotations reviews.jsonl --output-dir v2
```

`--report` 可以是带 `cases` 的 JSON，或每行一条记录的 JSONL。题目用 `id` 对齐；记录里的 `question_id` 会当作 `id`。输出是 `report.json`、`records.csv` 和 `summary.md`。

## 旧指标

`retrieval_support_hit` 调用 `rag_evaluation.support_hit`。可回答且缺少 support 时，旧函数对空列表返回真；V2 覆盖率另记为 `empty_gold`，不把这个空真当成证据命中。不可回答题的旧命中保持空，不参与分母。

文档 IR 调用 `retrieval_metrics.evaluate_ranking`，参数顺序与在线评测相同。`refusal_marker` 是 `rag_evaluation.refusal_marker`，只检查回答里有没有“资料不足”。报告里的旧值和重算值不同时写入 `legacy_drift`。

## 新字段

证据覆盖率按 support 项计。同一项里的 quote 数组是或，项与项之间是与，并且必须同一 document。`matched/total` 是命中项数和项数，`locations` 给出证据序号和块身份。文档被检索到不等于语义支撑。`citation.ids_subset_of_evidence` 只表示引用编号落在证据编号里；`citation.faithful` 固定为 unknown。

可满足性在完整清单上做集合覆盖。清单必须 `complete: true`，并且没有把 `candidate_complete` 设为 false。块身份是 `(chunk_id, document)` 或 `(document, local_index)`。只有局部序号、没有 document 的行不能参与覆盖。同一 document 的同一块覆盖多条 quote 只算一块；两份 document 的相同局部序号是两块，正文相同也不合并。

`algorithm=exact` 时 `minimum`、`lower`、`upper` 相等。项数超过 16 且上下界没有相遇时，`algorithm=bounded`，`minimum` 为 null。`lower` 来自一组互不相交的命中集合，`upper` 是贪心覆盖的大小。贪心结果不会写入 `lower`。`lower > k` 才能判定 `structural_at_k=false`；`upper <= k` 才能判定 true；界跨过 k 时是 unknown。没有完整清单时 scope 是 `observed`，缺 quote 不代表结构上不可满足。

回答分类只接受带 `source`（ai 或 human）、`status=confirmed`、`provenance` 和合法 `label` 的注释。人工注释一致时 `quality.class` 用该标签，`quality.method=annotation_human`，`ground_truth` 仍是 false。只有 AI 注释时 class 保持 unknown，标签放在 `ai_annotation_label`。人工冲突、注释无效或没有注释都是 unknown。必要或补充角色同样只采用一致的人工注释，否则 unknown。

`gate.eligible_for_default` 和 `manual_review_complete` 固定为 false。注释不会自动放行。
