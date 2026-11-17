# 用户体验改进计划（2026-11-16）

> 状态：探索与首轮交付。证据来自代码走读、`docs/` 对齐、前端 `npm test` / `npm run build`（本环境无完整后端联调）。检索/RAG 核心路径他人并行修改，本次不改相关后端。

## 优先级说明

| 级别 | 含义 |
| --- | --- |
| P0 | 阻断或明显误导，宜尽快修复 |
| P1 | 高频路径上的摩擦，价值高、改动可控 |
| P2 | 体验 polish 或需较多工程 |
| P3 | 新能力 / 大功能，仅规划 |

---

## 一、已发现痛点与方案

### P0 · 学习错题本展示英文 `status`

- **问题**：错题列表直接渲染后端枚举 `open` / `reviewing` / `mastered`（`Learning.tsx`），用户难以理解状态。
- **证据**：`frontend/src/components/Learning.tsx` 错题 `<span>… {row.status}</span>`；模型定义 `backend/.../quizzes.py` `WrongQuestion.status`。
- **方案**：前端集中映射中文标签与格式化 `last_wrong_at`（`learningUi.ts`）。
- **价值**：学习闭环可读性；**成本**：极低（纯前端）。
- **本次**：✅ 已实现。

### P0 · 移动端会话页引用侧栏默认占满屏

- **问题**：`LiveChat` 引用面板 `useState(true)`，小屏下 `data-sources="true"` 时侧栏与主对话争抢空间；用户每次需手动收起。
- **证据**：`frontend/src/styles.css` `@media (max-width: 760px)` `.chat[data-sources="false"] .source-panel { display: none }`；`LiveChat.tsx` 默认 `panel = true`。
- **方案**：`useDisclosurePreference` 记忆展开状态；首访在 `<769px` 默认收起，桌面默认展开（与首页概况一致）。
- **价值**：移动端首屏可提问；**成本**：低。
- **本次**：✅ 已实现。

### P1 · 无知识库时学习入口缺少引导

- **问题**：登录后若 `knowledgeBases` 为空，学习页仍展示空的 `<select>`，用户不知下一步。
- **证据**：`Learning.tsx` 未处理 `knowledgeBases.length === 0`。
- **方案**：`EmptyState` + 链至 `/knowledge`。
- **价值**：补齐 P0 闭环「建库 → 上传 → 学习」；**成本**：低。
- **本次**：✅ 已实现。

### P1 · 知识库处理中状态不够醒目

- **问题**：`processing_count > 0` 时仅后台轮询（`Library.tsx` L122），页面上除 Stepper 外无即时提示，用户上传后可能以为「卡住」。
- **证据**：`LibraryOverview.processing_count` 仅用于 `setTimeout` 轮询。
- **方案**：页头下增加 `role="status"` 处理中横幅。
- **价值**：处理进度可见性；**成本**：低。
- **本次**：✅ 已实现。

### P1 · 会话历史信息稀疏

- **问题**：历史列表只有标题链接，无更新时间；空状态无行动按钮。
- **证据**：`App.tsx` `HistoryPage`。
- **方案**：展示 `updated_at`，空状态 `EmptyState` +「前往首页」。
- **价值**：回访与会话管理；**成本**：低。
- **本次**：✅ 已实现。

### P1 · 首页有账号但零知识库时概况区沉默

- **问题**：右侧概况显示 `知识库 · undefined` 或 0，无明确「创建首个库」引导。
- **证据**：`Home.tsx` `overview-row` 仅展示数字。
- **方案**：`knowledge_base_count === 0` 时显示简短 onboarding 文案链至知识库。
- **价值**：新用户首流程；**成本**：低。
- **本次**：✅ 已实现。

### P2 · 全局搜索表单可访问性

- **问题**：搜索使用裸 `<label>关键词<input>`，与 Arc `Input` 不一致，错误态/描述难以扩展。
- **证据**：`App.tsx` `SearchPanel`。
- **方案**：改用 `Input` 组件。
- **价值**：一致性 + 屏幕阅读器；**成本**：低。
- **本次**：✅ 已实现。

### P2 · 学习 Tab 键盘与读屏

