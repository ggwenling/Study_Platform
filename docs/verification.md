# 本次验收记录

日期：2026-11-14。

已完成：

- 本地 `main` 初始化空提交；`codex`、`grok` 从该提交创建，开发提交均位于 `codex`。
- GitHub SSH 认证成功，origin 使用 SSH；未执行 push。
- Python 3.12 独立虚拟环境、依赖安装及 `uv.lock`。
- Ruff lint / format 检查通过；9 项单元测试通过。
- 37 张业务表的字段合同、UUID/时间约定、枚举长度及复合外键检查通过。
- 独立 Alembic DDL 可离线编译；SQLAlchemy mapper 和 PostgreSQL DDL 编译通过。
- 本机 HTTP `/health/live`、`/docs` 返回 200。
- Windows Uvicorn 事件循环回归测试已复现旧方案失败，并验证显式 SelectorEventLoop 修复。

尚待本机管理员步骤及实际 PostgreSQL 验收：

- 首次 C++ 工具安装因已有残缺 Build Tools 注册记录失败；脚本已增加官方 repair / modify 流程。
- 修复脚本的 Windows UAC 提升请求尚待用户处理，PostgreSQL 服务仍停止。
- pgvector 尚未完成编译安装，管理员账号尚未验证，项目数据库尚未创建。
- 实际 Alembic upgrade/downgrade、数据库约束/权限/向量测试及 checkpointer setup、持久化往返尚未运行。
- 9 项集成测试当前因未启用测试数据库而跳过；不能将其计为通过。

管理员步骤完成后，应更新此记录，并在独立测试数据库完成全部集成测试后再确认实际数据库任务完成。
