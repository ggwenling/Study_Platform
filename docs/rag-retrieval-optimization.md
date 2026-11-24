# 检索索引复用与离线取舍

本文件是检索侧这一轮的记录。基线提交是 `ed62865d8a3a5dd46c6a5531367f36fcccec1483`。没有跑 Live，没有调用 chat、embedding、rerank 或 vision API，没有改冻结题、既有评测报告或人工状态。

## 交付

`study-eval-rag` 默认仍在 `finally` 里 `DROP` 新建的 `study_eval_<32 hex>_test` 库。保留和导出都要显式打开：

- `--retain-index`：成功结束后留下该测试库，并在输出目录写 `index_manifest.json`。失败仍删除。
- `--export-index DIR`：写出不含连接串和密钥的包（`manifest.json`、`records.jsonl`、`package.sha256`）。向量来自已经写入的行，不再次嵌入。
- `--reuse-index PATH`：跳过语料嵌入，也不写入被复用的索引库。目录包会导入到新的 `study_eval_*_test`；单独的 manifest 会连回它记录的那个库，连接参数带 `default_transaction_read_only=on`。`--live --reuse-index` 可以生成回答；加上 `--retrieval-only` 则仍然只检索。查询嵌入、改写和生成的用量记在 `query_costs`，与语料嵌入分开。本增量没有调用付费接口。

复用前要同时满足：库名匹配 `study_eval_<32 hex>_test`；当前角色拥有该库；库名不等于应用库，也不等于 `TEST_DATABASE_URL` 指向的共享测试库；manifest 版本、schema revision、知识库、所有者、资料哈希、索引版本、embedding 模型、1024 维与库内快照一致。不一致就拒绝，不检索。导出目录的 `package.sha256` 不符，或清单出现凭据字段，也拒绝。`binding=database` 的单独 manifest 没有包哈希，不核对 `package.sha256`。

`default_transaction_read_only=on` 只是这次连接的会话参数，不是只读角色。库属主可以在会话里关掉它，也可以另开连接写入。它不能单独当成防篡改或硬只读。

离线 probe 的 `filename` 只打开 `--sources` 里的一个常规文件。官方清单的名字是单层文件名，例如 `rfc-9110.txt`、`nist-46.pdf`。绝对路径、`..`、路径分隔符和符号链接直接拒绝，不打开、不计算哈希。索引导入不走这个函数。

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

本轮结果：`ruff check` 通过；上述 pytest 为 `79 passed`。单测覆盖：默认仍删除；`--retain-index` 成功才保留、失败仍删除；创建权限不足不删除；复用路径不调用 `seed_corpus` 或 `embed_texts`；schema、模型、维度、资料哈希、所有者不符拒绝；库名 allowlist；应用库和共享测试库拒绝；角色不是属主拒绝；包哈希和凭据字段拒绝；只读会话恢复；权重为 0 的 hybrid 不建库；probe 的绝对路径、`..` 和符号链接不读取目录外文件。断言使用伪造连接和内存快照，没有付费构建，也没有重嵌。

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

来源提交 `44295ecd` 本身没有改 `evaluation_v2`、`evaluate_report_v2`、`chat.py`。集成分支在它之外另有语料清单契约、评测 `prepare_generation`，以及标题和正文里 `[S#]` 的展示改写。复用检索仍调用现有 `evaluate_case`。查询向量在获批的 `--live` 运行里生成；语料向量不再生成。默认 `top_k` 10、`lexical_weight` 0 不改。这不是向量质量收益，也不是完整的提示词注入防护。

## P4 只读复用入口

在 `backend` 执行。`TEST_DATABASE_URL` 必须是 PostgreSQL，库名以 `_test` 结尾，角色有 `CREATEDB`，并且连接参数里不能带 `dbname`、`service` 或 `servicefile`。管理连接会切到名为 `postgres` 的库，只创建或删除 `study_eval_<32 位十六进制>_test`。这个名字不能等于应用库，也不能等于 `TEST_DATABASE_URL` 指向的共享测试库。不要把生产 DSN 或 API 密钥放进仓库、日志或 cloud。密钥只留在 box。

