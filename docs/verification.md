# 验收记录索引

2026-11-15 的事实问答评测、查询改写候选及失效引用修复见 [质量验收记录](verification-rag-quality.md)。

2026-11-15 的上传闭环、Windows 启停与故障恢复验收见 [本地 RAG 验收](verification-local-rag.md)。以下保留 2026-11-14 基础设施阶段的历史记录，不代表当前业务能力或测试总量。

## 2026-11-14 基础设施验收

日期：2026-11-14。已完成今天确认的基础设施及实际建库任务。

## Git 与依赖

- 本地 `main` 初始化空提交，`codex`、`grok` 从该提交创建；全部开发提交均位于 `codex`，基础设施验收时尚未执行 push。
- GitHub SSH 认证成功，origin 为 `git@github.com:ggwenling/Study_Platform.git`。
- Python 3.12 独立虚拟环境、uv 依赖安装及 `uv.lock` 已完成。
- `.env`、虚拟环境和安装缓存已被 Git 忽略，凭据没有提交。

## 数据库与 Windows 环境

- PostgreSQL 18.3 服务已启动，已有其他数据库予以保留。
- 通过官方维护流程修复现有 Build Tools，安装 MSVC/SDK，实际编译并安装 pgvector 0.8.7。
- 安装脚本的 PowerShell 5 中文 JSON 解码与含空格路径引用问题已修复，并通过实际执行验证。
- 独立 `study_platform`、`study_platform_test` 数据库及业务/Graph 运行角色已创建，时区为 UTC。
- 两个数据库均实际启用 `vector 0.8.7`、`citext 1.8`，Alembic 已升级到 `0001_initial_schema`。
- 两个数据库均有 37 张 `study` 业务表（不含 Alembic 版本表），以及官方 setup 创建的 4 张 `study_graph` 内部表。
- `users.status` 从文档的 VARCHAR(16) 调整为 VARCHAR(24)，以容纳原文档声明的 `pending_verification`。

## 验收结果

在 `backend` 中启用 `RUN_DB_TESTS=1` 执行 `uv run pytest -q`：**19 passed**。

- 9 项单元测试：健康检查、数据库字段合同、UUID/时间约定、枚举长度、复合外键和 Windows SelectorEventLoop 回归。
- 10 项集成测试：37 表实际结构与模型一致、Alembic 无漂移、CITEXT 和待验证状态写入、跨 owner/跨 Quiz 外键拒绝、current/active 约束、1024 维向量和余弦查询、业务与 Graph 权限隔离、checkpoint 跨连接持久化。
- 迁移往返测试只在随机创建的临时空库执行 upgrade → downgrade → upgrade，完成后删除该临时库；没有对开发库或长期测试库执行降级。
- 实际 HTTP `/health/live`、`/health/ready`、`/docs` 均返回 200。
- Ruff lint 与 format 检查通过。HTTP 验收用的临时 API 进程已停止；PostgreSQL 服务保持运行。

业务接口、鉴权、RAG、前端，以及复杂业务服务规则属于后续任务；边界见 `database-rules.md`。
