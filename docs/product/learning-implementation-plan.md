# 学习功能实现计划（首版 + LLM 第二轮）

> 状态：实现中｜2026-11-16  
> 依据：[learning-grilling.md](learning-grilling.md)（Q1–Q8 已确认；余下节点按推荐自动落定）  
> **分支**：`cursor/learning-llm-integration-817d`（基于 `cursor/github-actions-ci-0cfc`）

---

## 1. 范围

### 纳入首版

| 能力 | 说明 |
| --- | --- |
| 选库出题 | 仅当前选定知识库；`visible_indices` + 检索 |
| 题源双轨 | **LLM** 基于检索片段结构化出题 + **桩**启发式抽题；绑定 `quiz_item_sources` |
| 练习会话 | `quiz` / `quiz_attempt` / `quiz_responses` |
| 拍照作答 | PNG/JPEG → RapidOCR → 判题 |
| AI/桩判题 | 精确匹配短路 + **LLM 语义判题**；低于 `learning_confidence_threshold` → `pending` |
| 三态 | `correct` / `incorrect` / `pending` |
| 用户改判 | PATCH verdict；可改参考答案（写入 `reference_answer_effective` 与 `answer_key`） |
| 错题本 | 判错入 `wrong_questions`；可标 `mastered` |
| 前端 | 左栏「学习」；练习 + 错题本；移动 Web 支持 `capture` 上传 |

### 明确不做

评分/满分、闪卡、FSRS/SM-2 调度、复习提醒、聊天一键出题、试卷级 rubric 多 agent（HUC 式）、`source_questions` 异步摄取 job（表保留，worker 未接）。

---

## 2. 架构决策

| 决策 | 选择 | 理由 |
| --- | --- | --- |
| `course_id` vs `knowledge_base_id` | **保留 `course_id` 列**；API 只用 `knowledge_base_id`；`ensure_course_anchor` 按需 upsert `courses` 同行 | 避免大范围改 37 表 FK |
| LLM 出题 | `learning_llm.py`：`chat_model`（`deepseek-flash`）+ JSON；`source_indices` 校验；不足时 `insufficient_material` | 与 RAG/分类一致的 provider 路径 |
| 桩模式 | `LEARNING_STUB_GRADER=true` 或 **无** `DEEPSEEK_API_KEY`：启发式出题 + 字符串判题 | CI 与离线开发 |
| 判题降级 | LLM 解析失败/超时/低置信 → `pending`；日志不打印作答全文 | 避免误判 |
| 生成配置 | `LEARNING_LLM_TIMEOUT_SECONDS`、`LEARNING_LLM_MAX_RETRIES`、`LEARNING_CONFIDENCE_THRESHOLD` | 与 `Settings` 对齐 |

### 出题流程（LLM）

1. `visible_indices` 限定知识库作用域  
2. `retrieve`（桩模式则顺序取分块）  
3. 编号片段送入模型；解析 `questions[]`  
4. 丢弃 `source_indices` 越界或无效的题  
5. 写入 `quiz_item_sources`（可多片段）  
6. `generator_profile`：`learning_llm_v1` / `learning_stub_v1`

### 判题流程

1. OCR 空 → `pending`  
2. 归一化后与参考答案完全一致 → `correct`（不走模型）  
3. 桩或无密钥 → 启发式 `pending`/`incorrect`  
4. 否则 `grade_with_llm` → 置信度门槛 → 三态；`grader_profile`：`learning_llm_v1` / `learning_stub_v1`

---

## 3. 数据与迁移

- **Alembic**：`0007_learning_verdict.py`
- **模型**：`QuizResponse` 判题字段
- **存储**：作答图片 `.local/storage/learning-answers/{response_id}/answer.png`

---

## 4. API（`/api/v1/learning`）

（同首版，无破坏性变更）

---

## 5. 前端

- `Learning.tsx`：无需为 LLM 改版；后端 `generator_profile` / `grader_profile` 仅诊断用

---

## 6. 测试与验收

| 编号 | 场景 |
| --- | --- |
| L01–L06 | 同首版 |
| L07 | 假模型：JSON 重试、引用校验、资料不足、低置信 `pending` |

自动化：

- `tests/unit/test_learning_grade.py`
- `tests/unit/test_learning_llm.py`（确定性，无真实 API）
- `tests/integration/test_learning_api.py`（`LEARNING_STUB_GRADER=true`）

---

## 7. 本地验证（真实 DeepSeek）

1. `.env` 配置 `DEEPSEEK_API_KEY`、`EMBEDDING_API_KEY`（出题检索需要向量）  
2. `LEARNING_STUB_GRADER=false`  
3. 上传资料并完成索引 → 学习页选库生成练习  
4. 检查题目题干与 `quiz_item_sources` 片段一致；拍照作答观察语义判题与 `pending` 降级

---

## 8. 已知风险

- OCR 对手写/模糊图不可靠 → `pending` + 人工改判  
- LLM 出题质量依赖片段质量；不足时明确错误而非硬编题  
- `courses` 锚点双写 → 长期迁移纯 `knowledge_base_id`（非本版）