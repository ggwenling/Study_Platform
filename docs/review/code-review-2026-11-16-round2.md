# 第二轮代码审阅（2026-11-16）

- **基准**：第一轮报告 `code-review-2026-11-16.md`（审阅 `714a070`）
- **修复分支**：`cursor/security-fixes-round2-db11`
- **迁移版本**：`0006_security_hardening`
- **方法**：逐项对照第一轮问题验证 + 全量测试 + 集成测试（PostgreSQL 16 + pgvector，库 `study_platform_test`）

---

## 1. 检查执行情况（修复后）

| 检查项 | 结果 |
| --- | --- |
| `uv sync --locked` | 通过 |
| `uv run ruff check .` | **通过** |
| `uv run pytest -q`（`RUN_DB_TESTS=1`） | **146 passed, 2 skipped** |
| PostgreSQL | **已安装**（apt `postgresql` + `postgresql-16-pgvector`），`study_platform_test` + `prepare_database.py` |
| 跳过项 | `test_runtime_access.py` 2 项：当前 `TEST_DATABASE_URL` 使用 `postgres` 超级用户，无法验证角色分离（见 §4） |
| `npm ci` / `npm test` / `npm run build` | **均通过**（45 tests） |

---

## 2. 第一轮问题修复状态

| ID | 严重度 | 状态 | 说明 |
| --- | --- | --- | --- |
| M-1 Refresh 不轮转 | 中 | **已修** | `rotate_session` 刷新 refresh/csrf 并递增 `access_generation`；集成测验证旧 refresh 401 |
| M-2 登录无限速 | 中 | **已修** | `auth_limits.rate_limit_auth`，默认 30/min/IP；单测 `test_auth_limits.py` |
| M-3 登录 CSRF / 无 Origin | 中 | **已修** | `allowed_request_origin` 要求白名单 Origin 或 Referer；TestClient 默认 Origin |
| M-4 上传后缀 MIME / 提前 content | 中 | **已修** | `upload_validation` 魔数；`content` 仅 `parse_status=ready` |
| M-5 chunk_query 无 owner | 中 | **已修** | join `KnowledgeBase` + owner；集成 `test_chunk_query_rejects_foreign_owner_index` |
| M-6 文档缺库详情 API | 中 | **已修** | `docs/api-integration.md` 增补章节 |
| M-7 Ruff 失败 | 中 | **已修** | 导入整理 + 全绿 |
| M-8 COOKIE_SECURE | 中 | **已修** | `validate_cookie_security`：`AUTH_PUBLIC_URL` 为 https 时强制 `COOKIE_SECURE=true` |
| L-1 CSRF 可读 | 低 | **已修** | `study_csrf` HttpOnly；`AuthDTO.csrf_token` + 前端内存持有 |
| L-2 user_agent_hash | 低 | **已修** | `create_session` / `rotate_session` 写入 |
| L-3 page_no 无上限 | 低 | **已修** | `max_pdf_page_preview`（默认 50） |
| L-4 project_knowledge_base_id | 低 | **待确认** | 产品未定义切库是否更新项目归属；**未改代码** |
| L-5 health 硬编码版本 | 低 | **已修** | 读取 Alembic head 动态比对 |
| L-6 with_general 无测试 | 低 | **已修** | 集成 `test_with_general_empty_kb_still_composes` |
| L-7 DOCX 压缩炸弹 | 低 | **已修** | `ensure_docx_uncompressed_limit` + 单测 |
| L-8 历史整轮排除 | 低 | **已修** | 移除 `retrieval_enabled=false` 整轮排除；检索仍排除禁用片段 |
| L-9 旧 Bearer 窗口 | 低 | **已修** | JWT `tg` 与 `access_generation`，refresh 立即使旧 access 失效 |
| L-10 前端 XSS 窃取 token | 低 | **部分缓解** | Bearer/CSRF 仅存模块闭包；完整防护需 CSP（未实现） |
| L-11 retry 未校验 KB owner | 低 | **已修** | `retry` 查询增加 `KnowledgeBase.owner_user_id` |

---

## 3. 验证要点（抽样）

- **Refresh 轮转 + access 作废**：`test_bearer_validation_refresh_and_revocation`。
- **上传/content**：`test_bearer_required_for_upload_and_private_files` 在 DB 标记 `ready` 后下载；魔数 `test_upload_validation.py`。
- **检索纵深**：`test_chunk_query_rejects_foreign_owner_index`。
- **with_general**：空库仍进入 composing，响应含分区标记。
- **片段禁用**：`test_disabled_fragment...` 更新为禁用后 **history 保留**、检索为空。

---

## 4. 新发现与残留风险

| 项 | 严重度 | 说明 | 建议 |
| --- | --- | --- | --- |
| R2-1 集成环境角色分离未测 | 低 | VM 使用 `postgres` 超级用户跑集成测，`runtime_access` 跳过 | CI 使用 `study_app`/`study_graph` 引导后跑全套 |
| R2-2 速率限制进程内 | 低 | `auth_limits` 单进程 dict，多副本需网关层限速 | 生产加反向代理/WAF 限速 |
| R2-3 L-10 CSP | 低 | XSS 仍可 hook `fetch` 读 Bearer | 部署 Content-Security-Policy |
| R2-4 L-4 产品决策 | — | 见上表 | 产品确认后单独立项 |

**未见**本轮修复引入的跨用户越权或 RAG scope 回归（集成测 39+ 项 chat/auth 通过）。

---

## 5. 变更摘要（供复核）

- `backend`: 迁移 `0006`、auth 会话硬化、上传校验、chat owner join、ingest DOCX 限额、health 动态版本。
- `frontend`: `csrf_token` 内存、契约字段。
- `contracts/frontend.openapi.json`：`AuthDTO.csrf_token`。
- `docs/api-integration.md`：库详情与认证强化。

---

## 6. 待确认（产品）

**L-4 `project_knowledge_base_id`**：同会话切库时是否更新会话的 `project_knowledge_base_id`（影响侧栏项目归类，不影响 RAG 事实范围）。请产品确认后再实现。

---

*第二轮审阅完成：2026-11-16。*