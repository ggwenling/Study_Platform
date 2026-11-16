# 学习功能实现计划（首版）

> 状态：实现中｜2026-11-16  
> 依据：[learning-grilling.md](learning-grilling.md)（Q1–Q8 已确认；余下节点按推荐自动落定）  
> **分支**：`cursor/learning-feature-9b72`（基于 `cursor/security-fixes-round2-db11`，待 PR #2 合并后可 rebase 到 `codex`）

---

## 1. 范围

### 纳入首版

| 能力 | 说明 |
| --- | --- |
| 选库出题 | 仅当前选定知识库；`visible_indices` + 检索 |
| 题源双轨 | 分块文本启发式抽题 + RAG 片段生成简答/填空；绑定 `quiz_item_sources` |
| 练习会话 | `quiz` / `quiz_attempt` / `quiz_responses` |
| 拍照作答 | PNG/JPEG → RapidOCR → 判题 |
| AI/桩判题 | 字符串比对 + 置信度；低于 `learning_confidence_threshold` → `pending` |
| 三态 | `correct` / `incorrect` / `pending` |
| 用户改判 | PATCH verdict；可改参考答案（写入 `reference_answer_effective` 与 `answer_key`） |
| 错题本 | 判错入 `wrong_questions`；可标 `mastered` |
| 前端 | 左栏「学习」；练习 + 错题本；移动 Web 支持 `capture` 上传 |

### 明确不做

评分/满分、闪卡、FSRS/SM-2 调度、复习提醒、聊天一键出题、试卷级 rubric 多 agent（HUC 式）、`source_questions` 异步摄取 job（表保留，worker 未接）。

---

## 2. 保守架构决策（自动落定）

| 决策 | 选择 | 理由 |
| --- | --- | --- |
| `course_id` vs `knowledge_base_id` | **保留 `course_id` 列**；API 只用 `knowledge_base_id`；`ensure_course_anchor` 按需 upsert `courses` 同行 | 避免大范围改 37 表 FK；可回退 |
| 评分字段 | DB 保留 `score`/`max_score`；API **不返回、不计分**；attempt `max_score=len(items)`，`total_score=0` | 不删列，降迁移风险 |
| 迁移 0006 | 仅 `quiz_responses` 增 `confidence`/`verdict`/`verdict_source`/`reference_answer_effective` | 最小 DDL |
| LLM 出题 | 首版 **桩生成**（检索命中分块 + 启发式题干）；有密钥时可后续接 chat 模型 | 测试无密钥可跑通 |
| 桩检索 | `learning_stub_grader=true` 时 **跳过向量检索**，按作用域顺序取分块 | 无嵌入 API 可跑通出题 |
| 原题抽取 | 启发式（题号正则）嵌入 generate 路径；**不写** `source_questions` 表 | 表与 worker 后续再接 |
| PR 依赖 | 基于 **PR #2** 安全分支，降低与鉴权/upload 修复冲突 | 用户指定 |

---

## 3. 数据与迁移

- **Alembic**：`0007_learning_verdict.py`（依赖 PR #2 的 `0006_security_hardening`）
- **模型**：`QuizResponse` 增判题字段（见上）
- **存储**：作答图片 `.local/storage/learning-answers/{response_id}/answer.png`

---

## 4. API（`/api/v1/learning`）

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| POST | `/knowledge-bases/{kb_id}/quizzes/generate` | 生成练习（body: `count`） |
| POST | `/attempts` | 开始作答（body: `quiz_id`） |
| GET | `/attempts/{id}` | 会话详情 |
| POST | `/responses/{id}/answer-image` | 上传作答图 |
| PATCH | `/responses/{id}/verdict` | 用户改判 / 改参考答案 |
| GET | `/wrong-questions` | 错题列表 |
| POST | `/wrong-questions/{id}/mastered` | 标已掌握 |

权限：全部校验 `owner_user_id`；出题前 `owned_kb`。

---

## 5. 前端

- `App.tsx` 导航 + `/learning` 路由
- `Learning.tsx`：UI Arc `Button` / `EmptyState`
- `api.client.ts` → `api.learning.*`

---

## 6. 测试与验收

| 编号 | 场景 |
| --- | --- |
| L01 | 未登录不可调用学习 API |
| L02 | 他用户不可访问他人练习 |
| L03 | 空库出题返回 `library_not_ready` |
| L04 | 拍照作答 → verdict 三态之一 |
| L05 | 用户改判为错 → 错题本 +1 |
| L06 | 标 mastered 后错题列表减少 |

自动化：`tests/unit/test_learning_grade.py`、`tests/integration/test_learning_api.py`；全量 `RUN_DB_TESTS=1 pytest`。

---

## 7. 分批提交顺序

1. 文档（grilling 共识 + 本计划）  
2. 迁移 + 模型 + 配置  
3. 服务层 + API  
4. 测试  
5. 前端 + 样式  
6. OpenAPI 契约（可选跟进）

---

## 8. 已知风险

- OCR 对手写/模糊图不可靠 → 依赖 `pending` + 人工改判（产品已定）  
- 桩判题非语义理解 → 生产需接 LLM rubric 或提高阈值策略  
- `courses` 锚点与 KB 双写 → 长期应迁移到纯 `knowledge_base_id` FK（非首版）