# 代码审阅报告（2026-11-16）

- **审阅基准**：`codex` 分支提交 `714a070`（Build knowledge library detail with live index management）
- **审阅范围**：后端 `backend/`、前端 `frontend/`、契约 `contracts/frontend.openapi.json`、产品文档 `docs/product/*`、接入说明 `docs/api-integration.md`、工作准则 `AGENTS.md`
- **审阅方式**：静态阅读 + 在 Cloud Agent VM 内执行可运行检查；**未修改业务代码**
- **产品关注**：知识库范围隔离、鉴权/CSRF、上传限制、worker 恢复、引用回看、SSE 流式问答

---

## 1. 执行摘要

整体架构与产品关键规则（每轮仅当前库检索、切库后历史不进模型上下文、引用按会话属主校验、上传所有权链、`MATERIALIZED` 向量检索隔离）在**代码与单元测试**层面一致，未发现可仅凭阅读确认的**高危越权读库**漏洞；该结论的强证明依赖集成测试（`backend/tests/integration/test_chat.py`、`test_auth.py`、`test_resource_api.py` 等），本轮**未能执行**。

主要风险集中在：**会话 Cookie 生命周期**（refresh 不轮转、CSRF 双提交 Cookie 可读、生产 `COOKIE_SECURE` 默认值）、**认证面缺少限速**、**上传阶段信任文件名后缀且原件可在 ingest 完成前下载**、**检索 SQL 缺 owner 纵深防御**、**对外文档落后于库详情 API**。另有 **Ruff 静态检查当前失败**（导入排序），与 AGENTS 要求的 `uv run ruff check .` 不一致。

---

## 2. 检查执行情况

| 检查项 | 是否执行 | 结果 | 说明 |
| --- | --- | --- | --- |
| `backend`: `uv sync --locked` | 是 | **通过** | VM 初始无 `uv`，已用官方安装脚本安装 `uv 0.13.8` 后执行成功 |
| `backend`: `uv run ruff check .` | 是 | **失败（2）** | `I001` 导入未整理：`api/v1/__init__.py:1-5`、`api/v1/library_detail.py:3-20`；均可 `--fix` |
| `backend`: `uv run pytest -q` | 是 | **85 passed, 53 skipped** | 跳过项均为需 `RUN_DB_TESTS=1` 及 `TEST_DATABASE_URL`（库名 `*_test`）的集成测试 |
| `backend`: `RUN_DB_TESTS=1` 集成测试 | **否** | — | VM 无 `postgresql`/`pgvector` 软件包源、无 Docker；无法拉起 PostgreSQL |
| `frontend`: `npm ci` | 是 | **通过** | 0 vulnerabilities |
| `frontend`: `npm test` | 是 | **45 passed**（6 文件） | jsdom 打印 `scrollTo` 未实现提示，不影响结果 |
| `frontend`: `npm run build` | 是 | **通过** | `tsc -b && vite build` 成功；大 chunk 体积警告非错误 |

### 2.1 未能运行的测试及影响

- **集成测试（53 项 skip）**：覆盖 Bearer/刷新/吊销、跨用户资料与文件、RAG 切库与 scope、引用归属、库详情 `update_retrieval`、迁移往返等。**未跑是本报告对隔离与并发结论标为「代码+单测支持、集成未复验」的原因。**
- **建议复验环境**：本地或 CI 按 `backend/tests/prepare_database.py` 与非 `_test` 库隔离要求配置后执行 `RUN_DB_TESTS=1 TEST_DATABASE_URL=... uv run pytest -q`。

---

## 3. 已验证的防护（对照产品规则）

