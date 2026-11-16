---
name: frontend-performance
description: Diagnose and improve frontend performance with evidence from real-user metrics, Chrome DevTools traces and HAR exports, network activity, bundles, builds, and source code. Use for slow pages, poor Core Web Vitals, large bundles, slow builds, runtime jank, rendering waste, or requests to learn how frontend performance works. If measurements are missing, teach the user how to collect a useful baseline before recommending fixes.
---

# Frontend Performance

性能问题得按排障来做。听起来合理的解释还不是结论，包变小也不一定让用户更快。先弄清用户到底觉得哪里慢、手头能拿到什么数据，再让每条建议都能回到证据上。

## 运行环境

这是一份遵循 Agent Skills 目录结构的可移植 skill。不要假设宿主一定是 Codex，也不要依赖 `$skill-name`、`/skill-name` 或某个产品专有的工具名；使用当前宿主提供的等价文件读取、终端和浏览器能力。`agents/openai.yaml` 只是 Codex 的可选界面元数据，其他宿主忽略即可。

`references/...` 和 `scripts/...` 都相对于本文件所在目录。执行配套脚本前，先从已加载的 `SKILL.md` 路径解析出 skill 的绝对根目录；工作目录通常是被检查的用户项目，并不是 skill 目录。下文中的 `FRONTEND_PERF_SKILL_DIR` 代表这个实际绝对路径，不是宿主预置的环境变量。

## Choose the working mode

大多数请求都能落在下面某一种场景里。只有证据确实跨了边界，才同时打开多份手册。

- **Baseline:** 用户觉得慢，但没有可信数据。读 [evidence-and-measurement.md](references/evidence-and-measurement.md)，帮他拿到最小可用的一组基线。
- **Diagnosis:** 已有 URL、trace、Lighthouse、Web Vitals、bundle report、构建日志或稳定复现步骤。先读 [diagnosis-and-reporting.md](references/diagnosis-and-reporting.md)，再按证据选对应手册。Chrome Performance 导出、火焰图或 HAR/Network 日志还要读 [chrome-devtools-artifacts.md](references/chrome-devtools-artifacts.md)。
- **Build and dependencies:** 关注包体、引用、chunk、tree-shaking、重复模块、source map、polyfill 或构建时间。读 [bundles-and-build.md](references/bundles-and-build.md)。涉及重复依赖/实现、生产产物、缓存交付或 map 策略时，再读 [dependency-and-delivery-controls.md](references/dependency-and-delivery-controls.md)。已确认是 Vite、webpack、Next.js 或 Nuxt 时，改配置前读 [framework-build-delivery.md](references/framework-build-delivery.md)。
- **Runtime and rendering:** 页面卡住、交互迟钝、滚动掉帧、内存持续上涨，或组件做了多余工作。读 [runtime-and-rendering.md](references/runtime-and-rendering.md)。
- **Loading and delivery:** 请求、缓存、图片、字体、第三方、渲染阻塞或服务端响应可疑时，读 [network-assets-and-caching.md](references/network-assets-and-caching.md)。Chrome 请求证据还要配合 [chrome-devtools-artifacts.md](references/chrome-devtools-artifacts.md)；DNS、TLS、跳转、HTTP/HTTPS、跨域、CDN 和第三方域名则读 [network-transport-and-domains.md](references/network-transport-and-domains.md)。查静态资源清单、源码来源、图片/字体/媒体取舍，或已获准转换资产时，读 [static-asset-performance.md](references/static-asset-performance.md)。要给出某种格式的压缩工具或在线服务，再读 [static-asset-compression.md](references/static-asset-compression.md)。
- **Learning:** 用户想弄懂概念、指标或学习路径。读 [performance-knowledge-map.md](references/performance-knowledge-map.md)，只讲与问题有关的分支。
- **Checklist review:** 用户要做性能 readiness review、规范/Checklist 审查，或对照 Front-End Performance Checklist。读 [checklist-review-mode.md](references/checklist-review-mode.md)，再按发现加载相关手册。每一项标成已验证、发现、不适用或未调查；没勾上的项不能直接变成代码改动。
- **Verification:** 已有改动，或用户需要防回归。读 [validation-and-budgets.md](references/validation-and-budgets.md)。

## Start with an evidence gate

提出改动前，把下面四件事说清楚：

1. Which user journey matters: first visit, repeat visit, route transition, search, checkout, editor interaction, or something else.
2. Which environment produced the symptom: production, staging, or local development; mobile or desktop; warm or cold cache.
3. What evidence is available and whether it is field data, lab data, a profile, a build artifact, source inspection, or only an observation.
4. What success would look like: a Web Vital threshold, faster interaction, smaller route payload, shorter build, lower memory growth, or a business outcome.

证据不够就先取数，不要用听上去很确定的话补空白。按 [evidence-and-measurement.md](references/evidence-and-measurement.md) 给用户一段短的采集步骤，优先要他真能提供的材料：DevTools trace、HAR、Lighthouse JSON/HTML、PageSpeed 结果、bundle stats、构建日志，或一个能复现的页面和操作。

When direct inspection is possible, use read-only commands and existing project tooling first. The optional script below gives a quick map of an unfamiliar repository without installing packages:

```bash
FRONTEND_PERF_SKILL_DIR=/absolute/path/to/frontend-performance
node "$FRONTEND_PERF_SKILL_DIR/scripts/collect-project-context.mjs" /path/to/project
```

Do not add analyzers, change build configuration, run production load tests, or introduce monitoring code unless the user asked for changes or approved that expansion.

## 排查时的顺序

工具会随项目变化，思路不要乱：

1. Reproduce one important journey under named conditions.
2. Find the limiting stage: server response, resource discovery and transfer, JavaScript execution, rendering, or interaction handling.
3. Narrow the stage to a resource, task, component, dependency, request chain, or architectural boundary.
4. Separate observations from conclusions. Mark uncertain explanations as hypotheses and say what would confirm them.
5. Rank changes by likely user impact, confidence, engineering cost, and regression risk.
6. Prefer the smallest change that tests the diagnosis.
7. Compare before and after under the same conditions, then say what still needs field validation.

## 几个常见误判

- Do not treat a Lighthouse score as production truth. It is a diagnostic result from one controlled run.
- Do not use bundle size as a substitute for execution cost, caching behavior, or route-level user impact.
- Do not recommend memoization, lazy loading, virtual lists, preload, or code splitting as universal fixes. Each can move cost elsewhere.
- Do not call every task over 50 ms the root cause. Connect it to the affected interaction or load phase.
- Do not compare a development build with an optimized production build.
- Do not claim a Core Web Vitals pass from averages. Field assessment is based on the 75th percentile and enough visits.
- Do not optimize a synthetic demo path while ignoring the user's important journey.

## 报告要能帮人做决定

审查或诊断结果至少交代：

- the journey and conditions examined;
- the available evidence and its limitations;
- the primary bottleneck, with the evidence that supports it;
- findings ordered by priority, each with expected impact, confidence, effort, risk, and a verification step;
- a short measurement plan for anything that remains uncertain;
- the smallest sensible next action.

Use exact file paths, modules, requests, tasks, or elements when the evidence supports them. If the user is learning, explain unfamiliar terms at first use and connect each concept to where they can see it for themselves.

把它写成同事之间能直接接手的排障记录：句子具体，不确定就说不确定。别堆审计套话、重复总结，或放一串任何项目都适用的建议。
