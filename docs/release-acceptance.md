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
| G2 | 本地后端单测与 Ruff | 见下方命令 | NOT_VERIFIED。本机已有 uv 0.13.9 和 `backend/.venv`，但没有跑 `ruff` 或 `pytest` |
| G3 | 独立 `*_test` 上的 DB 集成、迁移往返、契约 | 见下方命令 | 空库 `study_release_migrate_test` 的 Alembic `upgrade head` → `downgrade base` → `upgrade head` 已通过，结束后 0 行用户。pytest 集成与契约仍是 NOT_VERIFIED |
| G4 | 权限隔离：角色、跨用户、认证 | 见下方命令 | NOT_VERIFIED |
| G5 | UX：Vitest、`npm run build`，加上最终 SHA 的浏览器走查 | CI 只覆盖前两项 | 浏览器 UX NOT_VERIFIED |
| G6 | 真实质量。P4 50Q 必须跑在最终 SHA 上，并有花费记录 | 不在本环境默认执行 | NOT_VERIFIED。禁止用 100Q dry 或 V2 离线报告代替 |
| G7 | D `bc-5fe995c3-bdee-59df-bfa1-93264c3e2d65` 对最终 SHA 的 blocker 为 0 | 父主附上 D 的结论和 SHA | NOT_VERIFIED |
| G8 | gold 与第 4 节哈希一致 | `sha256sum` 对照 | 本快照哈希已记录。最终 SHA 未对照 |
| G9 | 默认 top-k 10、candidate 20、lexical 0、rewrite false | 读 `config.py` 与运行中的环境 | 源码默认已核对。运行中环境 NOT_VERIFIED |
| G10 | 隔离库备份恢复演练 | `infra/release/drill_isolated_env.py` | 本快照演练通过，见第 7 节。这不是最终 SHA 的 release PASS |

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

本机演练没有使用 Docker。安装的是 Ubuntu 24.04 的 PostgreSQL 16.15 和 `postgresql-16-pgvector` 0.6.0。默认集群 `16/main` 保持 down，不监听 5432。专用集群是 `16/releasegate`，数据目录 `/var/lib/postgresql/16/releasegate`，`listen_addresses = '127.0.0.1'`，端口 55432。

```bash
bash infra/release/prepare_loopback_cluster.sh
```

脚本在 `16/main` 已经 online 时直接退出。集群已存在时只核对配置并保持运行，不重新 initdb。

已有 Docker、且没有这套 apt 集群时，仍可以用下面的回环容器。密码来自 gitignored env，不写进命令历史：

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

`uv run` 会再拉起子进程。停 API 或 worker 时结束整个进程组，不要按端口去杀 8000、5173 或 5432。Docker 形态只停名为 `knowloop-release-gate` 的容器。apt 形态停专用集群：

```bash
sudo pg_ctlcluster 16 releasegate stop
```

演练脚本会自己停掉它启动的 API 和 worker。本轮结束后 18000 已关闭，`16/releasegate` 仍 online，方便复核两份 `study_release_*_test`。

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

- 空的 `study_release_migrate_test` 可以做 `upgrade head` → `downgrade base` → `upgrade head`。2026-11-23 的演练已经在这个空库上走完，结束后用户行数是 0，revision 回到 `0016_rerank_and_kb_retrieval`。这没有代替 `tests/integration/test_migration_roundtrip.py`。
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

`--schema` 导出不含 public 里的 `citext` / `vector`。`--no-privileges` 也不会带回应用角色授权。恢复前要先装扩展，恢复后按演练脚本重新授权，再让 `study_release_graph` 读 checkpoint。缺任何一步，`/health/ready` 或图角色读取都会失败。

```bash
createdb --host=127.0.0.1 --port=55432 --owner=postgres study_release_restore_test
psql --host=127.0.0.1 --port=55432 --username=postgres --dbname=study_release_restore_test \
  --command="CREATE EXTENSION IF NOT EXISTS citext" \
  --command="CREATE EXTENSION IF NOT EXISTS vector"
pg_restore --host=127.0.0.1 --port=55432 --username=postgres \
  --dbname=study_release_restore_test --exit-on-error "$dest/study.dump"
pg_restore --host=127.0.0.1 --port=55432 --username=postgres \
  --dbname=study_release_restore_test --exit-on-error "$dest/study_graph.dump"
```

存储目录解到单独目录，核对文件内容 SHA-256 后再决定是否替换 `.local/release-gate/storage`。不要在校验前删除现有 storage。tar 包本身的哈希含打包时间，内容哈希才是比对依据。可重复执行的实现是：

```bash
cd backend
uv run python ../infra/release/drill_isolated_env.py
```

脚本拒绝仓库根目录已有 `.env`、拒绝非 `releasegate` 数据目录、拒绝 5432 被占用，并且只在 `study_release_migrate_test` 上执行 downgrade。第二次运行会登录已有合成账号，不重复插入 checkpoint。

## 7. 本轮执行记录