| 领域 | 实现要点 | 位置（代表） | 测试依据（集成，本轮未跑） |
| --- | --- | --- | --- |
| 资料/文件所有权 | `owned_kb` / `owned_material` / `owned_version` / `owned_job` | `api/v1/materials.py` | `test_bearer_required_for_upload_and_private_files` |
| RAG 每轮 scope | `visible_indices` 绑定 owner+kb+material_ids；`context()` 历史要求同 kb、同 answer_mode、同 `index_version_ids` | `services/chat.py:55-80, 292-323` | `test_scope_switch_excludes_old_history_and_enforces_owner` |
| 向量检索隔离 | `chunk_query` + `MATERIALIZED` CTE，注释说明防 ANN 全局 top-k 越过滤 | `services/chat.py:369-396` | `test_retrieval_keeps_scope_and_only_caps_six_for_fusion` |
| 轮次并发/幂等 | `pg_advisory_xact_lock` + `IdempotencyKey` 指纹 | `services/chat.py:98-118` | `test_first_send_idempotency_and_per_conversation_concurrency` |
| 引用回看属主 | `get_source` join `Conversation.owner_user_id` | `api/v1/resources.py:555-568` | `test_reference_endpoint_checks_conversation_owner` |
| 失效来源脱敏 | `citation_dto` 不可用时不返回 quote/url | `services/citations.py:23-66` | `test_chat.py` 内不可用场景 |
| Worker 租约恢复 | `claim` 处理过期租约、`lease_expired`、可重试队列 | `services/ingestion.py:181-213, 297-489` | `test_ingestion_db.py`（集成） |
| 前端契约 | OpenAPI 与 `create_app` 一致；SSE Bearer + `Idempotency-Key` | `tests/unit/test_frontend_contract.py` | 本轮 **已跑通过** |

---

## 4. 问题清单（按严重度）

说明：**高** = 可直接导致跨用户数据泄露或严重完整性破坏（已确认或高置信）；**中** = 安全/可靠性/契约缺口，需在有 DB 或渗透场景下进一步确认；**低** = 纵深防御、运维、文档或体验类。

### 4.1 中

#### M-1 Refresh Token 会话内不轮转

| 项 | 内容 |
| --- | --- |
| **位置** | `backend/src/study_platform/api/v1/auth.py:153-162`；`backend/src/study_platform/services/auth.py:61-91, 94-104` |
| **问题** | `/auth/refresh` 仅签发新 access JWT（`issue_token`），**不更换** `study_refresh` Cookie 中的 opaque token，也不更新 `UserSession.refresh_token_hash`。Refresh 泄露后在会话 TTL（默认 7 天）内可反复刷新，直至 logout 或密码重置吊销。 |
| **触发/复现** | 窃取 `study_refresh` + 可读的 `study_csrf` 后周期性调用 `POST /auth/refresh`。 |
| **验证** | 代码审阅；`test_bearer_validation_refresh_and_revocation` 覆盖吊销/CSRF，**未**断言 refresh 轮转。 |

#### M-2 认证端点无速率限制与账户锁定

| 项 | 内容 |
| --- | --- |
| **位置** | `backend/src/study_platform/api/v1/auth.py:139-150`（及 register 同路径）；全仓库无 rate limit 实现 |
| **问题** | 登录依赖 Argon2 + 统一错误文案（可选 `LOGIN_DETAILED_ERRORS`），**无** IP/账户节流或失败锁定，在线撞库成本偏低。 |
| **触发/复现** | 对 `/auth/login` 高并发密码尝试。 |
| **验证** | 代码审阅；无对应用例。 |

#### M-3 登录/注册 CSRF 防护依赖 Origin，缺失 Origin 时不拒绝

| 项 | 内容 |
| --- | --- |
| **位置** | `backend/src/study_platform/services/auth.py:27-30`；`auth.py:106-150` |
| **问题** | `check_origin` 仅在 **存在** `Origin` 且不在白名单时 403；**无 Origin 不拦截**。注册/登录无 CSRF token，存在经典 **登录 CSRF** 风险（第三方页面替受害者种攻击者会话）。 |
| **触发/复现** | 构造不向 API 发送 `Origin` 的跨站表单 POST 登录攻击者账号（取决于浏览器/扩展行为）。 |
| **验证** | 代码审阅；`test_login_errors_and_session_creation` 测恶意 Origin，**未**测无 Origin。 |

#### M-4 上传 MIME 仅信文件名后缀，原件可在 ingest 校验前下载

