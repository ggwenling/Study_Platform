# 持续集成（GitHub Actions）

仓库在 push 与 pull request 时运行 [`.github/workflows/ci.yml`](../.github/workflows/ci.yml)，包含两个并行 job：

| Job | 内容 |
| --- | --- |
| **backend** | `uv sync --locked`、`ruff check`、`pytest`（`RUN_DB_TESTS=1`），使用带 pgvector 的 PostgreSQL 16 服务容器；通过 `tests/ci_bootstrap_postgres.py` 创建 `study_app` / `study_graph` 角色、迁移与 checkpointer setup |
| **frontend** | `npm ci`、`npm test`、`npm run build`（`tsc -b` 类型检查含在 build 中） |

CI 不配置任何真实模型 API Key；学习相关集成测试依赖 `LEARNING_STUB_GRADER=true`。

## 本地复现（与 CI 对齐）

需要本机 PostgreSQL 16（含 pgvector）监听 `localhost:5432`，管理员账号 `postgres` / `postgres`（或通过环境变量覆盖）。

```bash
# 根目录
export PGHOST=localhost PGPORT=5432 POSTGRES_USER=postgres POSTGRES_PASSWORD=postgres
export AUTH_JWT_SECRET='本地至少32字节的随机密钥'
export RUN_DB_TESTS=1

cd backend
uv sync --locked
uv run python tests/ci_bootstrap_postgres.py
uv run ruff check .
uv run pytest -q

cd ../frontend
npm ci
npm test -- --pool=threads --maxWorkers=1
npm run build
```

`ci_bootstrap_postgres.py` 会写入根目录 `.env`（已在 `.gitignore` 中），内容与 CI 使用的连接串一致：`TEST_DATABASE_URL` 使用管理员连接（便于 `CREATEDB` 类测试），`DATABASE_URL` 使用 `study_app`，`TEST_GRAPH_DATABASE_URL` 使用 `study_graph`，从而能执行 `test_runtime_access.py` 中的角色隔离断言。

仅刷新迁移后的表级权限（本地调试时）：

```bash
uv run python tests/ci_bootstrap_postgres.py --grants-only
```