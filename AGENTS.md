# 项目工作准则

- Codex 的全部开发在 `codex` 分支进行；每项任务完成后 commit，不主动 push。
- 修改前明确需求、前提与可验证的验收标准；有未解决的歧义先确认。
- 只实现当前要求，使用最少且清晰的代码，不增加投机性扩展。
- 只修改任务相关内容，遵循现有风格，不顺手重构或清理无关代码。
- 前端开发优先使用 [UI Arc](https://uiarc.dev/) 组件。现有组件及其组合无法满足视觉或交互要求时，再自定义组件。
- 修复缺陷时补充能够复现问题的测试，完成必要验证后再交付。
- Python 使用 `backend` 中的 uv 项目及 Python 3.12，不向全局环境安装项目依赖。
- 数据库使用独立 `study` schema；LangGraph 内部表由独立 setup 管理，位于 `study_graph`。
- 不提交 `.env`、密码、私钥或安装缓存；不改动已有其他数据库。

## `oil-dev` 开发约定

每次在本仓库使用 `oil-plan` 或 `oil-code-review`，均直接应用本节约定。计划中写明受影响路径和验证命令。审查时核对验证结果，并标明失败或尚未执行的门禁。

### 本地快测

| 变更范围 | 工作目录 | 命令 |
| --- | --- | --- |
| 后端 | `backend` | `uv run ruff check .`、`uv run pytest tests/unit -q` |
| 前端 | `frontend` | `npm test -- <测试文件>`、`npx tsc --noEmit` |
| 检索、切分或索引 | `backend` | `uv run --no-sync study-eval-rag --dataset evaluation/retrieval_mini.jsonl` 验证数据集。运行真实检索评测时使用 [检索评测说明](docs/retrieval-evaluation.md) |
| 纯文档 | 仓库根目录 | `git diff --check`，核对修改的相对链接、命令和状态声明 |

### 合并门禁

- 合并前要求 Backend 和 Frontend 两项作业通过。后端在 Python 3.12、PostgreSQL 16 和 pgvector 上跑 Ruff 与全量 Pytest。前端在 Node 22 上跑 Vitest 和 `npm run build`。[CI 配置](.github/workflows/ci.yml)
- 数据库相关验证需显式使用独立的 PostgreSQL `*_test` 数据库，设置 `RUN_DB_TESTS=1` 和 `TEST_DATABASE_URL`，初始化迁移和所需的独立 checkpointer。记录实际执行的集成测试，并列出任何跳过项。[Windows 启动说明](README.zh-CN.md)
- 对外 API 契约变更应同步更新 [`contracts/frontend.openapi.json`](contracts/frontend.openapi.json)，并通过 `uv run pytest tests/unit/test_frontend_contract.py -q`。

### 高风险路径

| 路径（相对仓库根目录） | 审查与验证重点 |
| --- | --- |
| `backend/alembic/`、`backend/src/study_platform/persistence/models/` | 既有数据兼容、迁移与 ORM 一致性。迁移变更需在隔离库跑 `uv run pytest tests/integration/test_migration_roundtrip.py -q` |
| `backend/src/study_platform/services/auth*`、`backend/src/study_platform/api/v1/auth.py`、`backend/src/study_platform/api/v1/account_recovery.py` | 登录、会话、授权和账户隔离。运行相关认证单测及 `tests/integration/test_auth.py` |
| `backend/src/study_platform/services/model_connections.py`、`backend/src/study_platform/services/model_network.py`、`backend/src/study_platform/api/v1/model_connections.py` | 凭据保护、外部连接边界、模型绑定和降级。运行相关单测及 `tests/integration/test_model_connections.py` |
| `backend/src/study_platform/services/ingestion.py`、`backend/src/study_platform/services/reindex.py`、`backend/src/study_platform/services/worker_runner.py` | 资料与引文兼容、任务取消与租约、索引切换。运行相关单测及 `tests/integration/test_ingestion_db.py`，涉及检索时追加检索回归 |
