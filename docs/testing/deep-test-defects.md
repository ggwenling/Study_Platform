# Deep-test defects and false positives (baseline `20a20b96`)

Isolated synthetic / MOCK context only — not production breach claims.

## DEEP-SEC-01 — **RETRACTED (false positive)**

| 字段 | 值 |
| --- | --- |
| 原报告 | U1 搜 `MOCK-INDEX-CANARY-U2-BRAVO` 时 `item_count`>0 |
| 根因 | Harness 断言 `item_count==0`；查询与 U1 正文共享词素 `MOCK`/`INDEX`/`CANARY`，`lexical_query_terms` + OR `tsquery` 会命中**本人** KB/材料/fragment（见 `resources.py` `search()` 中 `owner_user_id == user.id`） |
| 复验 | `validate_deep_sec01.py`：2× 清库 + 不相交 canary，`unauthorized_leaks==0`（`artifacts/deep-test/sec01-validation-20261121T170500Z/`） |
| 代码路径 | `backend/src/study_platform/api/v1/resources.py`（约 466–632 行）始终按当前 JWT 用户过滤 owner |
| 处置 | 已改 `run_indexed_isolation.py`：按 DB 归属 + 精确 canary 判定泄漏，不再用裸 `item_count` |

## DEEP-HARNESS-01 — 共享测试库 worker 队列污染

integration `test_ingestion_db` 在 MOCK 入库后 `run_once` 可能 claim 他人作业；Phase 2 将 worker 集成子集标为 Phase 1 gate，队列样例用 unit 测试。