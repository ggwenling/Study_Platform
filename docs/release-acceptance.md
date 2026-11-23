# KnowLoop P5 release 门禁、隔离环境与回滚

状态：**RELEASE_READY = false**。本文是给主集成的准备稿，不是合并、打 tag 或部署许可。

核对快照：`3bd2585e433105e573556add717fab22586e3f62`（`cursor/deep-testing-prep-9b8e`）。最终集成 SHA 尚未冻结。未在最终 SHA 上复跑的项目保持 **NOT_VERIFIED**，不能记成 PASS。

本轮没有 merge、tag、deploy，没有调用模型 API，没有连接生产库，也没有改 A/B/C 产品代码。

## 1. 快照事实（本轮已核对）

| 项 | 结果 |
| --- | --- |
| HEAD | `3bd2585e433105e573556add717fab22586e3f62` |
| 相对 `codex` `20a20b96` | 12 个提交；仓库没有 tag |
| PR #19 | 开着，base `codex`，head 就是这个 SHA |
| 版本 | `backend/pyproject.toml` `0.1.0`；FastAPI `version="0.1.0"`。没有独立 release 版本文件 |
| Alembic | 单一 head `0016_rerank_and_kb_retrieval`，`0001` 到 `0016` 线性 |
| 检索默认 | `retrieval_top_k=10`，`retrieval_candidate_k=20`，`retrieval_lexical_weight=0.0`，`rag_query_rewrite_enabled=false`（`backend/src/study_platform/core/config.py`） |
| Evaluation V2 | `schema_version=evaluation-v2.2`；`gate.eligible_for_default` 与 `manual_review_complete` 固定 false |
| 本机端口 5432 / 55432 / 8000 / 5173 / 18000 / 15173 | 全部 closed |
| 本机工具 | Python 3.12.3、Node v22.23.4。没有 uv、项目 venv、psql、docker、frontend `node_modules` |

`/health/ready` 要求 `AUTH_JWT_SECRET` 至少 32 字节，`study.alembic_version` 与代码 head 完全一致，并且 `STORAGE_DIR` 可写。代码回退和 schema 必须同一代；只退其中一个，就绪检查会失败。

