# Study Platform

FastAPI 学习平台后端基础设施。Python 3.12、uv、SQLAlchemy 2、Psycopg 3、Alembic，
PostgreSQL 18 + pgvector。当前完成基础框架和数据库结构，业务接口尚未实现。

## 本地运行

```powershell
cd D:\Synapse\backend
uv sync --locked
```

将根目录 `.env.example` 复制为 `.env` 并填写本机连接信息，文件已被 Git 忽略。
应用连接使用 `DATABASE_URL`；迁移使用独立的 `MIGRATION_DATABASE_URL`；
LangGraph 使用 Psycopg 格式的 `GRAPH_DATABASE_URL`。

```powershell
uv run alembic upgrade head
uv run study-setup-checkpointer
uv run study-api
```

API 默认监听 `127.0.0.1:8000`，Swagger 位于 `/docs`。
`/health/live` 检查进程；`/health/ready` 检查数据库连接及业务表是否可读。
业务路由预留 `/api/v1`。迁移和 checkpointer setup 是独立步骤，API 启动不会执行 DDL。
Windows 请使用 `study-api` 入口，确保异步 Psycopg 使用 SelectorEventLoop。

## 验证

```powershell
uv run ruff check .
uv run pytest -q
```

数据库集成测试需准备独立测试库并填写 `TEST_DATABASE_URL`、`TEST_GRAPH_DATABASE_URL`，
随后设置 `$env:RUN_DB_TESTS='1'` 再执行 pytest。

## Git 约定

`main` 和 `grok` 从初始化提交创建；所有 Codex 开发在 `codex` 分支完成。
每项任务完成后本地 commit，只有用户明确要求时才 push。
`.env`、`.venv` 和 `.local` 中的凭据、依赖及安装缓存不提交。
