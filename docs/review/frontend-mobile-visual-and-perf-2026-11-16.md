# 前端手机端视觉与性能复审（2026-11-16）

基线分支：`cursor/learning-feature-9b72`（依赖 PR #2、#4）。  
工作分支：`cursor/frontend-mobile-visual-perf-1dbc`。

## 使用的 Skill 与适配说明

### taste-skill 2.5.1（产品 UI 适配，非营销站）

**已采用（手机 / 产品 UI）**

| 规则来源 | 应用方式 |
| --- | --- |
| `layout-v4.md` 等宽边距、320/390 无横向溢出 | `--page-gutter`、`overflow-x: clip`、统一 `resource-page` / `learning-page` / `home` 内边距 |
| `gate.md` tap-size / collide（390、320） | 粗指针下导航、工具栏图标 44px；学习 Tab `min-height: 44px`；登录弹窗关闭钮 44px |
| `motion.md` prefers-reduced-motion | 保留 `MotionConfig`；**仅**在 `prefers-reduced-motion` 时隐藏 `orbit-light`（不因屏宽关闭）；触控端仍保留首页轨道、ThinkingOrb、`metal-fx` 发送钮 |
| `perf.md` WebGL | 分包 + 懒加载减小首包，**不**在粗指针上关闭 WebGL；仅 `prefers-reduced-motion` 时停用 `metal-fx` 动效 |
| `system.md` 触控与 safe-area | `viewport-fit=cover`、通知区与侧栏 `env(safe-area-inset-*)`、聊天 `chat-footer` 已有 safe-area |

**刻意跳过（营销站 / 与本产品不符）**

| 规则 | 原因 |
| --- | --- |
| 一页一屏 hero、dealt moments、`data-enter` | SPA 工作区，非 landing |
| `copy-lint` 营销口吻 / 虚构地址 | 不改正文为营销话术 |
| `photos.mjs`、shader 背景、奖项式场景 | 产品无摄影 hero |
| `gate.mjs` 全量 PASS | 对 Vite SPA 大量 FAIL 为预期；见下方 gate 摘要 |
| `consistency.mjs` section-height 营销节奏 | 不适用多面板工作区 |

### vite-react-best-practices

- 路由级懒加载：`Home`、`Account`（原已懒加载 Chat / Library / Learning）。
- Vite 8 `rolldown` 手动分包：`vendor-motion`、`vendor-three`、`vendor-metal`、`vendor-highlight`、`vendor-markdown`。
- 避免无测量 memo；仅按分包与动态 import 调整。

### frontend-performance-skills

- 先 `npm run build` 再改：见测量表。
- 首屏只加载入口 + 路由所需 chunk。

### dejank（静态扫面）

- `App` 中 `loginOpen` 时才挂载懒加载 `Account`，登录前预 `import()` 降低首开闪烁。
- `MarkdownContent` 异步挂载 highlight，避免首屏拉满 highlight 包。

## 测量基线与结果

### 构建分块（`npm run build`，minify 后）

| 资产 | 改前 | 改后 |
| --- | ---: | ---: |
| 入口 `index-*.js` | 578.9 KB（gzip 184.3 KB） | 313.5 KB（gzip 99.9 KB） |
| `LiveChat-*.js` | 34.0 KB | 9.1 KB |
| `MarkdownContent`（含 highlight） | 331.6 KB（未单独拆出） | 0.82 KB + `vendor-highlight` 170.2 KB + `vendor-markdown` 161.4 KB |
| `metal-fx` / `three` 进首页路径 | 打进主包 | `vendor-metal` 95.4 KB、`vendor-three` 25.6 KB（按需） |

说明：改前数据来自同分支未改动的首次 `vite build`；改后为本次提交构建。

### taste `perf.mjs`（`vite preview` @ `127.0.0.1:4173/`，无后端）

| 视口 | JS transfer（gzip 约） | FCP | LCP | CLS | WebGL @ load |
| --- | --- | --- | --- | --- | --- |
| 1440 | 251.1 KB（parsed WARN 743.8 KB） | 116 ms | 432 ms | 0.003 | 0 |
| 390 | 同上（字体子集更少） | （同脚本） | — | — | 0 |

仍 FAIL / WARN：`font bytes`（MiSans 多切片 + Geist，见 `frontend/src/fonts`）、`auth/refresh` 断连（预览环境无 API）。字体优化未在本迭代改动，仅记录。

### 浏览器截图（390 视觉回归，2026-11-16 跟进）

恢复触控端 WebGL / 轨道动画后重拍：

| 说明 | 路径 |
| --- | --- |
| 首页：轨道 `.orbit-light` + 发送区 `metal-fx`（`shoot.mjs --wait 2500`） | `/opt/cursor/artifacts/mobile-390/home-visual-restored.png` |
| ThinkingOrb（路由 `/__visual/orb`，无导航入口，同 `AssistantActivity` 组件链） | `/opt/cursor/artifacts/mobile-390/thinking-orb-visual-restored.png` |

历史截图（粗指针降级 WebGL 时期，仅作记录）：`mobile-390/home.png`、`mobile-390-chat-demo.png` 等。

### 浏览器截图（首轮改后）

| 路径 | 390×844 | 768×1024 |
| --- | --- | --- |
| 首页 | `/opt/cursor/artifacts/mobile-390/home.png` | `/opt/cursor/artifacts/mobile-768/home.png` |
| Demo 会话 | `/opt/cursor/artifacts/mobile-390-chat-demo.png` | — |
| 学习 | `/opt/cursor/artifacts/mobile-390-learning.png` | — |
| 知识库 | `/opt/cursor/artifacts/mobile-390-knowledge.png` | — |

## 送审清单

- [x] `npm test`（frontend）
- [x] `npm run build`
- [x] 390 / 768 真浏览器截图（taste `shoot.mjs` + Chrome）
- [x] `perf.mjs` 首页（适用项）
- [ ] 全量 `gate.mjs` PASS（不适用营销 SPA，已跳过）
- [ ] 后端联调全流程（需 API；预览仅 UI）
- [ ] Lighthouse CI（环境未配置，未跑）

## 变更文件索引（摘要）

- 手机视觉：`frontend/src/styles.css`、`frontend/index.html`、`frontend/src/components/Account.module.css`、`frontend/src/components/AssistantActivity.css`
- 性能：`frontend/vite.config.ts`、`frontend/src/App.tsx`、`frontend/src/components/{Composer,ComposerMetalSend,MarkdownContent,AssistantActivity,ThinkingOrbLazy}.tsx`、测试更新

## 跟进：恢复手机端视觉（同 PR #5）

| 项 | 处理 |
| --- | --- |
| 760 以下隐藏 `orbit-light` | 已撤销；仅 `prefers-reduced-motion` 隐藏 |
| 粗指针用图标代替 ThinkingOrb | 已撤销；`ThinkingOrbLazy` 全设备 |
| 粗指针跳过 `metal-fx` | 已撤销；仅 `prefers-reduced-motion` 走平面发送钮；`Composer` 挂载时预 `import('./ComposerMetalSend')` |
| `useCoarsePointer` | 已删除（无其它引用） |
| ThinkingOrb 390 截图 | 无公开 API 流式会话时用 `/__visual/orb` 烟测路由（`VisualOrbSmoke.tsx`） |