| 项 | 内容 |
| --- | --- |
| **位置** | `backend/src/study_platform/api/v1/materials.py:213-216, 246-250, 442-451`；真实格式校验在 `services/ingestion.py:99-165` |
| **问题** | 上传用 `Path(filename).suffix` 映射 `MIMES`，忽略 multipart `Content-Type`，**不校验魔数**。202 成功后 worker 处理完成前，属主可通过 `GET .../content` 以声明 MIME 下载落盘文件（有 `nosniff`，但仍占存储/误导类型）。 |
| **触发/复现** | 上传 `evil.pdf`（实为其他格式）→ 202 → 立即 `GET /material-versions/{id}/content`。 |
| **验证** | 代码审阅；`tests/unit/test_ingestion.py` 覆盖 extract 阶段，**无**上传口魔数测试。 |

#### M-5 向量检索 `chunk_query` 未联结 owner（纵深防御缺口）

| 项 | 内容 |
| --- | --- |
| **位置** | `backend/src/study_platform/services/chat.py:326-334, 369-396` |
| **问题** | `chunk_query` 仅按 `index_version_id` 数组与 Material 状态过滤，**未**校验 `Material.owner_user_id` / `KnowledgeBase.owner_user_id`。当前经 `visible_indices` 预筛，正常运行难越权；若未来 scope 被污染或内部误传 index id，检索层无最后一道租户隔离。 |
| **触发/复现** | 需构造非法 `index_version_id` 进入 `source_scope`（缺陷或错误调用）。 |
| **验证** | 代码审阅；集成测 scope，**未**测错误 owner 的 index。 |

#### M-6 `docs/api-integration.md` 未收录库详情/片段/分类 API

| 项 | 内容 |
| --- | --- |
| **位置** | 实现：`backend/src/study_platform/api/v1/library_detail.py`（如 `:167-169` overview、`:216-260` fragments、`:268-290` retrieval、分类相关 `:303-343`）；契约：`contracts/frontend.openapi.json`；前端：`frontend/src/api/client.ts:117-129` |
| **问题** | 产品声明接入以 `api-integration.md` 为准，但 `714a070` 引入的库仪表盘、片段检索开关、文档分类等 **未写入该文档**（`docs/` 内无 `fragments`/`overview` 描述）。易造成集成方遗漏鉴权或误用路径。 |
| **验证** | 文档与 OpenAPI/代码对照。 |

#### M-7 Ruff 检查失败（与 AGENTS 验收不一致）

| 项 | 内容 |
| --- | --- |
| **位置** | `backend/src/study_platform/api/v1/__init__.py:3-5`；`backend/src/study_platform/api/v1/library_detail.py:17-18` |
| **问题** | `uv run ruff check .` 报 2 个 `I001` 可自动修复的导入排序错误。 |
| **验证** | **本轮已执行**，退出码 1。 |

#### M-8 生产 Cookie `Secure` 默认为 false

| 项 | 内容 |
| --- | --- |
| **位置** | `backend/src/study_platform/core/config.py:25`（`cookie_secure`）；`services/auth.py:87` |
| **问题** | 未设置 `COOKIE_SECURE=true` 时，`study_refresh` 可在明文 HTTP 上传输，易被嗅探。文档要求 HTTPS 部署时开启，**无启动时强制或告警**。 |
| **验证** | 代码审阅。 |

### 4.2 低

#### L-1 CSRF Cookie 对 JavaScript 可读，放大 XSS 对面

- **位置**：`services/auth.py:78-80`（`study_csrf` `httponly=False`）
- **问题**：同源 XSS 可读 CSRF，配合窃取的 HttpOnly refresh 可刷新/登出会话。
- **验证**：集成测明确断言非 HttpOnly（`test_auth.py:73`）。

#### L-2 `UserSession.user_agent_hash` 未写入，Refresh 无设备绑定

- **位置**：`persistence/models/identity.py:79`；`create_session` 未设置字段
- **问题**：Refresh 仅校验 token 哈希与过期，无法检测跨设备盗用。

#### L-3 材料页图 `page_no` 无合理上限

- **位置**：`api/v1/materials.py:454-464`
- **问题**：仅 `page_no >= 1`，极大页码触发磁盘探测与 I/O，轻微 DoS。
- **复现**：`GET .../pages/999999999`。

