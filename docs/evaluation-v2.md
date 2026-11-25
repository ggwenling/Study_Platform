# Evaluation V2 离线报告

`study-eval-report-v2` 只读取已经保存的报告、冻结题目，以及可选的块清单和审查注释。它不连接数据库、网络或模型。缺输入的字段保持 unknown，不补造。当前 schema 是 `evaluation-v2.3`。

```powershell
uv run --no-sync study-eval-report-v2 --report report.json --questions questions.jsonl --manifest chunks.json --annotations reviews.jsonl --output-dir v2
```

`--report` 可以是带 `cases` 的 JSON，或每行一条记录的 JSONL。题目用 `id` 对齐；记录里的 `question_id` 会当作 `id`。输出是 `report.json`、`records.csv` 和 `summary.md`。

## 旧指标

`retrieval_support_hit` 调用 `rag_evaluation.support_hit`。可回答且缺少 support 时，旧函数对空列表返回真；V2 覆盖率另记为 `empty_gold`，不把这个空真当成证据命中。不可回答题的旧命中保持空，不参与分母。

文档 IR 调用 `retrieval_metrics.evaluate_ranking`，参数顺序与在线评测相同。`refusal_marker` 是 `rag_evaluation.refusal_marker`，只检查回答里有没有“资料不足”。报告里的旧值和重算值不同时写入 `legacy_drift`。

## 新字段

证据覆盖率按 support 项计。同一项里的 quote 数组是或，项与项之间是与，并且必须同一 document。`matched/total` 是命中项数和项数，`locations` 给出证据序号和块身份。文档被检索到不等于语义支撑。`citation.ids_subset_of_evidence` 只表示引用编号落在证据编号里；`citation.faithful` 固定为 unknown。

可满足性只在受支持的全语料清单上做集合覆盖。清单必须同时满足：`source` 为 `corpus_manifest`，`universe` 为 `corpus`，`complete` 与 `candidate_complete` 都为 true，每一行都有文档绑定身份，`chunk_count` 等于行数，`documents` 等于这些行的 document 集合。通过这些内部检查时 `corpus_verified` 为 true，`universe_trust` 为 `corpus_manifest`。这只核对清单契约，不读取在线索引。

仅有 `complete: true`、把已保存证据的 `id` 改写成 `local_index`、或额外塞入无关块，都停在 `universe_trust=caller_asserted`，`algorithm` 与 `structural_at_k` 为 unknown，不会报 impossible。`source` 为 `observed`、`saved_evidence`、`top_k` 或 `retrieval` 时同样 unknown。`chunk_count` 或 `documents` 对不上、以及清单行缺少文档绑定身份时，也是 unknown 并写入 `validation_error`，不会把其余行当成全集。契约通过且没有任何行命中时，才是 `impossible`。

块身份是 `(chunk_id, document)`、`(document, local_index)` 或 `(document, ordinal)`。`local_index` 缺失、空或类型无效时才使用 `ordinal`；`0` 是合法序号。字符串只接受 Unicode 十进制数字（Nd，含 ASCII 和全角）；`isdigit` 为真但没有十进制值的字符，例如 `²`、`①` 和 `²2`，一律无效并回退，不抛异常。布尔值、小数、负数和空白字符串也无效。显式 `chunk_id` 优先。保存证据若只有 `id` 和 `document`，仍用这组身份。

没有 document，且 `chunk_id`、`id`、`local_index`、`ordinal` 都不能组成文档绑定身份的行，不参与覆盖。契约清单里只要有这样的行，`algorithm` 和 `structural_at_k` 都是 unknown，并写入 `validation_error`。同一 document 的同一块覆盖多条 quote 只算一块；两份 document 的相同局部序号是两块，正文相同也不合并。

`algorithm=exact` 时 `minimum`、`lower`、`upper` 相等。项数超过 16 且上下界没有相遇时，`algorithm=bounded`，`minimum` 为 null。`lower` 来自一组互不相交的命中集合，`upper` 是贪心覆盖的大小。贪心结果不会写入 `lower`。`lower > k` 才能判定 `structural_at_k=false`；`upper <= k` 才能判定 true；界跨过 k 时是 unknown。没有完整清单时 scope 是 `observed`，缺 quote 不代表结构上不可满足。

回答分类只接受带 `source`（ai 或 human）、`status=confirmed`、`provenance` 和合法 `label` 的注释。人工注释一致时 `quality.class` 用该标签，`quality.method=annotation_human`，`ground_truth` 仍是 false。只有 AI 注释时 class 保持 unknown，标签放在 `ai_annotation_label`。人工冲突、注释无效或没有注释都是 unknown。必要或补充角色同样只采用一致的人工注释，否则 unknown。

`gate.eligible_for_default` 和 `manual_review_complete` 固定为 false。注释不会自动放行。

汇总按策略分开。`span_micro` 是可回答且有 support 的命中项数除以项数。`span_macro` 是这些题各自 `matched/total` 的平均，分母是题数。`empty_support` 单独计数。不可回答题不进入这两个分母，也不进入 AND 覆盖率分母。旧的 `retrieval_support_hit` 分母仍只计可回答题。

`integration_baseline` 给后续检索和回答集成使用。质量来自这次离线重算，`by_strategy` 复述 summary，不是第二次测量。`retrieval_ms`、`answer_metrics.elapsed_ms` 和 `usage.total_tokens` 只在保存报告里已经有数值时记为 measured，数值是这些记录的平均。没有 `cost` 数值时成本是 `NOT_VERIFIED`，不会把 token 换算成价格。`live_comparison` 固定为 `NOT_VERIFIED`，这些数字不是模型前后收益。
