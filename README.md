# Study Platform

个人知识库和资料问答服务：上传资料，经解析、OCR 和向量索引后在对话中引用原文。后端使用 Python 3.12、uv、FastAPI 和 PostgreSQL + pgvector。

## 本地运行

```powershell
cd D:\Synapse\backend
uv sync --locked
```

首次安装 PostgreSQL 扩展见 [Windows 安装说明](infra/windows/README.md)。已有 PostgreSQL 管理员可运行下面的命令，交互输入密码，创建独立开发库、测试库和运行角色，并生成根目录 `.env`；遇到同名数据库或角色时脚本停止，不覆盖已有对象。

```powershell
uv run python -m study_platform.cli.bootstrap_database --admin-user postgres
```

也可复制根目录 `.env.example` 为 `.env`，自行准备数据库和角色。填写数据库连接、`DEEPSEEK_API_KEY` 和 `EMBEDDING_API_KEY`；不要提交 `.env`。聊天默认使用 `deepseek-flash`，向量默认使用 `qwen3.7-text-embedding-flash`（1024 维）。请确保对应账户有权限和余额；服务不会切换到其他模型。

```powershell
uv run alembic upgrade head
uv run study-setup-checkpointer
uv run study-api
```

另开一个终端启动资料处理 worker：

```powershell
cd D:\Synapse\backend
uv run study-worker
```

API 默认监听 `127.0.0.1:8000`，Swagger 在 `/docs`。`/health/live` 检查进程，`/health/ready` 检查业务迁移、知识库表、存储目录写入及模型密钥是否配置，不调用付费模型。迁移和 LangGraph checkpointer setup 是独立步骤，API 启动不会执行 DDL。当前问答使用 LangChain 模型调用和数据库记录，不依赖 LangGraph checkpointer 恢复对话。

资料支持 PDF、DOCX、UTF-8 TXT、PNG 和 JPEG，单文件最多 20 MiB、PDF 最多 50 页、图片最多 2000 万像素。无文字层的 PDF 页和图片由 RapidOCR 在 CPU 上处理；资料原件通过鉴权接口访问。上传后需要 worker 处理完成才能检索。完整调用步骤、Cookie/CSRF、SSE 和错误处理见 [API 接入说明](docs/api-integration.md)。

## 验证

```powershell
uv run ruff check .
uv run pytest -q
```

数据库集成测试需独立测试库、`TEST_DATABASE_URL` 和 `TEST_GRAPH_DATABASE_URL`。准备测试库后设置环境变量运行；测试库名称必须以 `_test` 结尾。

```powershell
uv run python tests/prepare_database.py
$env:RUN_DB_TESTS = '1'
uv run pytest -q
```

数据库规则见 [数据库规则](docs/database-rules.md)。`.env`、`.venv`、`.local` 和其他凭据或安装缓存不提交。