#### L-4 同会话切库不更新 `project_knowledge_base_id`（元数据不一致）

- **位置**：`services/chat.py:125-134, 133-134`（新会话设 `project_knowledge_base_id=kb.id`；已有会话不切库更新）
- **问题**：列表/项目筛选可能仍显示首次库；**RAG 事实范围不受影响**（`context()` 已隔离）。
- **验证**：集成 `test_scope_switch_excludes_old_history_and_enforces_owner` 断言 project 仍为 A。

#### L-5 `/health/ready` 硬编码 Alembic 版本 `0005_library_detail`

- **位置**：`api/health.py:31-32`
- **问题**：下一迁移发布后若未同步修改，就绪探针长期 503。
- **验证**：代码审阅。

#### L-6 `with_general` 模式仅靠提示词约束来源分区

- **位置**：`services/chat.py:408-411, 500-503`
- **问题**：无后处理或评测门禁保证不混用未授权资料；产品合规风险，非跨库泄漏。
- **验证**：无 parametrized `with_general` 自动化测试。

#### L-7 Ingest 阶段缺少压缩炸弹/极端 XML 资源上限

- **位置**：`services/ingestion.py:64-96, 111-150`；上传仅 `max_upload_bytes`（`materials.py:226`）
- **问题**：20MiB 内高压缩 DOCX 或 OCR 密集页可耗尽 worker CPU/内存，影响共享 worker。
- **验证**：单测有页数/像素上限，**无** zip bomb 用例。

#### L-8 历史上下文：任一片段 `retrieval_enabled=false` 则整轮历史排除

- **位置**：`services/chat.py:309-314`
- **问题**：用户关闭单个片段检索后，同 scope 的已完成轮次可能全部不进 `history`，影响多轮连贯性（非安全）。

#### L-9 Access JWT 刷新后旧 Bearer 在 15 分钟内仍有效

- **位置**：`services/auth.py:52-57`（设计权衡）
- **问题**：泄露 access token 窗口内仍可调用 API。

#### L-10 前端 access token 存模块级内存，XSS 可窃取

- **位置**：`frontend/src/api/client.ts:7-8, 34-44`
- **问题**：不落 localStorage 是加分项，但 XSS 仍可 hook `fetch` 或读闭包。

#### L-11 `retry` 加载知识库未显式校验 `owner_user_id`

- **位置**：`services/chat.py:217-219`
- **问题**：依赖 `run.owner_user_id` 与 `visible_indices`；缺少对 KB owner 的显式断言（纵深防御）。

---

## 5. 未发现或已排除项（避免误报）

- **跨用户读知识库/资料/引用**：所有权链与集成测试设计一致，本轮静态阅读未见绕过路径。
- **切库后历史作事实依据**：`context()` 过滤 kb、`index_version_ids`、`answer_mode`，并排除含禁用片段引用的轮次（见 L-8 产品副作用）。
- **路径穿越上传名**：`Path(...).name` 取 basename（`materials.py:213`）。
- **聊天 CSRF**：流式接口使用 Bearer，不依赖 Cookie 提交（符合 `api-integration.md`）。

---

## 6. 修复计划

### 6.1 建议批次与顺序

| 批次 | 目标 | 项 |
| --- | --- | --- |
| **P0** | 会话安全基线 | M-1 refresh 轮转；M-8 生产强制/检测 `COOKIE_SECURE`；M-2 登录限速（可先 IP 滑动窗口） |
| **P1** | 上传与检索纵深 | M-4 上传魔数 + pending 状态限制 `content`；M-5 `chunk_query` join owner；M-3 登录 CSRF（double-submit 或 SameSite+Origin 收紧策略文档化并测无 Origin） |
| **P2** | 工程与文档 | M-7 `ruff check --fix`；M-6 补齐 `api-integration.md` 库详情章节；L-5 health 改为「>= 最小版本」或读 migration 头 |
| **P3** | 加固与体验 | L-3 page 上限；L-7 ingest 解压限额；L-6 `with_general` 评测用例；L-4 project 字段策略产品确认 |

### 6.2 分项修复要点

