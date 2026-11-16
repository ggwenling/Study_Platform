# 前端性能 Skill

为 AI 编程 Agent 提供基于证据的前端性能排查能力。

[English](README.md) | [简体中文](README_ZH.md)

`frontend-performance` 是一份可移植的 Agent Skill，可用于 Codex、Claude Code、Cursor、pi、OpenClaw 和 Hermes Agent。它用来分析页面加载慢、Core Web Vitals 不理想、运行时卡顿、包体过大、构建缓慢、静态资源和网络交付问题。

它从具体用户路径和已有证据出发。Lighthouse 分数不等于生产环境真相，大依赖不一定是瓶颈，trace 里的长事件也不能单独算作根因。

如果你希望编程 Agent 少给通用清单，多根据 trace、请求、构建产物和线上数据做判断，推荐安装这个 skill。它会帮你找到最值得先修的问题，并说明改完怎么验证。

## 能处理什么

- 使用 field data、RUM、CrUX、Lighthouse 和 Chrome DevTools 建立基线。
- 分析 Chrome Performance trace、火焰图、脱敏 HAR 和 Network 请求瀑布。
- 排查 LCP、INP、CLS、长任务、渲染工作、无效更新和内存增长。
- 分析 bundle、引用边界、tree-shaking、重复依赖、重复实现和构建时间。
- 检查生产产物、source map、内容哈希、压缩、缓存和部署配置。
- 提供 Vite、webpack、Next.js 和 Nuxt 的构建交付说明。
- 按图片、字体、SVG、CSS/JavaScript、音视频和 PDF 的特点选择压缩方案。
- 按 origin 分析 DNS、TCP/TLS、TTFB、跳转、HTTPS、跨域、CDN 和第三方请求。
- 使用 Front-End Performance Checklist 的检查面做审查。
- 设计前后对比、性能预算和防回归检查。

## 工作方式

1. 说清用户路径、环境、缓存状态和成功标准。
2. 收集最少但够用的一组证据。
3. 判断限制发生在服务端、网络、JavaScript、渲染、交互、内存还是构建阶段。
4. 分开记录观察和假设，再按影响、置信度、成本和风险排序。
5. 在相同条件下验证一个聚焦改动；有条件时，再用 field data 确认真实用户收益。

[SKILL.md](SKILL.md) 保存主流程，只在需要时将当前 Agent 路由到 [`references/`](references/) 中的专项手册。

## 兼容性

这个项目遵循 [Agent Skills 规范](https://agentskills.io/specification)：以 `SKILL.md` 为入口，按需加载 `references/`、`scripts/` 和宿主元数据。核心指令不依赖 Codex 专属工具或调用语法。

| 宿主 | 推荐的用户级目录 |
|---|---|
| [Codex](https://developers.openai.com/codex/skills) | `~/.agents/skills/frontend-performance` |
| [Claude Code](https://code.claude.com/docs/en/skills) | `~/.claude/skills/frontend-performance` |
| [Cursor](https://cursor.com/docs/skills) | `~/.agents/skills/frontend-performance` 或 `~/.cursor/skills/frontend-performance` |
| [pi](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/skills.md) | `~/.agents/skills/frontend-performance` 或 `~/.pi/agent/skills/frontend-performance` |
| [OpenClaw](https://docs.openclaw.ai/skills) | `~/.agents/skills/frontend-performance` 或 `~/.openclaw/skills/frontend-performance` |
| [Hermes Agent](https://hermes-agent.nousresearch.com/docs/guides/work-with-skills) | `~/.agents/skills/frontend-performance` 或 `~/.hermes/skills/frontend-performance` |

`agents/openai.yaml` 只用于补充 Codex 的界面信息，其他宿主可以忽略。Skill 的加载格式是可移植的；要执行配套脚本，宿主仍需开放本地文件和终端能力。
