# 检索索引复用与离线取舍

本文件是检索侧这一轮的记录。基线提交是 `ed62865d8a3a5dd46c6a5531367f36fcccec1483`。没有跑 Live，没有调用 chat、embedding、rerank 或 vision API，没有改冻结题、既有评测报告或人工状态。

## 交付

`study-eval-rag` 默认仍在 `finally` 里 `DROP` 新建的 `study_eval_<32 hex>_test` 库。保留和导出都要显式打开：

- `--retain-index`：成功结束后留下该测试库，并在输出目录写 `index_manifest.json`。失败仍删除。
- `--export-index DIR`：写出不含连接串和密钥的包（`manifest.json`、`records.jsonl`、`package.sha256`）。向量来自已经写入的行，不再次嵌入。
- `--reuse-index PATH`：跳过语料嵌入。目录包会导入到新的 `study_eval_*_test`；单独的 manifest 会只读连回它记录的那个库。复用必须带 `--retrieval-only`。查询向量仍由既有 `evaluate_case` 在获批的 `--live` 运行里生成；本轮没有执行那一步。

复用前要同时满足：库名匹配 `study_eval_<32 hex>_test`；当前角色拥有该库；库名不等于应用库，也不等于 `TEST_DATABASE_URL` 指向的共享测试库；manifest 版本、schema revision、知识库、所有者、资料哈希、索引版本、embedding 模型、1024 维与库内快照一致。不一致就拒绝，不检索。导出包的哈希不符或出现凭据字段也拒绝。

`--strategy hybrid` 和 `hybrid_compare` 在 `RETRIEVAL_LEXICAL_WEIGHT` 仍为 0 时直接退出，避免把纯向量结果写成混合对比。默认 `retrieval_top_k=10`、`retrieval_candidate_k=20`、`retrieval_lexical_weight=0` 没有改。`chat._candidate_limit()` 已是 `max(candidate_k, top_k)`；只把 `RETRIEVAL_TOP_K` 设为 24 时，候选池是 24。

## 离线测量

合成语料（23 条互不相同的一行 span，另有 40 行干扰；无标准原文）由 `synthetic_probe()` 计算：

| 量 | 值 |
| --- | ---: |
| 按行切块 | 23，多块行 0 |
| 覆盖全部 span 的最少块 | 23 |
| AND@10 在完美排序下 | 不可能 |
| AND@24 在完美排序下 | 可能 |
| 把 23 行合成一篇再切 | 2 块，最少覆盖 2 |
| 词法 token overlap@10 / @24 | 10 / 23 |

四份哈希匹配的 RFC 重放（`backend/evaluation/offline_probe_rfc_counts.json`）与冻结切分一致：rfc-9110 8658、rfc-9111 1498、rfc-9112 1931、rfc-7234 1558，多块行都是 0。九题 179/179 span 落在重放块里。最少块数是 22、15、23、17、22、21、15、23、17。

词法数字用的是 `lexical_query_terms` 与块 token 的交集排序，不是生产环境的 `ts_rank` / IDF / RRF。九题里只有 51/179 个 span 所在块含有查询 token；overlap@10 命中 3，@24 命中 14。REAL-040 的 23 个 span 与查询没有共同 token，词法分支无法把它们排进来。38 道可答题合计 282 个 span，含查询 token 的是 62，overlap@10 命中 6，@24 命中 17，文档命中 @10 是 11/38。

2026-11-23 从官方 URL 下载的 FIPS 46 SHA256 是 `33113108c9867def8465a0679c7acf7de89b08392e165a7ed86349b64ccd762f`，与评测记录 `a1c85b29aab88d505c33ab30988794a824e90be521fd535bbaefaac9b3935404` 不同。该项是 FAIL，文件未进入切分或排序。另外四份 NIST PDF 本轮没有下载，状态是 NOT_RUN。

## 取舍

