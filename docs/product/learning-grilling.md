# 学习功能需求 Grilling 纪要

> 状态：**已达成共识**（第 1 轮确认 + 自动落定后续节点）｜2026-11-16  
> 实现计划：[learning-implementation-plan.md](learning-implementation-plan.md)  
> 依据：[产品定位与路线图](product-positioning-and-roadmap.md)、[首页需求](homepage-requirements.md)  
> 方法：按 [grilling skill](https://github.com/mattpocock/skills/blob/main/skills/productivity/grilling/SKILL.md) 建立设计决策树，分轮提问直至 frontier 清空。  
> **在用户确认达成共识前不开始实现。**

---

## 一、产品前提（已定，来自现有文档）

| 项 | 状态 | 来源 |
| --- | --- | --- |
| 左栏独立「学习」入口，共用知识库 / 解析 / 检索 / 引用底座 | ✅ 已定 | 路线图 §下一阶段、首页 §5 |
| 题目分发 → 图片作答 → 判断对错；用户可手动判对 / 判错 / 更正 | ✅ 已定 | 同上 |
| 不引入评分、满分、排名、考试规划主线 | ✅ 已定 | 同上 |
| P0 不放无功能的学习占位死链 | ✅ 已定 | 首页 R24 |
| 题源、参考答案管理、识别不清流程、人工覆盖细则 | ✅ 按 Q1–Q8 + §4.2 自动落定 | 见实现计划 |

---

## 二、本仓库可复用底座（事实调研）

> 区分：**已实现** / **有表无业务** / **与新产品冲突**。未在本地跑通学习链路。

### 2.1 已实现、可直接复用

| 能力 | 证据路径 | 说明 |
| --- | --- | --- |
| 多知识库与权限隔离 | `backend/src/study_platform/api/v1/resources.py` | 与聊天一致的 owner 校验 |
| 资料上传、OCR、分块、向量索引 | `backend/src/study_platform/services/ingestion.py` | RapidOCR；800 字窗口分块；pgvector |
| 检索与引用 | `backend/src/study_platform/services/chat.py`、`services/citations.py` | 作用域 CTE、RRF、`message_citations` |
| 原图 / 页预览 | `backend/src/study_platform/api/v1/materials.py` | 学习图片作答可复用存储与预览 |
| 异步作业框架 | `backend/src/study_platform/persistence/models/operations.py`（`AiJob`） | `job_type` 已预留 `quiz`、`grade`，worker 现仅 `ingest` / `classification` |
| 前端壳层 | `frontend/src/App.tsx`、`api/client.ts` | 侧栏可增入口；尚无学习页 |

### 2.2 有数据模型、无 API / 无 worker

| 领域 | 模型文件 | 与新产品关系 |
| --- | --- | --- |
| 测验全链路 | `persistence/models/quizzes.py` | `quiz_items`、`quiz_item_sources`（chunk 引用同构于聊天引用）、`quiz_attempts`、`quiz_responses` |
| 错题本 | `quizzes.py` → `WrongQuestion` | 状态 `open` / `reviewing` / `mastered` |
| 试卷原题抽取（设计） | `persistence/models/materials.py` → `SourceQuestion` | 摄取流程**未写入** |
| 闪卡 / SM-2 字段 | `persistence/models/learning.py` | `ease_factor`、`due_date` 等；**无生成与复习 API** |
| 课程锚点 | 多数学习表 `course_id` FK | 产品容器已是 `knowledge_bases`（`alembic/versions/0002_personal_rag.py`）；需适配，不能原样当契约 |

### 2.3 刻意不沿用

- 架构草案中的 Tutor / LangGraph 考试流、`score` / `total_score` 字段（首页 §7 已说明）
- 旧考研工作台、科目目录、倒计时（首页 V2 已废弃）

---

## 三、外部参考调研摘要

> 仅引用本 agent **实际打开过的** 仓库文件。标注：**自述** / **源码** / **推论**（未运行、未实测效果）。

### 3.1 Synapse（RAG 学习助手）

- **自述**：[README](https://github.com/manav-2812/synapse/blob/5b703caca014cbfe71c4a09238e68661e9526a1f/README.md) — 聊天、自动生成测验 / 笔记、SM-2 闪卡。  
- **源码**：导航含 Chat / Documents / **Quiz** / Flashcards / Notes — [nav.ts @ 5b703ca](https://github.com/manav-2812/synapse/blob/5b703caca014cbfe71c4a09238e68661e9526a1f/frontend/src/components/layout/nav.ts)。  
- **源码**：`StudyService.generate_quiz` — 先 `retrieve` 片段，再 LLM JSON 出题；`submit_quiz` 字符串精确匹配并算 **score 比例** — [study_service.py @ 5b703ca](https://github.com/manav-2812/synapse/blob/5b703caca014cbfe71c4a09238e68661e9526a1f/backend/app/services/study_service.py)。  
- **推论**：题源= RAG+生成、判题=规则匹配+计分；与本项目「图片作答、无评分」需分叉；可借鉴检索出题与测验数据流，不借鉴打分 UX。

### 3.2 FSRS / 间隔重复（open-spaced-repetition）

- **自述**：[py-fsrs README @ main](https://github.com/open-spaced-repetition/py-fsrs/blob/main/README.md) — `Scheduler.review_card`、四档评分、JSON 序列化；README 指向 Rust 版 [fsrs-rs](https://github.com/open-spaced-repetition/fsrs-rs) 供 Anki 对齐。  
- **本仓库**：闪卡表为 **SM-2 形态**（`learning.py`），非 FSRS。  
- **推论**：若下阶段要做「复习调度」，需在产品层先决定是否上间隔重复；技术上可后装 `fsrs` 包，与现 SM-2 字段不直接兼容。

### 3.3 从文档生成测验的 RAG 项目

**A. notebookllm-minus（NotebookLM 替代，含测验制品）**

- **自述**：仓库描述称支持 flashcards、**quizzes** 且可回到来源（[仓库首页](https://github.com/omarTBakr/notebookllm-minus)）。  
- **源码**：`QuizService` — 四选一、可追溯 passage、选项 shuffle、schema 校验失败重试 — [QuizService.py @ 7c29ecc](https://github.com/omarTBakr/notebookllm-minus/blob/7c29ecc0aba72c41f119cdf60ca40cf160d4343f/src/application/services/studio/QuizService.py)。  
- **推论**：强调**题目—段落溯源**与结构化校验；适合借鉴 `quiz_item_sources` 设计，非拍照作答。

**B. Open Notebook（lfnovo/open-notebook）**

- **自述**：[README @ main](https://github.com/lfnovo/open-notebook/blob/main/README.md) 强调 chat、search、notes、podcast、**Content Transformations**；**未宣称内置测验模块**。  
- **源码**：[transformations.md @ main](https://github.com/lfnovo/open-notebook/blob/main/docs/3-USER-GUIDE/transformations.md) 内置 **Reflection Questions** 等 transformation，输出为带来源的 insight。  
- **推论**：可把「出题」建模为可配置 transformation + 持久化 insight；与本项目「学习入口」不同，但可参考**用户自定义提示词生成衍生内容**。

**C. Khoj**

- **自述**：[README @ master](https://github.com/khoj-ai/khoj/blob/master/README.md) — 个人 RAG、agents、自动化；**未列出测验 / 闪卡**。  
- **源码检索**：`quiz` / `flashcard` 无命中（GitHub code search，2026-11-16）。  
- **推论**：学习场景需用 **agents + 文档检索** 自建，无现成测验产品面；可借鉴多源文档 RAG 与自托管形态，不作为测验 UI 参考。

### 3.4 拍照作答 / 批改 / 人工复核

**HUC（手写答卷批改）**

- **自述**：[README @ main](https://github.com/SaroM0/huc/blob/main/README.md) — 扫描 PDF/照片 → **本地视觉 LLM OCR** → 按 rubric 多 agent 打分 → **人工 finalize**。  
- **源码**：`[illegible]` 标记、置信度不足可进人工、`huc finalize` — 同上 README CLI/API 表。  
- **推论**：与本产品「识别不清不能默默判对」「用户可更正」高度相关；全链路偏重**考试批卷**与计分 CSV，首版可只借鉴 **OCR→判题→待人工→finalize** 状态机，不做多 criterion 分数。

---

## 四、学习功能设计决策树

图例：`✅ 已定`｜`⏳ 本轮`｜`🔒 依赖父节点`｜`🕐 后续轮`

```text
[✅] 独立学习入口 + 无评分 + 图片作答 + 人工可改判
 │
 ├─[⏳ Q1] 学习范围组织（知识库 / 跨库 / 学习集）
 │   └─🔒 题目列表默认过滤规则
 │   └─🔒 course_id → knowledge_base_id 迁移策略
 │
 ├─[⏳ Q2] 首版题源主次（抽取 vs AI 生成 vs 双轨）
 │   └─🔒 摄取是否写入 source_questions
 │   └─🔒 生成 job 与 quiz_items 生命周期
 │
 ├─[⏳ Q3] 首版题型（主观拍照 / 含 MCQ / 仅资料原生题）
 │   └─🔒 作答 UI（纯图片 vs 可选文字）
 │   └─🔒 自动判题算法（精确匹配 vs LLM rubric）
 │
 ├─[⏳ Q4] 本阶段是否内置错题本
 │   └─🔒 wrong_questions 进入条件与「已掌握」定义
 │
 ├─[⏳ Q5] 本阶段是否做间隔重复（闪卡 / FSRS）
 │   └─🔒 与「题目分发」关系（并行入口 or 不做）
 │
 ├─[⏳ Q6] 判题结果状态机（对/错 + 待确认）
 │   └─🔒 识别不清 / 低置信度 UX 文案与可重试
 │   └─🔒 人工覆盖是否留审计链
 │
 ├─[⏳ Q7] 参考答案来源与可编辑性
 │   └─🔒 展示给学生 vs 仅批改使用
 │   └─🔒 与资料 chunk 引用的一致性
 │
 └─[⏳ Q8] AI 自动判题力度（首版）
     └─🔒 是否展示 OCR 中间结果
     └─🔒 与聊天 OCR 能力边界声明
```

**第 1 轮 frontier**：Q1–Q8 已全部确认「是」。  
**第 2 轮（自动）**：产品负责人授权按推荐答案落定 §4.2 节点，不再单独开问。

### 4.2 自动落定决策（推荐即最终）

| 节点 | 最终决定 | 依据 |
| --- | --- | --- |
| 学习范围 | 单知识库 | Q1 |
| 题源 | 双轨；抽取=启发式+表预留 | Q2 |
| 题型 | 主观为主 | Q3 |
| 错题本 | 纳入 | Q4 |
| 闪卡/FSRS | 不做 | Q5 |
| 三态判题 | 必须 | Q6 |
| 参考答案 | 固化+用户可改 | Q7 |
| 判题力度 | OCR+门槛+桩/LLM | Q8 |
| `course_id` 迁移 | **保守**：保留列+锚点 upsert | 高风险项 |
| `source_questions` worker | 首版不接 | 可回退 |
| LLM 生成题 | 首版桩+检索 | 测试替身 |

---

## 五、第 1 轮问题（frontier）

> 请产品负责人逐题回复。回复「是」即接受推荐选项；若不全接受，请写明你的选择。

---

❓ **Q1 - 学习范围怎么选**：进入学习后，题目是否默认只从**当前选定的一个知识库**里出（和聊天一样每次要先选库），而不是跨库混题或单独维护一套与知识库无关的「学习集」？

➡️ **是**——与首页 F01/F02 的「每轮范围」一致，服务端校验库内资料，降低权限与引用复杂度。Synapse 用 `document_scope` 限定资料（[study_service.py](https://github.com/manav-2812/synapse/blob/5b703caca014cbfe71c4a09238e68661e9526a1f/backend/app/services/study_service.py)），思路类似。

---

❓ **Q2 - 首版题源策略**：首版是否采用 **「双轨、但有主次」**：资料里能识别为「原题」的走抽取入库（对齐本仓库已有 `source_questions` 表设计）；其余题目在用户选定的库内用 **检索 + AI 生成**，且每道题都尽量绑定可回看的 chunk 引用（对齐 `quiz_item_sources` / notebookllm-minus 的 passage 溯源）？

➡️ **是**——兼顾真题卷与讲义两种资料；抽取可异步（`AiJob`），生成逻辑可参考 Synapse / notebookllm-minus，但不在首版承诺抽取准确率。

---

❓ **Q3 - 首版题型**：首版是否聚焦 **简答 / 填空 / 计算等需要手写或拍照的主观题**（配合已定的「图片作答」），而把 **选择题** 作为次优先（仅当资料中已有结构化 MCQ 或抽取到选项时支持），不首版强推在线点选作答？

➡️ **是**——与产品「图片作答」一致；避免做成 Synapse 式纯文本 MCQ 点选为主。MCQ 仍可展示为「拍选项或手写答案」。

---

❓ **Q4 - 错题本是否纳入本阶段**：用户判错或改为判错后，是否 **自动进入错题本**（可复习、可标「已掌握」），作为学习闭环的一部分，而不是仅当次显示对错？

➡️ **是**——表 `wrong_questions` 已存在且与产品「回看可辨认最终结果」一致；首版可不做过期提醒，只做列表与再次练习入口。

---

❓ **Q5 - 间隔重复 / 闪卡是否纳入本阶段核心**：左侧「学习」首版是否 **只做「题目分发 + 作答 + 判对错 + 错题本」**，而把 **闪卡、FSRS/SM-2 复习调度** 明确放到后续（即使数据库已有 `flashcards` 表）？

➡️ **是**——路线图学习阶段未要求闪卡；Synapse / FSRS 能力留作增强，避免首版双入口（练习 vs 卡片）分散验收。

---

❓ **Q6 - 判题状态机**：除「对」「错」外，是否 **必须** 支持第三态 **「待确认」**（含：OCR 不清、模型低置信、多空题部分不可判），且该态 **禁止** 默认展示为已对或已错？

➡️ **是**——对齐首页 §5「不能因最终结果二元就强行猜一个」；参考 HUC 的 illegible / 人工 finalize 思路，但由**用户**在本产品内确认而非教师批卷。

---

❓ **Q7 - 参考答案怎么存、谁能改**：是否在出题或抽取时就把 **参考答案固化** 到题目记录，并链到资料引用；用户 **手动判对 / 判错 / 更正** 时允许修改参考答案，且 **以用户最后一次确认为准** 写入「最终结果」，系统保留自动判题记录供回看？

➡️ **是**——区分「机器建议」与「用户裁定」；与「不评分」一致（无 partial score，只有对/错与依据）。notebookllm-minus 侧重标准答案在制品内固定，本产品更强调 **用户覆盖权**。

---

❓ **Q8 - 首版自动判题力度**：首版是否采用 **「AI 尝试批改 + 置信度门槛」**：对图片先做 OCR（复用现有能力），再按参考答案 / rubric 用模型判对错；**低于门槛一律进「待确认」**，由用户点选判对、判错或改答案，而不是首版就要求全自动高准确批改？

➡️ **是**——对齐路线图「不把 OCR 成功当作判题正确」；HUC 表明手写 OCR+批改成本高，本产品首版以 **人可纠偏** 为安全网。

---

## 六、回复记录

| 轮次 | 日期 | 状态 |
| --- | --- | --- |
| 1 | 2026-11-16 | Q1–Q8 全部「是」 |
| 2（自动） | 2026-11-16 | §4.2 按授权自动落定；进入实现 |

---

## 七、实现状态

- 实现分支：`cursor/learning-feature-9b72`（依赖 PR #2 基线）。  
- 代码与验收见 [learning-implementation-plan.md](learning-implementation-plan.md)。