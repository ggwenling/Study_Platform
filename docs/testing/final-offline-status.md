# Final offline phase status（PR #19 完成度）

| 产品基线 | `20a20b96ab99bb4176546df6e8972dbd64df51b4` |
| Harness | `586f8d0` |
| 最后整包 | **PASS** `final-20261121T172000Z` |
| 产物 | `artifacts/deep-test/final-20261121T172000Z/final_phase_manifest.json` |

## 九域状态（诚实）

| 域 | 状态 | 证据 |
| --- | --- | --- |
| P0 采集/OCR/索引 | **PASS (MOCK)** | `run_mock_ingest_index.py`；非生产 worker 池 |
| P0 RAG/检索 | **BLOCKED live** | dry/`validate_deep_sec01`；无 `--live` |
| P0 认证/隔离 | **PASS** | `run_indexed_isolation` 归属校验；`validate_deep_sec01` 2× |
| P0 备份/恢复 | **PASS** | `run_backup_restore_cycle.py` → `study_platform_restore_test` |
| P1 业务前后端 | **PARTIAL** | API smoke on restore；无浏览器 E2E |
| P1 异步/Worker | **PARTIAL** | `run_worker_process_probe.py` 真子进程 SIGTERM；租约 integration=Phase1 gate |
| P1 UX | **AUTO Vitest** + **BLOCKED** 浏览器 | Phase1 build/test |
| P1 性能 | **PASS smoke** | `run_http_load_smoke.py` localhost HTTP；TestClient 见 phase2 |
| P2 安装/契约 | **PASS** | Phase1 pytest 406 + `test_frontend_contract` in CI 路径 |

## 八项就绪

| # | 项 | 状态 |
| --- | --- | --- |
| 1 | 冻结 SHA | ✅ 产品 `20a20b96` |
| 2 | 隔离环境 | ✅ `study_platform_test` / `.local/deep-test/` |
| 3 | 模型预算 | ⚠️ 花费**已批**（无上限·有界评测）；**密钥仍缺** → live BLOCKED |
| 4 | 语料 | ✅ 100Q synthetic |
| 5 | 种子用户 | ✅ mock + backup 剧本 |
| 6 | 矩阵/门禁 | ✅ + `run_final_offline_phase.py` |
| 7 | 结果/缺陷 | ✅ artifacts + SEC01 撤销 |
| 8 | 负载/恢复 | ✅ restore + HTTP smoke（非生产 SLO） |

## 已验证命令

```bash
source .local/deep-test/env/deep-test.env
cd backend
uv run python evaluation/deep-test/harness/run_final_offline_phase.py
uv run python evaluation/deep-test/harness/run_backup_restore_cycle.py
uv run python evaluation/deep-test/harness/run_http_load_smoke.py
uv run python evaluation/deep-test/harness/validate_deep_sec01.py
uv run python evaluation/deep-test/harness/run_offline_gate.py  # 完整 406 门（耗时长）
```

## 用户动作（无密钥明文）

1. 在 **仅测试** 环境配置模型连接：`DEEPSEEK_API_KEY`、`EMBEDDING_API_KEY`（或 UI 连接）并批准花费上限。
2. 设置 `DEEP_TEST_ALLOW_LIVE=1` 与独立 `TEST_DATABASE_URL`（`CREATEDB`）。
3. 运行 `uv run study-eval-rag --dataset evaluation/deep-test/questions.jsonl --strategy compare --live --output <空目录>`。
4. Windows 长稳/物理备份：按 `test-environment.md` 在目标机执行（本 VM **NOT_RUN**）。
5. 浏览器 E2E：headed 前端 + Playwright/Cypress（云 agent **BLOCKED**）。

## BLOCKED / NOT_RUN

- Live RAG 质量、付费模型、生产 SSRF 外联
- 多小时 soak、Windows 专机、浏览器截图流
- 生产硬件认证 P95 阈值

## 证据索引（gitignored 原始件）

- `artifacts/deep-test/restore-cycle-20261121T171500Z/restore_manifest.json`
- `artifacts/deep-test/http-load-*/http_load_manifest.json`
- `artifacts/deep-test/final-*/final_phase_manifest.json`
- `artifacts/deep-test/sec01-validation-*/summary.json`