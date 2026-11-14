# Study Platform

FastAPI 学习平台后端基础设施。Python 3.12、uv、SQLAlchemy 2、Psycopg 3、Alembic，
PostgreSQL 18 + pgvector。当前完成基础框架和数据库结构，业务接口尚未实现。

## 本地运行

```powershell
cd D:\Synapse\backend
uv sync --locked
```

首次安装 PostgreSQL 扩展见 [Windows 安装说明](infra/windows/README.md)。
已有 PostgreSQL 管理员可运行以下命令，交互输入密码，创建独立的开发库、测试库和运行角色，
并生成根目录被 Git 忽略的 `.env`。脚本遇到同名数据库或角色会停止，不覆盖已有对象。

```powershell
uv run python -m study_platform.cli.bootstrap_database --admin-user postgres
```

也可将根目录 `.env.example` 复制为 `.env`，自行准备数据库和角色并填写连接信息。
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
先对测试库执行 Alembic 迁移和 checkpointer setup，再设置 `$env:RUN_DB_TESTS='1'` 执行 pytest。
测试库名必须以 `_test` 结尾；测试数据使用事务回滚或随机 checkpoint thread 清理。

```powershell
uv run python tests/prepare_database.py
$env:RUN_DB_TESTS = '1'
uv run pytest -q
```

输入设计快照保存在 `docs/architecture/`，实现决策及仍需业务层落实的规则见
[数据库规则](docs/database-rules.md)。原文档的 PG16 调整为已确认的本机 PG18；
`users.status` 长度调整为 24，以容纳原文档声明的 `pending_verification`。

## Git 约定

`main` 和 `grok` 从初始化提交创建；所有 Codex 开发在 `codex` 分支完成。
每项任务完成后本地 commit，只有用户明确要求时才 push。
`.env`、`.venv` 和 `.local` 中的凭据、依赖及安装缓存不提交。
