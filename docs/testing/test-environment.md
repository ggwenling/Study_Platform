# Deep-test 隔离环境与复现

## 1. 目标栈与实际核对

| 组件 | 目标 | 记录方式 |
| --- | --- | --- |
| Python | 3.12 | `manifest.json` → `versions.python` |
| Node | 22 | `versions.node` |
| PostgreSQL | 16 + pgvector | 服务端 `SELECT version()`；客户端 `psql --version` |
| OS | 隔离云 VM（本轮 Linux） | `versions.platform` |

**偏差处理：** 在 `manifest.json` 的 `versions.note` 与 PR 描述中写明；Windows 目标机另跑协议（不声称已被短 Linux 跑证明）。

## 2. 目录与隔离所有权

| 路径 | 用途 | Git |
| --- | --- | --- |
| `.local/deep-test/env/deep-test.env` | 测试专用连接串 | 忽略 |
| `.local/deep-test/storage/` | 测试原件/衍生 | 忽略 |
| `artifacts/deep-test/<run-id>/` |  sanitized 摘要/门禁 | 忽略 |
| `study_platform_test` | 共享集成库名（CI 同） | N/A |

**禁止：** 对生产库运行 `ci_bootstrap_postgres.py` 而不备份；禁止 load test 公网端点。

### 2.1 安全引导（不覆盖用户 `.env` 的首选流程）

```bash
# 从仓库根目录
python3 backend/evaluation/deep-test/harness/bootstrap_isolated_env.py
```

行为：

1. 若存在根 `.env`，备份到 `.local/deep-test/env/root-env-backup-*.env`
2. 调用 `tests/ci_bootstrap_postgres.py`（会临时写根 `.env`）
3. 复制并扩展为 `.local/deep-test/env/deep-test.env`（含 `STORAGE_DIR`）
4. **请从备份还原个人根 `.env`**，后续命令仅 `export` 测试 env 文件

加载隔离环境：

```bash
set -a
source .local/deep-test/env/deep-test.env
set +a
export RUN_DB_TESTS=1
```

## 3. 测试账号（≥2）

| 账号 | 用途 | 种子数据 |
| --- | --- | --- |
| `deep-u1@example.test` | 主库 A、会话、笔记 | Phase 2：`harness/seed_accounts.py`（待增） |
| `deep-u2@example.test` | 库 B、跨用户探针 | 含 `canary-private` 语义文档 |

密码：仅写入 `.local/deep-test/env/deep-test.env`（`DEEP_TEST_U1_PASSWORD` 等），**不提交**。

## 4. 离线门禁（无模型花费）

```bash
# 语料校验
python3 backend/evaluation/deep-test/validate_dataset.py

# 全门禁（Ruff + pytest + Vitest + build）
python3 backend/evaluation/deep-test/harness/run_offline_gate.py
```

产物：`artifacts/deep-test/<run-id>/manifest.json`、`summary.log`

## 5. Live RAG 对照（**需审批后执行**）

```bash
set -a && source .local/deep-test/env/deep-test.env && set +a
# TEST_DATABASE_URL 必须指向 *_test 且具备 CREATEDB
cd backend
uv run study-eval-rag --dataset evaluation/deep-test/questions.jsonl \
  --strategy compare --live --output ../../.local/deep-test/rag-live-<id>
```

**Fail-closed：** 无 `DEEPSEEK_API_KEY` / `EMBEDDING_API_KEY` 时命令应失败；不得使用生产用户密钥。

### 5.1 花费估算公式（无单价臆测）

设 \(N_A\) 为可回答题数，\(N_U\) 为无答案题数，\(C\) 为语料嵌入文档数，\(k\) 为检索 top-k：

- 嵌入调用 ≈ \(C\) 次（批处理按实现）
- 每题回答 ≈ 1 次 chat + 1 次检索前改写（若启用）
- Compare 策略 ≈ ×2 向量路径开销（见 `docs/rag-quality.md`）

记录 **实际 token** 到 `cost-manifest.json`（live 阶段），无 token 则标 `unknown`。

## 6. 拆卸与恢复

```bash
# 删除测试库（慎用）
psql -h localhost -U postgres -c "DROP DATABASE IF EXISTS study_platform_test"

# 清理本地产物
rm -rf .local/deep-test/storage artifacts/deep-test/<run-id>

# 从备份恢复根 .env
cp .local/deep-test/env/root-env-backup-*.env .env
```

## 7. Windows / 目标机（协议，本轮未执行）

- 使用 `Start-Synapse.cmd` 三进程启动；测试库指向本地 `_test`
- 备份：复制 `STORAGE_DIR` + `pg_dump study_platform_test`
- 长稳 soak：≥24h worker+API，单独计划

## 8. 版本清单字段（manifest）

每次运行记录：`baseline_sha`、`harness` 文件列表、`versions`、`isolation`、`steps[]`、`model_spend`、`load_baseline`。