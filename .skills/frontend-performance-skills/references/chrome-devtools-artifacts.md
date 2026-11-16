# Chrome DevTools 导出物：从图和请求到可验证结论

用户带来 Chrome Performance 导出、火焰图截图、Network 日志或 HAR 时，用这份手册把浏览器证据整理成能追溯的判断。它覆盖加载、交互、滚动和动画；一份 trace 不能代替线上分布数据。

## 先看手里的材料够不够

优先级从高到低：

1. **原始 Performance trace**：可重新打开、缩放并选中事件，才能查看调用栈、self time、来源和时序。
2. **脱敏 HAR**：可筛选、重排、查看 Initiator、Timing、响应头和缓存相关信息。默认使用 Chrome 的 *Export HAR (sanitized)*；不要要求用户发送带 Cookie、`Set-Cookie` 或 `Authorization` 的版本。
3. **Network 导出/截图**：可以辅助确认瀑布和单个请求，但截图通常无法证明发起链或完整 Timing。
4. **火焰图截图**：只可用来提出线索。若没有原始 trace，明确说明调用栈、事件详情、选区总耗时和第三方归因均无法复核。

可以先跑仓库里的只读摘要器缩小范围，再回 DevTools 确认因果：

```bash
node "$FRONTEND_PERF_SKILL_DIR/scripts/parse-performance-artifact.mjs" path/to/recording.json
node "$FRONTEND_PERF_SKILL_DIR/scripts/parse-performance-artifact.mjs" path/to/network.har --json
```

它会提取 HAR 的 origin、重定向、慢请求及其已导出的 Timing 阶段（DNS、connect、SSL、wait/TTFB 线索等），以及 trace 中持续时间至少 50 ms 的事件候选、可见 URL 和重定向事件。HAR 中 `-1` 或缺失的阶段表示该次请求没有提供该值，不能补零或据此判断没有连接成本。不同 Chrome 版本和录制选项会改变事件字段；“长时事件候选”不是浏览器 Long Task API 的等价物，也不能代替调用栈、LCP/INP 归因或缓存/连接状态。

记录或索要：页面与复现步骤、录制开始/结束位置、生产或开发构建、Chrome 版本、设备、CPU/网络节流、冷/热缓存、service worker 是否启用，以及是否有 source map。不要拿两次条件不同的录制直接比较绝对时间。

### 录制时注意什么

- 录制只覆盖一个完整且重要的用户旅程：导航到稳定、一次点击到界面完成更新，或一段连续滚动。过长 trace 会淹没因果关系。
- 先用普通录制。高级 paint instrumentation、CSS selector stats 等采集本身会增加开销；只在需要回答对应问题时开启，并在报告中注明。
- Performance trace 和 HAR 记录的是一次浏览器会话。用同一套步骤分别采集冷缓存首访与正常缓存复访，不要把两者混在同一结论里。
- 文件可能含 URL 参数、响应正文或业务标识。接收、存储或引用前先检查敏感信息；分析时优先摘录域名、资源类型、大小、耗时和必要的路径片段。

## 看 Performance trace / 火焰图

### 先框出调查窗口

先在概览轨道和截图轨道定位“用户感到慢”的时刻，再缩小到它前后的最小区间。将这个区间与一个具体信号绑定：LCP 标记、一次 Event/点击、明显掉帧、路由导航或加载完成。不要先从最宽的黄色块开始猜。

然后依次看：

1. **Metrics / Insights**：若 trace 有 LCP、INP、CLS 或 insight，先用它定位影响的时间段和元素。LCP 要拆成 TTFB、资源加载延迟、资源加载时间、元素渲染延迟；不要把最终 LCP 全归给图片下载。
2. **Main flame chart**：横轴是时间，嵌套的条表示调用关系。宽度表示该事件持续时间；选择事件后以 Summary 中的 duration、self time、来源 URL 和调用栈为准。红色三角和超过 50 ms 的阴影表示长任务的候选，不自动等于根因。
3. **Bottom-up 与 Call tree**：对选中的调查窗口，用 Bottom-up 找累计 self time 高且可归因的函数/脚本，再用 Call tree 和调用栈确认谁反复触发它。一个函数总耗时高可能是一次特别慢，也可能是高频小调用；两种修复路径不同。
4. **Rendering / GPU / Frames 轨道**：把 JavaScript、Recalculate Style、Layout、Paint、Composite 与掉帧或视觉更新对齐。若读取几何信息发生在写样式之后，且 trace 标示 forced reflow，再追到具体调用点；不要只因看到 Layout 就断言“布局抖动”。
5. **来源与归因**：查看事件的 URL、entity 和 source map 位置。用 first-/third-party 视图或按来源筛选，分开报告自有代码、第三方和无法归因的执行时间；没有 source map 时，缩小到 chunk/域名，不要编造源码函数名。

### 常见模式与成立条件

