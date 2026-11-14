# Thinking Orbs 集成验收

日期：2026-11-14。依据 [Libraries.dev React 文档](https://libraries.dev/orbs)，锁定 `thinking-orbs@0.3.2`，适配现有 React 19 / Vite / npm 前端。

## 效果与范围

- 真实 AI 消息左侧固定 64×64 头像区域。处理期间显示单色 Orb，结束后恢复静态书本图标；移动端正文保留整行宽度，用户消息仍靠右。
- 后端在真实执行位置发送 `phase`：准备和收尾为 `working`，检索为 `searching`，模型输出为 `composing`。没有定时模拟阶段。
- 模型仍关闭深度思考，本次不发送 `solving`；前端保留其类型与展示映射。
- 完成、取消、错误及断线立即移除 Orb；恢复查询显示文字。重放结果通过 `meta.replay` 避免播放动画。
- 组件使用 `theme="auto"`，沿用库内减少动态效果、离屏和隐藏标签页暂停机制。应用保持原有暗色主题，不增加全站主题切换。

## 修改文件

| 文件 | 内容 |
| --- | --- |
| `frontend/package.json`、`frontend/package-lock.json` | 锁定 React 版动画依赖 |
| `frontend/src/components/AssistantActivity.tsx` | 可复用、memo 化的 Orb 与静态头像组件、阶段文案 |
| `frontend/src/components/AssistantActivity.css` | 固定占位及桌面、移动端布局 |
| `frontend/src/components/LiveChat.tsx` | 仅活动 AI 消息展示动画和阶段文字 |
| `frontend/src/useChat.ts` | 当前轮次阶段、终态清理、重放及旧服务端兼容 |
| `frontend/src/api/types.ts` | `ChatPhase`、`phase` 事件和可选 `meta.replay` |
| `frontend/src/chat-recovery.test.tsx` | 新增六项阶段及动画生命周期回归，保留既有恢复用例 |
| `backend/src/study_platform/services/chat.py` | 真实执行边界的阶段事件及重放标记 |
| `backend/tests/integration/test_chat.py` | 检索、摘要、资料不足和重放阶段回归 |
| `docs/api-integration.md` | 新增事件和兼容行为说明 |
| `docs/verification-thinking-orbs.md` | 本记录 |

## 验证结果

- `frontend`: `npm test`，**34 passed**；`npm run build`（含 `tsc -b`）通过，只有既有大分块提示。项目没有前端 lint 脚本，本次没有引入额外 lint 工具。
- `backend`: `uv run ruff check .` 通过；`RUN_DB_TESTS=1 uv run pytest -q tests/integration/test_chat.py`，**11 passed**，使用独立临时 PostgreSQL 测试库和模拟模型，不调用付费服务。
- Chromium 实际渲染：1440、390、320px；受控 HTTP SSE 驱动阶段切换，头像始终 64px，无横向溢出，无浏览器运行错误。
- 对真实 Canvas 绘制计数：正常状态持续绘制，减少动态效果下为静态帧，离屏后绘制停止，完成后 Canvas 移除。检查明暗背景及移动端截图。
- React 组件回归覆盖阶段匹配、完成后恢复查询期间无动画、错误、取消响应返回前清理、旧请求事件、旧服务端正文流回退、重放和账号替换卸载。

## 验证边界

未调用真实付费模型验证阶段耗时或回答质量；浏览器使用测试接口和受控 SSE。未实测 Safari、Firefox、实体移动设备及真实后台标签页的资源采样；后台暂停沿用所锁定库的可见性监听实现。未增加数据库迁移或更改模型配置。