- **问题**：Tab 无 `aria-controls` / `tabpanel` 关联；练习按钮忙时无 `loading` 反馈。
- **方案**：补齐 WAI-ARIA Tabs 模式，出题按钮 `loading={busy}`。
- **价值**：可访问性；**成本**：低。
- **本次**：✅ 已实现。

### P2 · 资料上传/处理反馈（Composer & Library）

- **现状**：`CurrentMaterials` 轮询、错误保留与重试已较完善（`upload-workflow.test.tsx`）；Composer 有 `role="status"` 上传提示。
- **可跟进**：上传进度百分比（需 API）、多文件队列 UI、知识库页拖拽上传。
- **成本**：中–高；**本次**：未改（已有能力足够，避免范围膨胀）。

### P2 · 历史会话搜索与删除

- **问题**：仅列表，无按标题筛选、归档或删除。
- **方案**：后端分页 API + 前端筛选；需产品确认删除语义。
- **成本**：中；**本次**：未实现。

### P3 · 间隔重复复习（FSRS）

- **价值**：学习留存；**成本**：大（算法、调度、UI、数据模型）。
- **本次**：仅列入候选，不实现。

### P3 · 检索质量工具（分块编辑、Hit Testing）

- **价值**：进阶用户；**成本**：大；与当前 RAG 改造冲突。
- **本次**：不实现。

---

## 二、候选新功能（取舍）

| 功能 | 价值 | 成本/风险 | 决定 |
| --- | --- | --- | --- |
| FSRS 复习计划 | 高（长期学习） | 高 | 列入路线图，本次不做 |
| 上传拖拽 + 多文件队列 | 中高 | 中 | 下次与 worker 进度 API 一起做 |
| 会话置顶 / 标题编辑 | 中 | 低–中 | 计划 P2，需 API |
| 知识库 onboarding 向导（Stepper 式） | 中 | 中 | 部分由首页/Composer 空态覆盖 |
| 错题本「再练一次」跳转出题 | 中高 | 中 | 计划 P2，需学习 API 扩展 |
| 离线/PWA 提示 | 低（个人本地部署为主） | 中 | 暂缓 |
| 引用侧栏「跳转到知识库片段」深链 | 中高 | 中 | 计划 P2，与 Library 路由对齐 |
| 键盘快捷键（`/` 搜索已有） | 中 | 低 | Composer 已支持 Enter 发送；可文档化 |

---

## 三、本次交付摘要（`cursor/ux-improvements-2026-11-3604`）

| 项 | 文件 |
| --- | --- |
| 错题状态/时间中文化 | `frontend/src/learningUi.ts`, `Learning.tsx`, `learningUi.test.ts` |
| 学习空库引导、Tab a11y、出题 loading | `Learning.tsx`, `styles.css` |
| 会话引用侧栏偏好 | `LiveChat.tsx` |
| 知识库处理横幅 | `Library.tsx`, `Library.css` |
| 历史列表时间 + 空状态 | `App.tsx`, `styles.css` |
| 首页零库引导 | `Home.tsx`, `styles.css` |
| 搜索 Input | `App.tsx` |

**刻意未改**：`services/retrieval.py`、`services/chat.py` 检索路径、`rag_evaluation.py`、`core/config.py` retrieval_*、`README.md`；首页轨道、ThinkingOrb、metal-fx 发送钮保持全设备启用。

---

## 四、截图（预览环境，未登录）

| 说明 | 路径 |
| --- | --- |
| 首页桌面 | `/opt/cursor/artifacts/ux-2026-11/home-desktop.png` |
| 首页 390 | `/opt/cursor/artifacts/ux-2026-11/home-mobile.png` |
| 历史（访客） | `/opt/cursor/artifacts/ux-2026-11/history-guest.png` |
| 学习（访客） | `/opt/cursor/artifacts/ux-2026-11/learning-guest.png` |

登录后变更（错题中文、处理横幅、零库引导等）需在联调环境复验。

## 五、验收建议（明早人工）

1. 登录 → 无库时首页右侧见「创建首个知识库」；Composer 可创建库。
2. 上传资料 → 知识库见处理中横幅；Composer「当前库资料」轮询至可问答。
3. 移动端宽 390：打开会话默认收起引用，切换后刷新仍记忆。
4. 学习 → 错题本见中文状态；无库时见引导。
5. 历史 → 见会话时间；空列表见前往首页。