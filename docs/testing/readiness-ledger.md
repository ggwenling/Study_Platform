# Deep-test readiness ledger（2026-11-21）

| 项 | 值 |
| --- | --- |
| 产品基线 | `20a20b96ab99bb4176546df6e8972dbd64df51b4` |
| Harness | `a92b26f` on `cursor/deep-testing-prep-9b8e` |
| Phase 1 离线门 | **406** pytest pass；Vitest/build；`study-eval-rag` dry 100Q |
| Phase 2 套件 | `uv run python evaluation/deep-test/harness/run_phase2_suite.py` |
| 最近产物 | `artifacts/deep-test/phase2-20261121T171800Z/phase2_manifest.json`（gitignored） |

## Pass（本回合实测）

- DB reset guard（仅 `*_test`）
- `verify_eval_dataset` + `read_dataset`
- `study-eval-rag` dry（非 live 打分）
- `check_model_budget` fail-closed
- SSRF：`test_web_clip.py` 16 passed
- Worker 样例：`test_worker.py` + stale cancel unit
- **MOCK** 双账号 ingest+index（每用户 active_chunks≥1）
- Owner 正向 search（canary）
- Notes 跨用户 404
- Load 1/5/10（TestClient，15s/tier，0 errors）
- Backup 快照（storage SHA256 + pg_dump data-only）

## Fail（产品）

- **DEEP-SEC-01**：U1 可搜到 U2 的 `MOCK-INDEX-CANARY-U2-BRAVO`（见 `docs/testing/deep-test-defects.md`）

## Blocked

| 能力 | 缺失输入 |
| --- | --- |
| Live RAG / chat 质量 | `DEEPSEEK_API_KEY`、`EMBEDDING_API_KEY`、`DEEP_TEST_ALLOW_LIVE=1`、花费上限书面批准 |
| 浏览器 E2E / 截图 | headed stack + 前端 dev 常驻 |
| Windows  soak | 物理机/协议未执行 |
| BAK 全量 restore | 本回合 `restore_protocol: NOT_RUN` |
| 真实 provider 索引质量 | 任何 `--live` 与生产嵌入 |

## Not run（有协议）

- 长时间 soak（`test-environment.md`）
- PERF 硬 SLO 判定（仅记录 p50/p95）

## 工具与环境限制

- Ubuntu VM，PostgreSQL 16 + pgvector；`study_platform_test` only
- 无付费模型调用；嵌入 **MOCK** `[0.1]*1024`
- `artifacts/`、`.local/` gitignored — PR 仅摘要路径