| 组件 | 决定 | 依据 |
| --- | --- | --- |
| 默认切块 | 保持 | 179 条 span 已经在一行一块里。合成语料把多行并进一块后，最少块数从 23 降到 2，但那会改变 locator，并且必须整库重嵌。线上引用效果未测。 |
| 默认 top_k | 保持 10 | 九题的最少块数是 15–23，AND@10 在完美排序下也不成立。没有 top 24 的真实向量名次。 |
| 词法权重 | 保持 0 | 上面的 token 交集已经给出词法分支的上限。权重 0 的 hybrid 对比无效，CLI 会拒绝。 |
| rerank | 不接入 | 需要付费排序 API。状态 NOT_VERIFIED。 |
| 向量 top 24 | 不改默认 | 真实向量和相似度阈值未测。状态 NOT_VERIFIED。 |
| query rewrite 质量 | 不改检索 | 本轮没有新的生成调用。状态 NOT_VERIFIED。 |

## 已测与未测

已测：上面的合成数字、RFC 重放块数、set-cover、token overlap、FIPS 46 哈希 FAIL，以及下方单测。保存的旧答案审计不是优化后的 Live 收益。

未测：一次获批构建的墙钟、查询嵌入费用、top_k=24 的真实 Recall 和 support_hit、生产 RRF、rerank、rewrite 对九题的影响、NIST PDF 页图像素。质量、延迟、费用、资源没有新的 Live 读数，不填。

上次成功语料嵌入仍是既有账本里的 734 次调用、14636 块、600623 token、约 40 分钟。本轮没有重做。复用之后，那一次全量嵌入不必为了每次只读检索再付一次；查询嵌入的增量费用要等获批运行才有数字。

## 验证

在 `backend` 执行：

```bash
uv run ruff check src/study_platform/services/eval_index_reuse.py \
  src/study_platform/services/retrieval_probe.py \
  src/study_platform/cli/evaluate_rag.py \
  evaluation/offline_retrieval_probe.py \
  tests/unit/test_eval_index_reuse.py \
  tests/unit/test_retrieval_probe.py \
  tests/unit/test_rag_evaluation.py
uv run pytest tests/unit/test_eval_index_reuse.py \
  tests/unit/test_retrieval_probe.py \
  tests/unit/test_rag_evaluation.py \
  tests/unit/test_retrieval_ranking.py \
  tests/unit/test_lexical_retrieval.py \
  tests/unit/test_chunking.py \
  tests/unit/test_retrieval_metrics.py -q
```

本轮结果：`ruff check` 通过；上述 pytest 为 `78 passed`。单测覆盖：默认仍删除；`--retain-index` 成功才保留、失败仍删除；创建权限不足不删除；复用路径不调用 `seed_corpus` 或 `embed_texts`；schema、模型、维度、资料哈希、所有者不符拒绝；库名 allowlist；应用库和共享测试库拒绝；角色不是属主拒绝；包哈希和凭据字段拒绝；只读会话恢复；权重为 0 的 hybrid 不建库。断言使用伪造连接和内存快照，没有付费构建，也没有重嵌。

带原文的重放命令（原文留在本地，不入库）：

```bash
uv run python evaluation/offline_retrieval_probe.py \
  --questions <questions.jsonl> \
  --sources <hash-checked-sources> \
  --source-manifest <manifest.json> \
  --output evaluation/offline_probe_rfc_counts.json
```

## 下一次最小获批试验

1. 同一次 `--live --retrieval-only --retain-index`（或同时 `--export-index`）构建语料。
2. 用 `--reuse-index` 再跑只读检索，确认语料嵌入调用数为 0。
3. 只把 `RETRIEVAL_TOP_K=24`，不要为了这次对比再嵌 14636 块。报告九题的 span 覆盖和官方 support_hit。文档 Hit@10 已经是 1，不作为成功标准。
4. 若覆盖几乎不动，停止加大 k，也不要为这九题改切块。词法权重保持 0，除非另有一组显式 `RETRIEVAL_LEXICAL_WEIGHT>0` 的只读对比；权重仍为 0 的 hybrid 结果作废。

## 给集成方

未改 `evaluation_v2`、`evaluate_report_v2`、`chat.py`、prompts、citations。跨度覆盖率和 set-cover 下界若要进评测报告，属于报告层。复用检索仍调用现有 `evaluate_case`，查询向量和 `chat.retrieve_vectors` 的接口没有改。本分支不合并其他代理的提交。