GitHub Actions 在这个 SHA 上的最新一次运行是 [45239508776](https://github.com/ggwenling/Study_Platform/actions/runs/45239508776)：

| Job | 结论 | 时间（UTC） |
| --- | --- | --- |
| Backend `137920197642` | success | 18:32:58–18:34:42 |
| Frontend `137920198359` | success | 18:32:59–18:33:43 |

这是**该快照的 CI 结论**，不是 release PASS。日志末尾是容器拆除，没有读到 pytest 汇总行，所以通过条数记 NOT_VERIFIED。更早一次运行 `45239495180` 同样 success，不另计。

## 2. 源 PR 怎么进主集成

不要把 PR #20 或 #21 merge 进 `cursor/deep-testing-prep-9b8e`。两边的 parent 都是 `ed62865`，而主分支在那之后还有 V2 提交。

| 源 | 处理 |
| --- | --- |
| PR #20 `58dcc338` | 已在主分支，cherry-pick 为 `3bd2585`。两边 `git patch-id --stable` 都是 `418cd5a33efd9375d4ca6b62b103258fdb513d07` |
| PR #20 增量 `5e459a4ac32454181471873811819624b0d0cdee` | **不在 HEAD**。标题是 citation token 中和。只动 `chat.py`、`test_answer_quality.py`、`docs/rag-answer-quality.md` |
| PR #21 `44295ecd5f0f63a5cf60e1f03af7ae7eb49d0d20` | **不在 HEAD**。索引复用，9 个文件，约 +3087/−54 |

在快照上用 `git merge-tree` 模拟（没有改工作区）：

- 把 PR #20 **整支**合并到 HEAD：3 处冲突，`chat.py`、`test_answer_quality.py`、`docs/rag-answer-quality.md`。
- 只 cherry-pick `5e459a4`（merge-base 用它的 parent `58dcc338`）：0 冲突。
- 只 cherry-pick `44295ecd`（merge-base `ed62865`）：0 冲突。

父主协调者在**当时的**集成 HEAD 上重做模拟再拣选。HEAD 继续前进后，这次 0 冲突作废。拣选命令：

```bash
git fetch origin
git switch cursor/deep-testing-prep-9b8e
git merge-tree --merge-base=58dcc338b1cb08b3a19deb547e62836f4580d8a2 \
  HEAD 5e459a4ac32454181471873811819624b0d0cdee
git merge-tree --merge-base=ed62865d8a3a5dd46c6a5531367f36fcccec1483 \
  HEAD 44295ecd5f0f63a5cf60e1f03af7ae7eb49d0d20
# 两段输出都没有 CONFLICT 行时，才由 A 执行：
git cherry-pick 5e459a4ac32454181471873811819624b0d0cdee
git cherry-pick 44295ecd5f0f63a5cf60e1f03af7ae7eb49d0d20
```

拣选之后关闭源 PR，不要再按 Merge 按钮。A 自己的 V2 manifest / 证据判空提交继续留在主分支，不从源 PR 覆盖。

## 3. 最终 SHA 才能记 PASS 的门禁

证据至少要有：完整 SHA、命令、退出码、日志路径。缺一项就是 NOT_VERIFIED。

| ID | 门禁 | 命令 | 本快照 |
| --- | --- | --- | --- |
| G0 | 父主冻结一个集成 SHA，并声明 PR #20/#21 已按第 2 节处理且没有整支 merge | 父主书面给出 SHA | NOT_VERIFIED |
| G1 | 该 SHA 上 Backend 与 Frontend 最新 check 均为 `completed/success`，且不是被取消的并发运行 | `gh run list --branch cursor/deep-testing-prep-9b8e --commit <SHA>` | 仅 `3bd2585` 有 success。最终 SHA 未核 |
| G2 | 本地后端单测与 Ruff | 见下方命令 | NOT_VERIFIED（无 uv / venv） |
| G3 | 独立 `*_test` 上的 DB 集成、迁移往返、契约 | 见下方命令 | NOT_VERIFIED。GitHub job 成功不等于本机复跑 |
| G4 | 权限隔离：角色、跨用户、认证 | 见下方命令 | NOT_VERIFIED |
| G5 | UX：Vitest、`npm run build`，加上最终 SHA 的浏览器走查 | CI 只覆盖前两项 | 浏览器 UX NOT_VERIFIED |
| G6 | 真实质量。P4 50Q 必须跑在最终 SHA 上，并有花费记录 | 不在本环境默认执行 | NOT_VERIFIED。禁止用 100Q dry 或 V2 离线报告代替 |
| G7 | D `bc-5fe995c3-bdee-59df-bfa1-93264c3e2d65` 对最终 SHA 的 blocker 为 0 | 父主附上 D 的结论和 SHA | NOT_VERIFIED |
| G8 | gold 与第 4 节哈希一致 | `sha256sum` 对照 | 本快照哈希已记录。最终 SHA 未对照 |
| G9 | 默认 top-k 10、candidate 20、lexical 0、rewrite false | 读 `config.py` 与运行中的环境 | 源码默认已核对。运行中环境 NOT_VERIFIED |
| G10 | 隔离库备份恢复演练 | 第 6 节，只打 `study_release_*_test` | NOT_VERIFIED |

G2、G3 在 `backend` 执行。数据库 URL 必须来自第 5 节的 gitignored env，端口 55432：

```bash
uv run ruff check .
uv run pytest tests/unit -q
RUN_DB_TESTS=1 uv run pytest tests/integration/test_migration_roundtrip.py tests/integration/test_database_schema.py tests/integration/test_auth.py tests/integration/test_frontend_contract.py -q
RUN_DB_TESTS=1 uv run pytest -q
```

G4 在同一 env 下：

```bash
uv run python evaluation/deep-test/harness/prove_isolation.py
uv run python evaluation/deep-test/harness/seed_accounts.py
uv run python evaluation/deep-test/harness/run_cross_user_isolation.py
```

`tests/ci_bootstrap_postgres.py` 会 `DROP DATABASE study_platform_test` 并覆盖仓库根 `.env`。release 演练不要调用它。

G6 另计。V2 报告自己写明 `eligible_for_default=false`，`live_comparison=NOT_VERIFIED`，空 support 记 `empty_gold`，不能当成证据命中。50Q 真实验收需要父主提供：最终 SHA、题目清单哈希、输出目录、token 或 `unknown` 花费、以及 D 没有质量 blocker。本轮不调用付费 API。

G8 对比命令：

```bash
sha256sum \
  backend/evaluation/deep-test/questions.jsonl \
  backend/evaluation/deep-test/materials.json \
  backend/evaluation/deep-test/isolation_canaries.json \
  backend/evaluation/retrieval_mini.jsonl \
  backend/evaluation/facts.jsonl \
  backend/evaluation/materials.json
```

任一 gold 文件变化都要在 release 记录里写明原因。未解释的变化是 FAIL，不是“稍后补”。

## 4. 本快照 gold 指纹

| 文件 | 行数 | SHA-256 |
| --- | --- | --- |
| `backend/evaluation/deep-test/questions.jsonl` | 100 | `bb6e7308a60251a620286f450d5fd5a87960b05e4b22e000b24dfa98f250ff3c` |
| `backend/evaluation/deep-test/materials.json` | — | `8b765f97c3e69ccd3bb96815f97afa8b336d652cc5c56071efc0c9ffe1e25ac9` |
| `backend/evaluation/deep-test/isolation_canaries.json` | — | `2b2fa56d73b40832bb42dd4ecaedfbdd377f5890b5a0a2b695732a050ec13bb0` |
| `backend/evaluation/retrieval_mini.jsonl` | 7 | `2f5cb0525fe361d87cffb06cf0acff189697e518b61e438ae8f829febcb218b8` |
| `backend/evaluation/facts.jsonl` | 48 | `c51c3114fbaa3919c233b5ef1f8ad9369b2e2b52cbb11ed1bf6c335c8ed99e7c` |
| `backend/evaluation/materials.json` | — | `4158dfe3ab7abf5435f08031f14d631e90295ba54cf935722595418de77bcf5f` |

100 题 dry 校验不是 P4 的真实 50Q。语料清单 `backend/evaluation/deep-test/fixtures/manifest.json` 的 `review_status` 仍是 `synthetic_not_human_curated`。

## 5. 隔离测试环境

示例：[infra/release/isolated-test.env.example](../infra/release/isolated-test.env.example)。复制后再改：

```bash
mkdir -p .local/release-gate/env .local/release-gate/storage .local/release-gate/backups .local/release-gate/run .local/release-gate/pgdata
cp infra/release/isolated-test.env.example .local/release-gate/env/release-gate.env
# 编辑副本：POSTGRES_PASSWORD、两个角色密码、AUTH_JWT_SECRET（至少 32 字节）
chmod 600 .local/release-gate/env/release-gate.env
```

`.gitignore` 已忽略 `.env.*` 和 `.local/`。不要把填好的副本提交。

硬边界：

- 主机只允许 `127.0.0.1` 或 `localhost`。
- 端口只允许 `55432`。5432、8000、5173 留给已有本机栈，release 脚本不占用、不杀掉。
- 库名只允许 `study_release_gate_test`、`study_release_restore_test`、`study_release_migrate_test`。
- `study-api` 写死 `127.0.0.1:8000`。隔离 API 用下面的 uvicorn 命令，监听 `18000`。
- 示例里的 `LEARNING_STUB_GRADER=true`，模型 key 为空。真实 50Q 不使用这份默认。

### 5.1 启动 Postgres（只绑本机 55432）

在已安装 Docker 的隔离机器上。密码来自 gitignored env，不写进命令历史：

```bash
set -a
source .local/release-gate/env/release-gate.env
set +a
test "$PGPORT" = "55432"
test "$RELEASE_DATABASE" = "study_release_gate_test"
docker run -d --name knowloop-release-gate --restart no \
  -p 127.0.0.1:55432:5432 \
  -e POSTGRES_USER -e POSTGRES_PASSWORD -e POSTGRES_DB=postgres \
  -v "$PWD/.local/release-gate/pgdata:/var/lib/postgresql/data" \
  pgvector/pgvector:pg16
docker exec knowloop-release-gate pg_isready -U postgres -h 127.0.0.1
```

然后只在这个端口创建三个 `study_release_*_test` 库、`study` / `study_graph` schema，以及 `study_release_app`、`study_release_graph`（`NOSUPERUSER NOCREATEDB`）。不要运行 `bootstrap_database.py` 或 `ci_bootstrap_postgres.py`：前者固定连 5432 并创建 `study_platform`，后者会删掉 `study_platform_test`。

迁移和 checkpointer 只打 gate 库：

```bash
cd backend
uv run alembic upgrade head
uv run study-setup-checkpointer
```

### 5.2 启动、健康、停机

```bash
set -a
source .local/release-gate/env/release-gate.env
set +a
cd backend
uv run uvicorn study_platform.main:create_app --factory --host 127.0.0.1 --port "$API_PORT"
```

另一个终端：

```bash
set -a && source .local/release-gate/env/release-gate.env && set +a
cd backend && uv run study-worker
```

Web：

```bash
set -a && source .local/release-gate/env/release-gate.env && set +a
cd frontend && npm run dev -- --host 127.0.0.1 --port "$WEB_PORT" --strictPort
```

健康：

```bash
curl -fsS "http://127.0.0.1:${API_PORT}/health/live"
curl -fsS "http://127.0.0.1:${API_PORT}/health/ready"
```

`/health/live` 为 `{"status":"ok"}`。`/health/ready` 为 `{"status":"ready"}` 才算数据库 head、密钥长度和存储探针同时通过。JWT 仍是 `CHANGE_ME` 时 ready 必须是 503，这是失败闭合，不是故障。

停机只结束自己记下的 PID，或按容器名停止 `knowloop-release-gate`。不要 `kill` 占用 8000、5173、5432 的进程，不要 `docker rm` 其他容器。

```bash
docker stop knowloop-release-gate
```

本轮上述启动命令均未执行：本机没有 docker、psql、uv，目标端口是 closed。

## 6. 回滚（三层，分开做）

禁止 `git reset --hard` 已推送分支、`git push --force`、`git push --force-with-lease`、移动或删除已发布 tag、`DROP DATABASE` 生产库或 `study_platform`。当前还没有 release tag，也不要在门禁完成前创建。

### 6.1 代码

尚未合并时，回滚就是不合并。不要为了“回到干净点”去改写 `codex` 或 PR #19。

合并之后，用新提交撤销，保留原 merge：

```bash
git fetch origin
git switch -c cursor/rollback-<release-sha12> origin/codex
git revert --no-edit <release-merge-commit>
git push -u origin HEAD
```

然后开 PR。revert 冲突就停下来手工处理，不要改成 reset。发布 tag 只追加、不移动。回滚成功后另打新 tag 指向 revert 结果。

只退代码、不退 schema 时，`/health/ready` 会因为 revision 不一致返回 503。这是预期保护，不是半成功。

### 6.2 Schema

head 与代码绑定。`0016` 的 downgrade 会删掉 `knowledge_bases.retrieval_settings`，并收紧 kind / job_type / purpose 检查；库里已有 `rerank` 或 `reindex` 时，downgrade 会失败。`0002` 在 `materials`、`conversations`、`knowledge_bases`、`ai_workflow_runs` 任一有行时直接 `RAISE EXCEPTION`，不丢数据。Alembic downgrade 不用 `CASCADE`，也不会拆掉 `study_graph`。

因此：

- 空的 `study_release_migrate_test` 可以做 `upgrade head` → `downgrade base` → `upgrade head`，对应 `tests/integration/test_migration_roundtrip.py`。
- 已有业务行的库不做 schema downgrade。数据回滚走 6.3 的备份恢复。
- `study_graph` 由 `study-setup-checkpointer` 单独管理。恢复应用数据时要同时处理这份 schema，否则会话 checkpoint 和 `study` 对不上。

### 6.3 数据备份与恢复

Windows 现有 `Backup-Synapse.cmd` 只打包 `study` schema 和 `STORAGE_DIR`，不含 `study_graph`，也不含 `.env`。个人本机可以继续用它，但它不是这次 release 门禁的完整回滚包。

隔离环境在 API 和 worker 已停止时打三份文件到 `.local/release-gate/backups/<stamp>/`：

```bash
set -a && source .local/release-gate/env/release-gate.env && set +a
stamp=$(date -u +%Y%m%dT%H%M%SZ)
dest=".local/release-gate/backups/$stamp"
mkdir -p "$dest"
pg_dump --host=127.0.0.1 --port=55432 --username=postgres \
  --format=custom --schema=study --no-owner --no-privileges \
  --file="$dest/study.dump" --dbname=study_release_gate_test
pg_dump --host=127.0.0.1 --port=55432 --username=postgres \
  --format=custom --schema=study_graph --no-owner --no-privileges \
  --file="$dest/study_graph.dump" --dbname=study_release_gate_test
tar -C .local/release-gate -cf "$dest/storage.tar" storage
(cd "$dest" && sha256sum study.dump study_graph.dump storage.tar > SHA256SUMS)
```

恢复先进入空的 `study_release_restore_test`，核对 `study.alembic_version` 和表数量，再决定是否切回 gate 库。切回前先对 gate 库再打一份 pre-restore 备份。`pg_restore` 失败就留下 restore 库和 pre-restore 备份，不删除源库。

```bash
createdb --host=127.0.0.1 --port=55432 --owner=postgres study_release_restore_test
pg_restore --host=127.0.0.1 --port=55432 --username=postgres \
  --dbname=study_release_restore_test --exit-on-error "$dest/study.dump"
pg_restore --host=127.0.0.1 --port=55432 --username=postgres \
  --dbname=study_release_restore_test --exit-on-error "$dest/study_graph.dump"
```

存储目录用临时目录解包，核对 `SHA256SUMS` 后再替换 `.local/release-gate/storage`。不要在校验前删除现有 storage。

## 7. 本轮执行记录

已做：

- 读取 CI workflow、健康检查、Alembic `0001`–`0016`、`config.py` 默认值、`ci_bootstrap_postgres.py` 的删库行为、`0002`/`0016` downgrade。
- 用 GitHub Checks API 核对 `3bd2585` 的 Backend/Frontend 结论。
- `git fetch` 三个源 SHA，计算 patch-id，并用 `git merge-tree` 模拟合并与 cherry-pick。工作区仍停在 `3bd2585`，源提交没有被应用。
- `sha256sum` gold 六文件。
- Python 探测六个端口均为 closed，并确认 Alembic 单一 head。
- 静态检查示例 env：无密钥、端口 55432、库名 `study_release_*_test`、检索默认与实验关闭。脚本与结果在本轮 artifact `release/snapshot-gate.py`、`release/snapshot-verdict.json`。

未做，全部 NOT_VERIFIED：

- 最终集成 SHA 的 CI、单测、DB 集成、权限隔离、浏览器 UX、真实 50Q、D review。
- 启动 Postgres、迁移、`/health/ready`、停机、备份恢复。
- 任何 merge、tag、生产部署。

## 8. 交给父主的清单

1. 冻结最终集成 SHA。其中包含 A 的 V2 manifest / 证据判空、cherry-pick `5e459a4`、cherry-pick `44295ecd`，且不包含对 PR #20/#21 的 merge commit。
2. 把该 SHA 的 Backend 与 Frontend check URL、pytest 末行、前端测试与 build 退出码附上。
3. 附上独立 `study_release_gate_test`（或 CI 服务容器）上的迁移往返、认证隔离和跨用户结果。
4. 附上 P4 真实 50Q 的 SHA、题目哈希、输出目录和花费记录。没有这次记录就保持 G6 = NOT_VERIFIED。
5. 附上 D 对同一 SHA 的 blocker 列表。非空则不能 release。
6. 重跑第 4 节 `sha256sum`。有差异就解释，否则 G8 = FAIL。
7. 门禁全部有证据之后，才由父主合并 PR #19、打不可移动的 tag。本文作者不执行这一步。

## 9. 拣入主集成之后

第 1–8 节是校验者在 `3bd2585e433105e573556add717fab22586e3f62` 上的记录。那些“不在 HEAD”的句子只描述那个快照。主集成已经拣入下面的提交，不要再执行第 2 节的 cherry-pick，也不要 merge PR #20、#21 或 #22。

| 源 | 主集成 |
| --- | --- |
| A 语料契约与评测 `prepare_generation` | `610fe59a702a991bb66508ba6a28381251e364b7` |
| B `44295ecd5f0f63a5cf60e1f03af7ae7eb49d0d20` | `c907a9035ab7d6b09e4f6defb0d690bed3a9a4cf` |
| C `58dcc338b1cb08b3a19deb547e62836f4580d8a2` | `3bd2585e433105e573556add717fab22586e3f62` |
| C 标题标记增量 `5e459a4ac32454181471873811819624b0d0cdee` | `9c7bba44f4b9e61ad68e04d133b33c189579ba1e` |
| 隔离 Postgres 索引复用 | `9a2f9c717daf5c5447c956cc76b64b3070aeafb7` |
| 本文第 1–8 节与示例 env | `95dabea42b1df5a540e4eab788406c72c3fd36ba` 的 cherry-pick |

`610fe59` 之后 Evaluation V2 的 schema 是 `evaluation-v2.3`。第 1 节的 `evaluation-v2.2` 只属于 `3bd2585`。检索默认仍是 top_k 10、candidate 20、lexical 0、rewrite false；这次拣入没有改 `config.py`。

RELEASE_READY 仍是 false。最终 SHA 还没冻结：离线 probe 读到 sources 目录外的修复由检索侧另交，拣入后再核数据 head 与代码 head。本环境没有启动 55432 上的 release 栈，也没有付费调用。标题和正文里的 `[S#]` 只在提示词展示时改成 `(S#)`，这不是完整的提示词注入防护。复用只读也不是专用角色，库属主可以关掉会话只读。