第一次构建会调用语料嵌入，只在 box 里做：

```bash
uv run --no-sync study-eval-rag \
  --dataset <facts.jsonl> \
  --strategy compare \
  --retrieval-only \
  --live \
  --retain-index \
  --export-index <export-dir> \
  --output <new-output-dir>
```

成功后输出目录有 `index_manifest.json`，`binding` 为 `database`。导出目录有 `manifest.json`、`records.jsonl` 和 `package.sha256`，`binding` 为 `export`。失败仍删除本次新建的库。默认不加 `--retain-index` 时，成功也会删除。

之后的复用跳过语料嵌入，也不往被测索引里写。查询嵌入、改写和生成仍会在不带 `--retrieval-only` 的 `--live` 里发生，用量见报告里的 `query_costs`，不是 `corpus_embedding`。`--retrieval-only` 仍然只检索。`--strategy hybrid` 或 `hybrid_compare` 在 `RETRIEVAL_LEXICAL_WEIGHT` 为 0 时直接拒绝。

```bash
uv run --no-sync study-eval-rag \
  --dataset <同一 facts.jsonl> \
  --strategy compare \
  --retrieval-only \
  --live \
  --reuse-index <output-dir>/index_manifest.json \
  --output <另一个新目录>
```

`--reuse-index` 指向导出目录时，会把包导入一个新的 `study_eval_*_test`，不调用嵌入 API。不带 `--retain-index` 时导入库在结束时删除。只校验包、不连库时去掉 `--live`。

清单契约：`manifest_version=1`，`kind=study_eval_index`，`embedding_dim=1024`，`embedding_profile` 等于 `<embedding_model>:1024`，`pipeline_profile=semantic-v3`，`schema_revision` 等于当前 Alembic head。库内只能有一个评测用户，知识库和资料都属于该用户，资料哈希、索引版本、块数和向量维度必须与清单一致。复用前数据集文件和 `materials.json` 的 SHA256 也要一致。导入后的 `study.alembic_version` 必须等于代码 head；不一致就拒绝检索。

这些检查不是绝对安全边界：

- 只读来自连接参数 `default_transaction_read_only=on`，不是专用只读角色。库属主可以在会话里执行 `SET TRANSACTION READ WRITE` 后写入。默认会话会拒绝写；关掉开关之后不再拒绝。
- `binding=database` 的单独清单不核对 `package.sha256`。这个哈希只在 `binding=export` 的目录包上检查。
- 导出的 `records.jsonl` 包含块原文和向量，不只是清单。
- 清单拒绝的是 `password`、`api_key`、`database_url`、`authorization`、`secret`、`token`、`access_key` 这些字段名。它不审查字段值里有没有秘密。
- 离线 probe 的 `filename` 现已只读 `--sources` 里的单层常规文件。绝对路径、`..`、分隔符和符号链接拒绝且不打开、不哈希。这不是完整的提示词注入防护。D 尚未复核这一增量。

离线 Evaluation V2 的全库结构结论另有契约：`source=corpus_manifest`、`universe=corpus`、`complete` 与 `candidate_complete` 都为 true，`chunk_count` 和 `documents` 与行一致。仅有 `complete: true` 不是全库证明。schema 是 `evaluation-v2.3`。

CI 路径见 `.github/workflows/ci.yml`：Python 3.12、PostgreSQL 16、pgvector 镜像，`tests/ci_bootstrap_postgres.py` 之后跑 Ruff 和全量 Pytest。数据库集成需要 `RUN_DB_TESTS=1` 和上面的 `TEST_DATABASE_URL`。本地最小验证是 `tests/integration/test_eval_index_reuse_db.py`：保留、只读复用、schema 不符拒绝、属主和库名边界、失败后库被删除。向量是手工 1024 维，不重建 14636 块，不调用模型。

## 评测嵌入失败与续跑

候选基线是 `783e680b3682058786a4f6511ea6010b01a42584`。这次只改评测 CLI 的失败记录、显式复用答题和可选嵌入缓存。不改模型参数、检索、切块、gold、默认 top_k、词法权重、产品超时，也不改评测 V2 或生产 chat。释放门禁仍是 false。`783e680` 里的 worker / UX 修复不在这次增量里重做。

