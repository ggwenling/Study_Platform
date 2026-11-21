# KnowLoop 深度测试用例矩阵

**图例：** `AUTO` 自动化 · `MANUAL` 人工 · `MOCK` 无真实模型 · `BLOCKED` 未具备条件 · `DIAG` 诊断性（不阻断准备完成）

证据默认写入 `artifacts/deep-test/<run-id>/`：日志、`manifest.json`、截图/录像（浏览器阶段）。

## P0 — 采集 / OCR / 索引

| ID | 前置 | 操作 | 期望 | 严重度 | 证据 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| ING-01 | 隔离 DB；账号 U1 | 上传 `fixtures/samples/notes.txt` | 作业成功；chunks>0；活跃 index | P0 | ingest job 日志 | MANUAL Phase2 |
| ING-02 | U1 KB | 上传 `empty.txt` | 拒绝或无可索引文本；无脏活跃索引 | P0 | API 4xx + worker | MANUAL |
| ING-03 | U1 | 上传 malformed PDF 字节 | 失败可重试；错误码稳定 | P0 | worker + DB status | MANUAL |
| ING-04 | 渲染 materials `ocr-yard` png | evaluate_rag 渲染 → ingest | OCR 文本进入 chunk | P0 | chunk text | MOCK |
| ING-05 | 重复 SHA 两文件 | 连续上传 duplicate-a/b | 去重或版本策略符合产品 | P1 | materials 行 | MANUAL |
| ING-06 | `max_upload_bytes` | 运行时超大 txt | 413/422 | P1 | API body | BLOCKED |
| ING-07 | pytest `RUN_DB_TESTS=1` | `test_database_schema` 约束 | FK/CHECK 拒绝非法索引态 | P0 | pytest | AUTO |
| ING-08 | Worker 运行中 | 中断 worker 再启 | 作业可恢复/重试幂等 | P1 | job 表 | DIAG Phase2 |

## P0 — RAG / 检索 / 引用

| ID | 前置 | 操作 | 期望 | 严重度 | 证据 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| RAG-01 | 语料 D01 | 检索/评测 support hit | 命中 `ops-core` 引用句 | P0 | retrieval report | MOCK offline validate |
| RAG-02 | 语料 U01-08 | 无答案题 | 拒答；无 gold support | P0 | questions.jsonl | AUTO validate |
| RAG-03 | 同索引 | vector vs hybrid vs rewrite 成对 | 分指标表；不单次生成断言增益 | P0 | compare report | BLOCKED live |
| RAG-04 | 语料 M02-M03 | A 库提问 B 库干扰数字 | 不得引用 interference 文档 | P0 | citations | BLOCKED live |
| RAG-05 | `test_chat.py` | 围栏轮次/隔离 | 无跨 KB 泄露 | P0 | pytest | AUTO |
| RAG-06 | DT-Q 全量 | `validate_dataset.py` | ≥100 题；类别齐全 | P0 | stdout OK | AUTO |
| RAG-07 | workshop | hybrid hit-test API | 诊断字段完整 | P1 | pytest integration | AUTO |

语料题 ID 与类别见 `backend/evaluation/deep-test/questions.jsonl`（前缀 D/U/M/P）。

## P0 — 认证 / 隔离 / SSRF

| ID | 前置 | 操作 | 期望 | 严重度 | 证据 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| SEC-01 | study_app 角色 | 读 `study.users`；拒 `study_graph` | 权限分离 | P0 | test_runtime_access | AUTO |
| SEC-02 | study_graph 角色 | 读 checkpoints；拒 `study.users` | 图状态隔离 | P0 | test_runtime_access | AUTO |
| SEC-03 | U1/U2 各一库 | U1 读 U2 material id | 404/403 | P0 | integration pattern | AUTO partial |
| SEC-04 | ISO-02 语料 | U1 问 NIGHTJAR-3 | 不得出现 B 库专有事实 | P0 | chat answer | BLOCKED live |
| SEC-05 | SSRF mock | 模型连接 URL → 127.0.0.1 mock | 拒绝或仅允许列表 | P0 | MOCK server | BLOCKED |
| SEC-06 | `.env` | 仓库扫描 | 无密钥提交 | P0 | git grep | AUTO |

## P0 — 备份 / 迁移 / 恢复

| ID | 前置 | 操作 | 期望 | 严重度 | 证据 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| MIG-01 | 空 `_test` 库 | `alembic upgrade head` | schema 完整 | P0 | pytest bootstrap | AUTO |
| MIG-02 | 有数据 | `test_migration_roundtrip` | 降级保护/往返 | P0 | pytest | AUTO |
| BAK-01 | storage 有原件 | 导出+删库+重建+还原 | 原件 SHA 一致 | P0 | manifest | MANUAL Phase2 |
| BAK-02 | 索引 building | 中断恢复 | 无 twin active index | P0 | DB 行 | DIAG |

## P1 — 业务功能

| ID | 前置 | 操作 | 期望 | 严重度 | 证据 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| BIZ-01 | U1 | 注册→建库→聊天 SSE | 200/事件序列合法 | P1 | integration | AUTO MOCK |
| BIZ-02 | U1 | 笔记保存/导出 MD | 文件含引用 | P1 | pytest/手工 | AUTO partial |
| BIZ-03 | stub grader | 学习答题 MCQ+简答 | 阈值 pending/correct | P1 | pytest learning | AUTO |
| BIZ-04 | 模型连接 | 测试连接 | 状态机 testing→ready/fail | P1 | integration | MOCK |
| BIZ-05 | 前端契约 | `contract.test.ts` | OpenAPI 路径覆盖 | P2 | vitest | AUTO |

## P1 — 异步 / Worker / 租约

| ID | 前置 | 操作 | 期望 | 严重度 | 证据 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| ASY-01 | ingest 队列 | 并发 2 作业 | 无死锁；租约释放 | P1 | worker log | DIAG |
| ASY-02 | model lease | `task()` 上下文 | 租约表写入/清理 | P1 | DB | AUTO partial |

## P1 — UX / 多端

| ID | 前置 | 操作 | 期望 | 严重度 | 证据 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| UX-01 | Vitest | 主题/导航/聊天组件 | 用例绿 | P1 | npm test | AUTO |
| UX-02 | 浏览器 375px | 主导航/聊天 | 无阻断布局 | P1 | 录像 | BLOCKED |
| UX-03 | 键盘 | Ctrl+K 搜索 | 焦点陷阱合理 | P2 | MANUAL | BLOCKED |

## P1 — 性能 / 用量

| ID | 前置 | 操作 | 期望 | 严重度 | 证据 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| PERF-01 | 本地 API | 1/5/10 客户端 30s health | 记录 P50/P95；不设硬门槛 | P1 | load.json | BLOCKED |
| PERF-02 | 用量 API | 登录后 summary | 仅本用户 | P1 | pytest | AUTO |

## P2 — 安装 / 契约

| ID | 前置 | 操作 | 期望 | 严重度 | 证据 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| INS-01 | 干净 clone | `uv sync` + `npm ci` | 无锁漂移 | P2 | CI | AUTO |
| INS-02 | backend | `test_frontend_contract` | OpenAPI 快照一致 | P2 | pytest | AUTO |
| INS-03 | backend | `test_model_contract` | 字典含 archived_at 等 | P2 | pytest | AUTO |