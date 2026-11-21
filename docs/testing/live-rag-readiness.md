# Live RAG readiness（PR #19）

**产品基线：** `20a20b96` · **Harness：** 见 PR 头 · **语境：** 隔离 VM、合成语料、无生产库

## 花费授权（本轮）

| 项 | 值 |
| --- | --- |
| 用户确认 | **无上限**，仅限本次有界深度评测（非无限期、非其他采购） |
| Harness 标记 | `DEEP_TEST_SPEND_APPROVED=unlimited_finite`（写入 `deep-test.env` 后生效） |
| 执行约束 | 小步切片、禁止空转重试；`artifacts/deep-test/live-<run-id>/cost-ledger.json` |

**花费授权 ≠ 凭证已配置。**

## 本 VM 凭证与能力核查（2026-11-21）

| 字段 | 呈现 |
| --- | --- |
| `DEEPSEEK_API_KEY` | **absent** |
| `EMBEDDING_API_KEY` | **absent** |
| `DEEP_TEST_ALLOW_LIVE` | **unset** |
| `DEEP_TEST_ISOLATION` | `1` |
| `DATABASE_URL` | `study_platform_test`（隔离） |
| Cursor environment secrets | **未注入**（personal env，无 environment.json） |

**结论：Live 阶段未启动**（无健康探测、无 provider 调用）。

## 数据与 CLI 就绪（已验证、无模型）

```bash
source .local/deep-test/env/deep-test.env
cd backend
uv run study-eval-rag --dataset evaluation/deep-test/questions.jsonl   # 100Q dry
uv run python evaluation/deep-test/harness/verify_eval_dataset.py
uv run python evaluation/deep-test/harness/check_model_budget.py
```

- 策略（`--live` 时）：`baseline` | `rewrite` | `compare` | `hybrid` | `hybrid_compare`
- `--retrieval-only`：跳过 chat，**仍要 embedding**
- 评测库：`study_eval_<hex>_test`（CLI 自建，见 `evaluate_rag.py`）

默认模型契约（无密钥时仅元数据）：chat 默认 `deepseek-flash` + 可配置 base URL；嵌入默认 `qwen3.7-text-embedding-flash`（DashScope 兼容端点）。**不强制 DeepSeek**——任何 OpenAI 兼容 chat/embedding 且 **1024 维** 即可。

Vision / rerank：本次 **`study-eval-rag` 路径不包含** VLM 或 rerank 直连；OCR/vision 属采集域，另计。

## 解除 BLOCKED 所需配置（勿在聊天中粘贴明文密钥）

1. **Cursor Cloud Agent 环境机密**（推荐）：在 Personal/Team Environment 为 **本仓库** 添加  
   - `DEEPSEEK_API_KEY`（或你的 chat 提供商密钥）  
   - `EMBEDDING_API_KEY`  
   - `DEEP_TEST_ALLOW_LIVE=1`  
   - `DEEP_TEST_SPEND_APPROVED=unlimited_finite`  
   重新启动 agent 后 harness 可读；**不要**把密钥写入 PR 或聊天记录。

2. **或** 仅写入 `.local/deep-test/env/deep-test.env`（已 gitignore），本地/agent 文件挂载可读。

3. `TEST_DATABASE_URL`：postgres 角色需 **CREATEDB**（bootstrap 默认 `postgres` → `study_platform_test` 通常可用）。

4. 可选：产品内 **Model Connection**（`model_connections_key`）供应用运行时；**`study-eval-rag --live` 当前读平台 `.env` 密钥**，deep-test 仍以 env 密钥为准。

## 有凭证后的有界协议（待执行）

| 步骤 | 范围 | 产出 |
| --- | --- | --- |
| L0 | Provider 连通性（1 embed + 1 chat，合成短句） | `live_health.json` |
| L1 | `--retrieval-only`，**5–10 题**切片 | `retrieval_metrics.json` + token 若有 |
| L2 | `--strategy compare` 同切片 | 对照 + 分母 |
| L3 | 可选单次 **100Q** `compare` | `report.json`、`gate.json`（**非人工复核**） |

费用：记录 provider 返回的 usage；无返回则 **cost: unknown**，仅记调用次数。

## 仍 BLOCKED

- 浏览器 E2E、Windows soak、离线门重复跑（本轮不要求）
- Live RAG 质量结论，直至 L1+ 实际执行