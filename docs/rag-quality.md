# 事实问答质量回归

本轮目标是中文事实查找和同义提问。HTTP 请求、SSE 事件及已有引用字段类型不变，不重建现有索引，不更换模型或分块策略。

向量检索还可单独输出 Recall@k、nDCG@k、MRR、Hit@k，详见 [检索评测指标](retrieval-evaluation.md)。

## 运行策略

`RAG_QUERY_REWRITE_ENABLED` 默认 `false`。本次真实评测没有新增事实组命中，未达到启用门槛，所以日常问答继续使用原问题检索。设置为 `true` 并重启 API 可显式试验候选；关闭并重启后恢复基线。评测命令的 `--strategy` 独立于日常开关。

候选只作用于有索引的 QA：

1. 使用现有聊天模型，根据当前问题生成一个等义问题；不传历史或资料正文。只接受一个 JSON `query` 字符串，保留显式数字、编号和引号内实体。最多 256 输出 token，8 秒超时，SDK 不重试。
2. 空结果、相同问题、无效格式、控制字符、编号变化、超时或模型错误回退原问题；用户取消不会被当作普通失败吞掉。
3. 原问题始终保留，与有效改写在同一批请求中生成向量。两路仅检索本轮已固定的索引，沿用默认相似度阈值 0.35。
4. 按 chunk ID 去重，用 `Σ1/(60+排名)` 融合排序，同分优先原问题排名，再按 ID 排序。双路融合最多保留 6 个片段，单路检索使用 `RETRIEVAL_TOP_K`。最终回答仍以原问题为准。RRF 公式见 [Cormack 等的原论文](https://cormack.uwaterloo.ca/cormack/cormacksigir09-rrf.pdf)。

改写前后重新检查轮次、租约和资料可用性。摘要、空库和已结束轮次重放不增加改写调用，阶段仍归入现有 `searching`。每轮最多增加一次改写调用，没有额外重排或模型裁判。两句向量化虽是一批请求，输入量仍增加；原本直接拒答的问题若找到了证据，也可能产生正常回答费用。SSE 原有 usage 仍是回答用量；分阶段评测用量见本地报告。

## 评测命令

在 `backend` 中运行；新增入口需先执行 `uv sync --locked`。Windows 如果旧 `study-api.exe` 正在运行，先正常停止对应应用再更新入口，或者直接使用 `uv run --no-sync python -m study_platform.cli.evaluate_rag`。

```powershell
# 只验证资料标注，不连接数据库、不调用模型。
uv run --no-sync study-eval-rag

# 真实对照：使用现有模型账户，会产生费用。
uv run --no-sync study-eval-rag --strategy compare --live --output ../.local/rag-eval/run-01

# 使用单独准备的私有数据集；不会扫描或导入整个资料目录。
uv run --no-sync study-eval-rag --dataset ../.local/private-eval/facts.jsonl --strategy compare --live --output ../.local/rag-eval/private-01

# 核对 report.json 并填写 review.json 后，离线重算门槛；不调用模型。
uv run --no-sync study-eval-rag --output ../.local/rag-eval/run-01 --review ../.local/rag-eval/run-01/review.json
```

输出目录必须是新目录，默认位于忽略提交的 `.local/rag-evaluation/<随机ID>`。私有问题、原始回答、片段及复核文件不要放到 Git 或公开目录。

真实评测要求显式配置的 PostgreSQL `TEST_DATABASE_URL` 指向 `_test` 库，并拒绝覆盖数据库目标的连接参数。工具使用有 CREATEDB 权限的测试操作角色，新建随机 `study_eval_*_test` 库并验证实际目标，再在新库执行迁移、准备资料。正常退出和异常收尾只删除本次成功创建的库，应用库与已有其他数据库不变。硬杀进程可能来不及清理，应核实遗留库归属后人工处理。

原件保存在本次输出目录的 storage 中。私有文件仅从 `materials.json` 明确指定的本地路径复制；云端占位文件会拒绝读取，原文件不修改。普通 pytest 不发起付费模型调用。

## 数据与报告

固定样例在 `backend/evaluation`：24 个事实组，每组两种问法，共 48 题。其中 16 组可回答、8 组在目标库资料不足；按组固定划分为两个各含 8 个可回答组、4 个无答案组的集合。另一知识库保存部分干扰答案，检验范围隔离。资料全部为虚构样例，DOCX、TXT、PNG、JPEG、扫描 PDF 在评测时生成，并经过现有提取和分块逻辑。

数据集由 `facts.jsonl` 和同目录 `materials.json` 组成。每题必需字段：

- `id`、`fact_group`、`split`（calibration/heldout）、`knowledge_base`、`question`、`answerable`。
- `expected_facts`：必须出现的事实要点，每项可以提供多个等价写法；仅用于机械提示。
- `support`：支撑事实的 document ID 和 quote。多个 support 项要求全部命中；同一个 quote 可用字符串数组表达等价原文，任一处命中即可。

可回答题必须有事实与原文标注，原文须属于本题知识库。同一个事实的所有问法不得跨校准集与保留集。不能用整段原文直接充当问题，也不能只凭文件 ID 相同就判定命中。图片样例当前使用 Windows 微软雅黑字体渲染。

两种策略共享同次生成的原问题向量、语料和索引快照。报告保留模型配置、数据及原件指纹、返回证据、回答、引用编号、分阶段耗时与用量。`summary.json` 按事实组统计所有问法都命中的比例、平均倒数排名、调用次数和上下文大小；共享向量请求按题去重计数。源资料建立索引的用量在新报告的 `corpus_embedding` 单列；未获得 usage 的步骤明确视为未知，不能当作免费。

`fact_aliases_found` 不证明答案全部正确；`citation_valid` 只证明引用编号合法；`refusal_marker` 也不证明没有夹带无依据内容。引用是否支持全部结论，必须对照原文复核。

## 启用门槛

`review.json` 默认没有已确认的结论。逐题检查事实正确性与引用支持，填写 reviewer，并由人工显式设置 `human_confirmed=true`。复核文件绑定完整报告指纹，其他运行的复核不能混用；这是一项操作确认，不是人员身份认证。

`complete_comparison` 表示比较评测已完成，且预期清单里的每题都恰好生成了两种策略的结果。旧报告和中断报告可以离线查看，如需判定是否能默认启用，请重新完整运行比较评测。

默认启用必须同时满足：

- 人工复核完整，实际 DOCX 对照无退步。
- 保留集至少新增一个完整命中的事实组，不损失基线已命中的组。
- 基线正确答案无退步；无答案错误作答、范围越界、无效引用或不受依据支持的结论不能通过。
- 取消、重放及失效引用的自动回归通过。

工具只生成门槛结果，不修改 `.env` 或自动开启功能。本轮结果见 [质量验收记录](verification-rag-quality.md)。