#### M-1 Refresh 轮转

- **思路**：`refresh` 成功时生成新 refresh token，更新 `UserSession.refresh_token_hash`，Set-Cookie 覆盖；可选保留短宽限期使旧 token 单次失败。
- **涉及文件**：`services/auth.py`、`api/v1/auth.py`
- **测试**：扩展 `tests/integration/test_auth.py`：refresh 后旧 refresh Cookie 401；新 Cookie 可继续 refresh。
- **验收**：刷新两次后 DB 中 hash 变化；窃取的旧 refresh 不可用。

#### M-2 登录限速

- **思路**：反向代理层限速或应用内按 IP+邮箱计数（如 5 次/分钟），返回 429 + `retryable`。
- **涉及文件**：`api/v1/auth.py` 或中间件新模块
- **测试**：集成测连续失败触发 429。
- **验收**：超限请求不执行 Argon2 路径或仍有恒定时间（防账号枚举）。

#### M-3 登录 CSRF

- **思路**：注册/登录要求一次性 CSRF（同 refresh 模式）或强制 `SameSite=Strict` 并文档化仅同源表单；拒绝可疑跨站 POST。
- **测试**：无 Origin 的跨站 POST 不种会话（按选定策略）。
- **验收**：OWASP 登录 CSRF 场景不成立。

#### M-4 上传校验

- **思路**：上传时 sniff 前几 KB；`parse_status != ready` 时 `content` 返回 409 或 404；失败类型不写入可下载 MIME。
- **涉及文件**：`api/v1/materials.py`、`services/ingestion.py`
- **测试**：单元测假 PDF 后缀；集成测 pending 不可 content。
- **验收**：与文档 20MiB/类型列表一致；恶意文件不长期以错误 MIME 可下载。

#### M-5 检索 owner 联结

- **思路**：`chunk_query` join `KnowledgeBase` 并 `where KnowledgeBase.owner_user_id == :owner`（owner 从 run 传入）。
- **测试**：集成构造「他人 index id 误入 scope」应 0 结果或 prepare 失败。
- **验收**：SQL explain 含 owner 条件。

#### M-6 文档

- **思路**：在 `api-integration.md` 增加「库详情与片段」：overview、sync、fragments、retrieval PATCH、classification 流程与错误码。
- **验收**：与 OpenAPI 路径一致；示例含 Bearer。

#### M-7 Ruff

- **思路**：`uv run ruff check . --fix` 并纳入 CI。
- **验收**：`ruff check` 0 错误。

### 6.3 测试缺口汇总（建议新增）

| 场景 | 建议测试类型 |
| --- | --- |
| Refresh 轮转 | 集成 `test_auth.py` |
| 登录限速 / 无 Origin 登录 CSRF | 集成 + 可选 e2e |
| 上传魔数、pending content | 单元 + 集成 |
| `chunk_query` owner | 集成 `test_chat.py` |
| `with_general` 空库与来源标注 | 集成或 eval 夹具 |
| 库详情 `update_retrieval` 跨库 chunk id | 已有 `test_library_detail_*`，需跑 DB |
| Worker lease 恢复 | 已有 `test_ingestion_db.py`，需跑 DB |

---

## 7. 文档与实现差异（非缺陷但需跟踪）

| 文档 | 差异 |
| --- | --- |
| `docs/product/homepage-requirements.md` | 文首声明「本次只交付文档，不改代码」与当前实现状态不一致；应以 `product-positioning-and-roadmap.md` 补记为准 |
| `docs/architecture/*` | 产品文档注明旧架构快照未对齐，审阅未以此验收 |
| `docs/api-integration.md` | 缺库详情 API（见 M-6） |

---

## 8. 审阅人备注

- 本报告问题均附有**文件路径与行号**；标「疑似」者本轮无运行证据——本次集成测试未跑，**隔离类结论不排除在未测 DB 行为下存在边界 bug**。
- 修复实施应继续在 `codex` 分支按 AGENTS 准则小步提交，并为 M-1～M-5 优先补充可复现集成测试。

---

*报告生成：Cloud Agent 审阅任务，2026-11-16。*