# Deep-test reproduced defects (baseline `20a20b96`)

记录于隔离 VM、`study_platform_test`、**provider=MOCK** 索引路径。非 live RAG 质量结论。

## DEEP-SEC-01 — 跨用户全文搜索泄漏（P0）

| 字段 | 值 |
| --- | --- |
| Harness | `run_indexed_isolation.py` |
| 复现 | U2 材料经 MOCK 嵌入索引后，U1 以 `MOCK-INDEX-CANARY-U2-BRAVO` 检索 |
| 期望 | `item_count == 0` |
| 实测 | `item_count` 为 2–7（多次运行，随库中历史 mock 材料累积变化） |
| 正向对照 | 各 owner 本人检索同一 canary：**pass** |
| Notes 边界 | U2 读 U1 note：**404 pass** |
| 产物 | `artifacts/deep-test/phase2-*/phase2_manifest.json` → `indexed_isolation` |

**处置：** 产品修复需单独确认；本 PR 仅记录与矩阵 FAIL 状态。

## DEEP-HARNESS-01 — 共享库排队污染 worker 集成样例

| 字段 | 值 |
| --- | --- |
| 现象 | `mock_ingest_index` 之后运行 `test_ingestion_db` 中依赖 `ingestion.run_once` 的单测时，job 停留在 `queued` |
| 原因 | 共享 `*_test` 库中已提交 MOCK 任务优先被 claim |
| 缓解 | Phase 2 套件将 `worker_recovery_sample` 排在 `mock_ingest_index` **之前** |
| 说明 | 非产品缺陷；完整 worker 生命周期仍以 MOCK 路径 + 负向隔离 FAIL 为证据 |