产品代码仍是 `3bd2585e433105e573556add717fab22586e3f62`。演练证据在 [infra/release/drill-evidence.json](../infra/release/drill-evidence.json)，`drill=pass`，`release_ready=false`。捕获时间 `2026-11-24T05:45:53Z`。没有调用模型 API。合成账号密码只写在 gitignored env，没有进入证据、日志摘要或本文。

已在本机做完：

- `16/releasegate` 只监听 `127.0.0.1:55432`；`16/main` 为 down。扩展 `citext` 1.6、`vector` 0.6.0。
- gate 库迁移到 `0016_rerank_and_kb_retrieval`，`study-setup-checkpointer` 建出 4 张 `study_graph` 表。`study` 与 `study_graph` 合计 48 张表。
- API 在 18000 上 `/health/live` 与 `/health/ready` 都是 200。停掉后端口关闭、`study_release_app` 会话归零。再启动，两次仍是 200。
- worker 单独运行时保持 1 条 `study_release_app` 会话，然后被进程组停掉。
- 注册 `release-gate-user@example.test`，创建知识库 `Release Gate Synthetic`，写入 `synthetic-release-gate.txt`。文件内容 SHA-256 是 `0507d090bef861772b8bb25303edabe24481d3d45f7bf5a474cae3e9fd7e3845`。`study_graph.checkpoints` 有 1 行 `release-gate-synthetic`，图角色能读。
- 停掉 API 后备份。恢复到空的 `study_release_restore_test` 之前先装扩展，恢复后再授权。源库与恢复库的 revision、用户、知识库、48 张表和 marker 行数一致。备份没有改源库。源库没有被 downgrade。
- 把 API 指到恢复库和恢复出来的存储目录，`/health/ready` 返回 200。结束后 API 与 worker 都已退出。
- 同一脚本再跑一遍仍然通过，用来确认已有合成数据上的登录和 marker 不会重复插入。

仍是 NOT_VERIFIED，不能当成 release PASS：

- 最终集成 SHA，以及该 SHA 的 CI、本地 `ruff` / `pytest`、权限隔离套件、浏览器 UX、真实 50Q、D review。
- 前端 15173 没有启动。这次演练没有打开网页。
- 没有 merge、tag 或生产部署。

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
| B sources 路径增量 `dfc0d1c1633c4088e19f302ee47ee895cff8f8b0` | `78edfbe`，只拣这一笔，不合并 PR #21 |
| 本文第 1–8 节与示例 env | `95dabea42b1df5a540e4eab788406c72c3fd36ba` 的 cherry-pick |
| 隔离演练脚本与证据 `6fa31bf4b4d04395121224b4aebeb30588f91f10` | `daf8a9b`，只拣这一笔，不合并 PR #22 |

`610fe59` 之后 Evaluation V2 的 schema 是 `evaluation-v2.3`。第 1 节的 `evaluation-v2.2` 只属于 `3bd2585`。检索默认仍是 top_k 10、candidate 20、lexical 0、rewrite false；这次拣入没有改 `config.py`。

RELEASE_READY 仍是 false。sources 目录外读取的修复已经在 `78edfbe`。第 7 节描述的是产品快照 `3bd2585` 上的演练；那份 JSON 留在提交 `ac8d8bf` 里，不能当作下面这次候选复跑。验收候选包含这些脚本，但不是 merge、tag 或部署许可，也没有付费调用。标题和正文里的 `[S#]` 只在提示词展示时改成 `(S#)`，这不是完整的提示词注入防护。复用只读也不是专用角色，库属主可以关掉会话只读。`binding=database` 不核对 `package.sha256`。

## 10. 候选 `ac8d8bf` 的隔离复跑

被测产品树是 `ac8d8bf6079834c4a9d6c28e407f38a546a659fb`。证据提交是另一次提交，不移动 `cursor/deep-testing-prep-9b8e`。`backend` 与 `frontend` 相对这个产品树没有改动。演练在原有 `16/releasegate` 上执行，`listen_addresses=127.0.0.1`，端口 55432；`16/main` 保持 down。没有重建集群，也没有对已有数据的 gate 库做 downgrade。

`2026-11-24T05:53:59Z` 运行 `infra/release/drill_isolated_env.py`，`drill=pass`，`release_ready=false`。那次 JSON 在证据提交 `a0b290c33b9e533bda8322a067eefc12ffcbfe1d`，`product_tree_sha` 是 `ac8d8bf`。它不是 `3bd2585`，也不是第 11 节的新产品候选。