`ac8d8bf` 上两次有限 50 题构建都在语料嵌入阶段以 `Evaluation failed (ApiError)` 结束。两份日志只有 955 和 907 字节，没有 HTTP 状态、错误码、Retry-After、RequestID 或批次号。`provider_error` 原本会映射 `model_auth_failed`、`model_quota_unavailable`、`model_service_error`、`model_timeout`、`model_response_invalid`，旧 CLI 把这些也吞掉，报告停在 `status=running`。这不能据此断定是超时、429 还是长文本异常。失败后的小探测 HTTP 200 也不能排除账户级间歇 429。

第一次最后一行 `Indexed` 是 nist-186-5；rfc-9110 源文件已经复制（502941 字节）但没有 `Indexed`。第二次最后 `Indexed` 是 nist-202；nist-186-4 已经复制但没有 `Indexed`。复制源文件不是成功嵌入批次。只有返回并校验通过的向量批次才记入 `corpus_embedding`，整篇写完才进入 `indexed_documents`。

官方材料（父进程已核对，本增量未再付费请求）：

- [OpenAI 兼容 Embedding](https://help.aliyun.com/en/model-studio/embedding-interfaces-compatible-with-openai) 与 [qwen3.7-text-embedding-flash](https://help.aliyun.com/en/model-studio/qwen3-7-text-embedding-flash)：flash 单次最多 20 行，和产品批次一致，不改批次大小。
- 上述 embedding 页没有服务端 timeout 秒数。因此不改 `model_timeout_seconds`（仍是 60），也不把 `ApiError` 归因成超时。
- 错误码指引区分 `Throttling.RateQuota` 和 `Throttling.AllocationQuota`，并建议降频、有界退避，同时看 HTTP、code、RequestID。所读错误页没有 Retry-After。响应里如果带了可解析的秒数且不超过 30 秒，才按它等待；解析不了或更长就停止，不把原始 header 写进日志。
- 官方 RPM/TPM 不是本账号已经保证的额度。`Throttling.AllocationQuota` 不重试。

失败报告是 `status=failed`。`failure` 只含阶段、异常类名、本库错误码、映射码、固定的官方额度码或 `unknown`、HTTP 状态、失败种类、用量是否已知、是否可重试、Retry-After 是否存在、状态是 `absent` / `wait` / `stop`、0–30 秒的等待值和尝试次数。`request_id` 固定写成 `unknown`，不保存原始值、前缀或后缀。进程退出文本带上异常类名。文件缺失、权限、IO、JSON/编码解析和索引校验是 `local_missing` / `local_permission` / `local_io` / `local_parse` / `local_verification`，`mapped_error_code` 为空，不用 `provider_error` 收成模型错误。缺文件或坏的 `materials.json` 在创建库和语料嵌入之前就失败。

`provider_code` 只保留完全相等的 `Throttling.RateQuota` 和 `Throttling.AllocationQuota`。短码、全字母密钥、UUID、以及其他自由文本都写成 `unknown`，即使它嵌在 body 或 `error.code` 里。合法 UUID 不算“不是密钥”。记录路径不读取环境里的真实凭证来做比对。不写自由文本、body 或 header。

Retry-After 在缩短字段之前决定。没有 header 的瞬时错误才按有界退避再试。可解析且不超过 30 秒才等待。大于 30 秒、未来 HTTP 日期超过 30 秒、过长整数、NaN/Inf、或 header 存在但无法解析，都是 `stop`，不能当成没有 header 立刻重试。官方 RPM/TPM 不是本账号额度。

语料嵌入这一层的上限是每个批次最多 3 次调用（1 次初次加 2 次额外尝试）。两次间隔里，已接受的 Retry-After 各自不超过 30 秒，所以这一层额外等待不超过 60 秒。没有 header 时这两次间隔是 0.5 秒和 1 秒。这是这个循环自己的上限，不是缩减账号额度或其他全局 budget。

`seed_corpus` 把一次构建放在同一个事务里。`Indexed` 只是该篇的向量和行已经放进事务，不是可复用索引的提交点。后面的失败会整笔回滚，报告里 `indexed_documents` 为空、`index_retained` 为 false；已校验的批次只留在显式缓存的 journal 里。缓存或源文件副本不能当成完整索引。显式 `--retain-index` 要等发布校验通过才把 `succeeded` 设为 true 并留下库；没通过就删除本次新建的库，不泄漏未验证库。默认失败清理不变。只读会话参数和旧 manifest 可伪造 `corpus_verified` 仍是未关闭的边界，这次不宣称已经解决。

失败批次的 `usage_status` 为 `unknown` 时，费用就是 unknown，不能写成 0。`currency_total` 保持 `unknown`。探测账本里的小数不能当成这两次语料嵌入的总费用。

`--embedding-cache DIR` 是显式开关，默认关闭。目录必须是当前用户的 `0700` 目录，不能是符号链接。每个批次文件是 `0600`，原子替换。键绑定源文件 SHA256、`semantic-v3`、供应商 URL 指纹、模型、`<model>:1024`、维度和这 20 条输入的 SHA256。不匹配、损坏、`complete` 不是 true、或权限过宽的文件不当作命中，重新计算。缓存里是原文的向量，只留在本机。命中批次 `outcome=cached`、`calls=0`、`new_corpus_charge=false`，不记成这次的新语料费用。失败仍然只删除这次自己创建的库，不靠留下未验证的共享库。

同一进程里，瞬时连接、超时、HTTP 5xx 和 `Throttling.RateQuota` / 未标明的 429 最多再试 2 次，加上初次调用一共 3 次。400 一类的无效请求、维度/条数/NaN、认证失败和 `AllocationQuota` 不重试。产品客户端 `max_retries` 仍是 0。上面的次数和等待是这一层的上限，不表示全局 budget 被缩小。

```bash
uv run --no-sync study-eval-rag \
  --dataset <facts.jsonl> \
  --strategy compare \
  --live \
  --embedding-cache <private-cache-dir> \
  --output <new-output-dir>
```

中断后用同一个 `--embedding-cache` 再跑一次。已核对的批次不再调用嵌入；缺的批次才调用。一次成功构建并 `--retain-index` 或 `--export-index` 之后，答题复用是：

```bash
uv run --no-sync study-eval-rag \
  --dataset <同一 facts.jsonl> \
  --strategy compare \
  --live \
  --reuse-index <output-dir>/index_manifest.json \
  --output <另一个新目录>
```

这里不要再加 `--retrieval-only`，除非这次只想检索。报告里的 `index_reuse.corpus_embedding_calls` 应为 0。

费用日志示例（数字是单测夹具，不是那两次失败的真实账单）：

```json
{
  "status": "failed",
  "indexed_documents": [],
  "index_retained": false,
  "failure": {
    "stage": "corpus_embedding",
    "failure_kind": "http_429",
    "http_status": 429,
    "provider_code": "Throttling.RateQuota",
    "mapped_error_code": "model_quota_unavailable",
    "request_id": "unknown",
    "retry_after_state": "wait",
    "usage_status": "unknown",
    "attempts": 3
  },
  "corpus_usage": {
    "accepted_new_tokens": 120,
    "accepted_new_tokens_excludes_failures": true,
    "cached_new_corpus_charge": false,
    "failed_cost": "unknown",
    "currency_total": "unknown",
    "partial": true
  }
}
```

`query_costs` 里的 `allow_usage` 以前只留下顶层 token。LangChain 记在 `usage_metadata.input_token_details.cache_read` 的命中数，以及 DeepSeek 的 `prompt_cache_hit_tokens`、OpenAI 兼容的 `prompt_tokens_details.cached_tokens`，会被丢掉。丢掉以后不能把缺字段读成 cache hit = 0，也不能据此把全部 input 当成 miss。

`244f91e` 上已结束的真实 campaign 没有改写。独立复核看到的已落盘调用是 input 143173、output 35215、cache_read 72816。当时报告里的 DeepSeek USD 0.0852099 只是把这些 input 全部按 miss 估算的上界，不是“命中数为 0”的测量，也不是 provider 账单。同一批已记录 cache 数按公开高峰价的另一种估算是 USD 0.063801996；已记录 embedding 618894 tokens 的 DashScope 估算是 CNY 0.07736175。本仓库不写入这些费率，也不做汇率换算。`ac8d8bf` 两次失败语料调用和中断的 REAL-013 可能重复调用仍然 unknown，不能补 0，也不能并进一份历史总价。

新的 `recorded_usage` 只加状态仍是 `known` 或显式 `estimate` 的安全非负整数。同一题的共享 query embedding 只计一次。cache 字段缺失时该组是 `unknown`。`input_tokens` 和 `prompt_tokens` 冲突、`cache_read` 或显式 miss 大于 input、两者之和不等于 input、或同一计数的来源不一致，这一行上已经出现的 cache 计数都改成 `inconsistent`。原整数留在行上，只供诊断，不能再当 known。组里只要有一行冲突或缺字段，就不发布其余行的部分合计。provider 给出的 miss 在约束成立时记为 known；只有 cache_read 和单一 input 都已知、且 cache_read 不超过 input、又没有显式 miss 时，才写 `cache_miss_estimate`。`currency_total` 和 `unrecorded_calls` 仍是 `unknown`。`total_tokens` 不与 input+output 对账。`cache_creation` 只在它是非负整数时留下，不进入 cache 合计。

本地 PostgreSQL 16.15 与 pgvector 0.6 上，手工向量已经核对：25 条可区分向量在显式 `top_k=24`、`candidate_k=48` 下返回前 24 名且顺序正确，默认仍是 10/20。复用答题走真实检索、改写和生成代码，只替换 HTTP transport；回答非空，`[S1]` 绑定第一条证据，查询嵌入、改写和生成的调用与 usage 都在，语料没有再次嵌入，索引行数不变。这不是真实 provider 的名次或费用。

未验证：真实 provider 上这两次 `ApiError` 的 HTTP 状态和错误码；真实响应里有没有 Retry-After；真实向量的 top_k=24 名次；复用答题的真实查询/生成费用；缓存恢复一次完整 14636 块索引。原始 request id 不再落盘，所以 ID 的真实形状不再决定记录是否安全。真实 provider 仍未调用。旧 manifest 可自填 `corpus_verified`，以及只读只是会话参数，这两项仍未关闭。

## 实验切分 semantic-v4

`semantic-v3` 仍是缺省。入库任务不带 `pipeline_profile`、评测不传 `--pipeline-profile` 时，路径和 locator 与原来一致。显式选择 `semantic-v4` 才会：

- 把 text/plain 里被空行隔开的连续非空行收成一段，locator 用原文的 `start_line` / `end_line`。
- 在超过 800 字、需要按句切开时，不把单个换行当成句号。`。！？!?；;` 仍是句末。句点遇到小数、字母缩写（如 `e.g.`、`U.S.`、`Dr.`）不切。
- 块如果只是段落或页面的一部分，locator 收窄到该片段的行或 `page` + `start_char` / `end_char`。对不上原文就不写行范围。

限制要一起看：一块最多 800 字（代码、表格、公式原子仍到 1600）。更长的句子会切开。有空白可锚定时，下一块最多重叠 120 字；没有空白的长句按宽度切开，不重叠。空行仍是段落边界，短段不会为了“消灭续行”被拼成更长的段。页与页不合并。块数变少只说明几何变了，不是质量结论。

新 profile 只能从源文件重新抽取。对已经按行存下的 semantic-v3 section 做 rechunk，仍发布 semantic-v3，不会把相邻行拼起来冒充 v4。已经是 v4 的索引再 rechunk 仍是 v4。未知名字拒绝，不建索引。导出、导入和复用要求清单与每一条索引行的 profile 相同；旧缓存的 identity 含 profile，不能当作 v4 命中。

因此换到 v4 要整份重抽、重嵌。旧库和旧向量不动。本轮没有重建，也没有接模型。REAL013 使用的 FIPS 46 与本地 `33113108…` 那份不是同一文件，合成折行只证明切分机制。