| 现象 | 可以提出的判断 | 还需排除 / 验证 |
|---|---|---|
| 一次输入或点击后出现长 Event，内部是大量 JS | 交互处理或同步计算可能延迟呈现 | 对齐 INP/截图，并确认不是录制期间的其他后台工作 |
| JS 之后连续的 Style / Layout / Paint 占满关键区间 | 这次更新的呈现成本可能是主要限制 | 检查更新范围、DOM 变化和是否可用更小的状态边界复现 |
| 一帧内交替出现样式写入与几何读取，并有 forced reflow | 存在强制同步布局的直接线索 | 追调用栈，确认读取确实发生在写入之后且命中用户路径 |
| 第三方脚本同时占网络和 Main | 该第三方可能挤占关键路径或交互预算 | 确认业务必要性、加载时机和 consent 约束；不能只按域名删除 |
| LCP 资源完成后仍有很长的元素渲染延迟 | 渲染阻塞或主线程工作可能拖后了 LCP | 用 LCP phases、Main 和 render-blocking 请求交叉验证 |

火焰图颜色只是阅读辅助，不能单独当证据。“某函数看起来很宽”要变成 finding，还得补上选区、事件详情和用户影响。

## 看 Chrome Network / HAR

先按资源类型和关键旅程过滤，再回到按开始时间排序的 Waterfall。对每个可疑请求至少检查：状态/失败原因、发起时间、Initiator、Priority（可见时）、传输大小与解码大小、缓存/服务工作线程信息、响应缓存头，以及 Timing。

### 用瀑布回答四件事

1. **为什么现在才开始？** 看前置文档、CSS、脚本或接口，借助 Initiator 区分 HTML parser、redirect、script 和其他触发者。重点是发起链，而不是只看总耗时。
2. **开始后为什么慢？** 在 Timing 中区分 queueing/stalled、DNS/connect/SSL、request sent、waiting/TTFB 与 content download。`waiting` 很长是需要继续拆分的服务器/网络线索，不等于“后端代码慢”。
3. **它阻塞了什么？** 将请求结束时间与 LCP、首屏 CSS、字体、关键脚本或后续请求的开始时间对齐。并行出现不代表互不影响：它们仍可能竞争连接、带宽或主线程。
4. **重复访问为何仍然下载？** 比较冷/热两份日志的 Size、Status、缓存来源和 `Cache-Control`/ETag/Vary；同时确认 CDN 与 service worker 行为。一次 `(disk cache)` 不能证明全体用户能命中。

### 请求类型的阅读重点

- **Document**：重定向链、TTFB、HTML 是否足以早发现关键资源。
- **CSS / Font**：是否位于首次渲染前，是否通过错误的 preload/CORS/候选造成重复请求。
- **Image**：显示尺寸和候选是否匹配；LCP 图片何时发现、何时开始、何时完成；首屏 LCP 图片不应因懒加载而晚开始。
- **Script**：是否由 parser 早发现、是否形成脚本执行后才发现资源的串行链；同时回到 trace 看下载之外的解析/执行成本。
- **Fetch / XHR**：是否被关键 UI 不必要地串行等待；检查接口依赖与取消/缓存语义，不能把所有接口都并行化。
- **第三方**：把域名的网络时间和 trace 中的主线程时间一起统计，再评估延后、按同意状态加载或移除的业务风险。

HAR 的字段和浏览器实现会随版本、导出选项而变化。把 HAR 当作请求时序和元数据的证据；若它缺少 Priority、缓存解释或完整传输细节，回到原始 Network 面板/trace，而不是从缺失字段推断“没有问题”。

## 把 trace 和请求链接起来

性能结论通常要跨两种证据：

```text
用户动作 / Web Vital
  -> trace 中的关键区间与 Main / Rendering 工作
  -> 对齐的 Network 请求及其 Initiator、开始和完成时间
  -> 具体资源、脚本、组件或服务边界
  -> 最小改动与同条件复测
```

例如，LCP 图片晚开始时，先证明 LCP 元素和请求是同一个资源，再看它是 parser、script 还是接口响应后才发现；随后检查请求传输和资源完成后的渲染延迟。只有“图片请求很慢”而没有这条链时，建议应保留为假设。

对交互问题，先锁定 input/click 对应的 trace 区间，再区分输入延迟、事件处理、渲染呈现。不要用 HAR 的接口时长解释一个发生在接口完成前或与接口无关的长任务。

## 一条 finding 至少要有这些证据

每条结论包含：

```text
发现：在 <旅程和条件> 下，<资源/代码/渲染阶段> 限制了 <用户可见结果>。
证据：trace 的 <事件、时间范围、self time/调用栈>；HAR/Network 的 <Initiator、开始/结束、Timing/缓存>。
影响：受影响的是首次加载、重复访问还是某个交互；影响的指标或视觉结果。
置信度：高 / 中 / 低，以及尚未覆盖的条件。
建议：能验证该机制的最小改动。
风险：带宽竞争、缓存正确性、业务依赖或交互语义。
验证：同一版本、设备、节流与缓存条件下重录；比较哪几个事件、请求或指标。
```

少了原始文件、调用栈、Initiator 或复现条件，就降级为“待验证假设”，并写明缺什么。DevTools insight 很适合指路，但不能原样当结论，还得回到当前用户旅程核实。

## 官方参考

- [Chrome DevTools Performance panel](https://developer.chrome.com/docs/devtools/performance/overview)
- [Performance features reference](https://developer.chrome.com/docs/devtools/performance/reference)
- [Performance Insights and LCP phases](https://developer.chrome.com/docs/devtools/performance-insights)
- [Network features reference and sanitized HAR export](https://developer.chrome.com/docs/devtools/network/reference)
