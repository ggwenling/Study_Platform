# DEEP-SEC-01 evidence validation（撤销误报）

**语境：** 隔离 `study_platform_test`、MOCK 嵌入、合成账户；**非生产侵入结论**。

## 结论

**FALSE_POSITIVE** — 2 次独立清库复现，`unauthorized_leaks=0`。先前 P0 来自 harness 误用 `item_count==0`。

## 复验命令

```bash
source .local/deep-test/env/deep-test.env
cd backend && uv run python evaluation/deep-test/harness/validate_deep_sec01.py
```

- 每次迭代 ~3–4s（含 `ci_bootstrap_postgres.py` 清库 + MOCK ingest）
- 产物：`artifacts/deep-test/sec01-validation-<run-id>/summary.json`（gitignored）

## 机制（为何 item_count 可 >0 仍无泄漏）

1. `GET /api/v1/search` 在 `resources.py` 对各子查询使用 `KnowledgeBase.owner_user_id == user.id` 与 `Material.owner_user_id == user.id`。
2. 查询 `MOCK-INDEX-CANARY-U2-BRAVO` 经 `lexical_query_terms` 拆出共享词素 `mock`/`index`/`canary`，`plain_tsquery` 以 **OR** 组合（见 `lexical.py`），故 U1 **自己的** fragment/KB 标题也会命中。
3. 污染库上多次 MOCK 运行会放大 `item_count`；与跨账户无关。

## 迭代 1 摘要（`NYX-VIOLET-9920-<tag>` 作 U2 canary）

| 检查 | 结果 |
| --- | --- |
| 未登录 search | 401 |
| U1 JWT ↔ `/api/v1/me` | 一致 |
| U1 搜 U2 canary | `item_count=3`，`unauthorized_leaks=0`；命中均为 U1 KB/材料/fragment（共享 run_tag 词素） |
| U1/U2 正向对照 | 各 ≥1 hit |
| DB `Material.owner_user_id` vs ingest | 与注册用户一致，无共享 owner |

## Harness 修正

- `search_leak_checks.classify_search_items` — 泄漏 =  peer `owner_user_id` / peer KB或 material ID / 精确 peer canary 出现在 excerpt
- `run_indexed_isolation.py` — 采用上述判定，不再要求 `item_count==0`

## 性能数字免责

Phase 2 `run_load_baseline.py` 为 **TestClient in-process**；p50/p95 **不代表** 生产 HTTP、硬件或 WAN 吞吐。