- 空库 `study_release_migrate_test`：`upgrade head` → `downgrade base` → `upgrade head`，用户 0 行，revision 回到 `0016_rerank_and_kb_retrieval`。
- gate 与 restore 的 revision 都是 `0016_rerank_and_kb_retrieval`，与代码 head 一致。`study` 与 `study_graph` 各 48 张表，两边表名集合相同。
- 合成用户 `release-gate-user@example.test`、知识库 `Release Gate Synthetic`、checkpoint `release-gate-synthetic` 在源库和恢复库都是 1。图角色能读 marker。
- 存储内容 SHA-256 仍是 `0507d090bef861772b8bb25303edabe24481d3d45f7bf5a474cae3e9fd7e3845`，恢复后的文件哈希与之相同。
- API `127.0.0.1:18000` 启动、停止、再启动时 `/health/live` 与 `/health/ready` 都是 200。worker 单独运行时有 1 条 `study_release_app` 会话。结束后 API 与 worker 已退出。把 API 指到恢复库时 `/health/ready` 也是 200。
- 没有模型调用。密码没有写入证据。

同一次读取的候选 CI，不再轮询：

| 运行 | 事件 | 结论 |
| --- | --- | --- |
| [45336065785](https://github.com/ggwenling/Study_Platform/actions/runs/45336065785) | push `ac8d8bf` | success。Frontend `138248319497` 05:50:08–05:50:48；Backend `138248319777` 05:50:07–05:51:56 |
| [45336071099](https://github.com/ggwenling/Study_Platform/actions/runs/45336071099) | pull_request `ac8d8bf` | success。Backend `138248337583` 05:50:10–05:51:48；Frontend `138248337889` 05:50:11–05:50:56 |

这两次 CI 只说明该候选的 Backend 与 Frontend 作业通过。D review、真实 50Q 和浏览器 UX 仍是 NOT_VERIFIED。RELEASE_READY 保持 false，不代替父主放行。

## 11. 候选 `783e680` 的隔离复跑

被测产品树是 `783e680b3682058786a4f6511ea6010b01a42584`。证据提交是另一次提交，父级包含该产品树，但不移动 `cursor/deep-testing-prep-9b8e`。`backend` 与 `frontend` 相对这个产品树没有改动。`ac8d8bf` 上的演练仍然有效，只属于那个 SHA；不要把第 7 节的 `3bd2585` 或第 10 节的 `ac8d8bf` 写成这次结果。

相对 `ac8d8bf`，产品只增加了 worker 冷导入修复和相关测试。gold、retrieval 与默认配置文件没有差异，真实 50Q 不在这个 SHA 上重建，G6 对 `783e680` 仍是 NOT_VERIFIED。

`2026-11-24T06:22:49Z` 在原有 `16/releasegate` 上运行 `infra/release/drill_isolated_env.py`。`drill=pass`，`release_ready=false`。当前 `infra/release/drill-evidence.json` 的 `product_tree_sha` 是 `783e680`。没有重建集群，没有 downgrade 已有数据的 gate 库，`16/main` 保持 down，55432 只在 `127.0.0.1`。

| 项 | 结果 |
| --- | --- |
| 空库迁移往返 | PASS。`study_release_migrate_test` 为 upgrade → downgrade base → upgrade，用户 0 行，head `0016_rerank_and_kb_retrieval` |
| gate / restore revision | PASS。两边都是这个 head，48 张 `study`/`study_graph` 表集合一致 |
| 合成数据 | PASS。用户、知识库、checkpoint marker 两边都在，图角色能读 marker |
| storage | PASS。内容 SHA-256 `0507d090bef861772b8bb25303edabe24481d3d45f7bf5a474cae3e9fd7e3845` |
| API 健康 | PASS。`127.0.0.1:18000` 启动、停止、再启动，以及指向恢复库时 `/health/ready` 都是 200 |
| 正式 worker | PASS。直接执行 `.venv/bin/study-worker`，入口 `study_platform.cli.worker:main`，没有 `PYTHONSTARTUP`，也不是预加载 API 的 wrapper。进程保持连接并有 1 条应用会话，日志没有 ImportError |

`2026-11-24T06:23:19Z` 到 `2026-11-24T06:23:26Z`，用同一产品树的正式控制台脚本再跑 `tests/unit/test_worker_cold_start.py` 和 `tests/integration/test_worker_process.py`。干净环境里 2 passed。合成任务走本地 stub，没有付费 API；临时库已删除，gate 仍是 1 个用户、revision `0016`。第一次尝试继承了演练 env 的 `ALLOWED_ORIGINS`，在启动 worker 前失败，这不是产品失败。

同一次读取的候选 CI，没有循环轮询：

| 运行 | 读取到的状态 |
| --- | --- |
| [45339464288](https://github.com/ggwenling/Study_Platform/actions/runs/45339464288) | push，completed/success。Backend `138259344583` 06:18:24–06:19:58；Frontend `138259345018` 06:18:25–06:19:02 |
| [45339470519](https://github.com/ggwenling/Study_Platform/actions/runs/45339470519) | pull_request。运行对象当时是 in_progress。同一次作业页上 Frontend `138259366387` 与 Backend `138259366615` 都已 success，Backend 结束于 06:20:19。没有再读运行对象 |

D review、真实 50Q 和浏览器 UX 仍是 NOT_VERIFIED。RELEASE_READY 保持 false，不代替父主放行。
