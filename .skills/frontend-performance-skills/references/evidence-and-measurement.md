# 先把“慢”说清楚

“页面有点慢”可以作为起点，但还不能直接开改。第一次沟通不需要把人教成性能专家，拿到一份能分辨方向的基线就够了。

## 到底是哪一次体验慢

同一个页面可以有几种完全不同的性能问题。至少确认下面几项：

- 用户从哪里开始，做了什么，在哪一步感到慢；
- 首次打开还是站内跳转，匿名用户还是登录用户；
- 生产、测试还是本地环境；
- 手机还是电脑，浏览器与大致网络条件；
- 问题每次都出现，还是偶尔出现在某类用户身上。

用户说不清时，请他挑最重要的一条路径，按平时的操作录一次。第一次取数别搞成工具考试。

## 手里没有数据时，从这里开始

### 本地最快怎么采

这套方法适合本地能复现的问题，也适合没有监控系统的小项目。

1. 使用生产构建，而不是开发模式。开发模式里的额外校验、未压缩代码和热更新会扭曲结果。
2. 打开 Chrome DevTools 的 **Performance** 面板，刷新页面并完成那次关键操作。
3. 留意 Live metrics 中的 LCP、CLS，以及操作后出现的 INP。能稳定复现后再录制一段 trace。
4. 打开 **Network** 面板，保留请求记录，分别观察一次关闭缓存的首次加载和一次正常缓存的重复加载。
5. 导出 Performance trace；如果请求链或缓存可疑，再导出经过脱敏处理的 HAR。HAR 可能含有 URL 参数、Cookie 或鉴权头，分享前提醒用户检查敏感信息。

请用户同时给出页面路径、复现步骤、设备和网络、冷/热缓存、Chrome 版本与录制区间。只有总分或火焰图截图时，能下的判断很有限，尽量拿原始 trace。Chrome 导出物怎么读，见 [chrome-devtools-artifacts.md](chrome-devtools-artifacts.md)。

### 用 Lighthouse 建立可重复的实验室基线

Lighthouse 适合找线索和做前后对比。它模拟的是某一组设备与网络条件，不代表线上所有用户。

- 在隐身窗口或干扰较少的浏览器环境中运行，避免扩展和已有缓存影响结果。
- 使用同一页面、同一设备预设和同一构建连续跑几次。首次结果可能受机器负载影响，不要只挑最好的一次。
- 保存 HTML 或 JSON 报告。报告里的诊断项比一个整数分数更有分析价值。
- 登录后页面可以在 DevTools 中运行；自动化或 CI 场景再考虑 Lighthouse CLI / Lighthouse CI。

如果用户只能提供一样东西，优先要完整报告，而不是性能分数截图。

### 用 PageSpeed Insights 或 CrUX 看线上真实体验

公开页面可以先查 PageSpeed Insights。页面上方的 field data 来自 CrUX，反映过去一段时间内符合条件的真实 Chrome 用户；下方的 Lighthouse lab data 是一次模拟运行。两组数据可能不同，这通常不是工具出错。

记录以下信息：

- 数据是 URL 级还是 origin 级；
- 移动端还是桌面端；
- LCP、INP、CLS 的第 75 百分位及好、待改善、差的分布；
- 页面是否因为流量不足而没有 field data；
- 异常集中在哪类设备、网络或页面模板。

CrUX 很适合确认“线上是不是普遍有问题”，但常常不能回答“为什么”。它是约 28 天的聚合数据，也不适合判断昨天发布的改动是否已经生效。

### 有 RUM 时，别急着看总数

RUM 的价值不在总览图，而在于能把慢体验切开。优先按下面维度看：

- 页面或路由模板；
- 设备类型、浏览器、网络类型；
- 新访客与回访用户；
- 发布版本；
- 登录状态、地域或业务用户群；
- LCP 元素、慢交互目标、布局偏移来源等归因信息。

关注分布和第 75 百分位。平均数会把尾部用户的糟糕体验藏起来。

如果项目还没有 RUM，可以建议使用 [`web-vitals`](https://github.com/GoogleChrome/web-vitals) 采集核心指标，但这属于项目改动，必须得到用户授权。一个最小入口大致如下，真正上线前还要确定采样率、上报地址、隐私规则和版本维度：

```js
import { onCLS, onINP, onLCP } from 'web-vitals';

function report(metric) {
  // Send the metric to the project's existing analytics endpoint.
}

onCLS(report);
onINP(report);
onLCP(report);
```

不要只是把指标打印到控制台。没有稳定的上报、聚合和版本标记，就无法用它观察生产回归。

## 不同证据能回答什么

| 证据 | 擅长回答 | 回答不了什么 |
|---|---|---|
| CrUX / PageSpeed field data | 真实用户总体是否达标、趋势如何 | 某个组件或请求为什么慢 |
| 自建 RUM | 哪类用户、页面或交互最慢 | 没有归因字段时仍难定位源码 |
| Lighthouse | 受控条件下的加载线索和前后回归 | 所有真实用户的体验 |
| Performance trace | 主线程、渲染、交互和网络时序，以及与 Web Vitals 相关的事件 | 长期用户分布；跨录制条件的绝对比较 |
| HAR / Network 面板 | 请求链、缓存、协议和资源传输 | JavaScript 内部执行成本；没有被记录到的请求 |
| Bundle report / stats | 哪些模块进入了哪些 chunk | 用户是否真的下载或执行了它们 |
| Build log / profile | 构建阶段与插件耗时 | 页面运行时体验 |
| 源码检查 | 可能的因果机制 | 问题在生产中实际发生的频率 |

## 向用户要材料时

没有环境访问权限时，把请求压成一份好执行的清单：

```text
请提供：
1. 最重要的页面路径和复现步骤
2. 环境、设备、网络条件，以及冷/热缓存
3. 一份 Performance trace
4. 一份 Lighthouse HTML/JSON 报告
5. 如果是加载问题，再附 HAR 或 Network 截图
6. 如果是包体问题，再附生产构建的 bundle stats/report
```

不要机械地索要全部材料。交互卡顿通常先要 trace；构建慢先要带阶段耗时的构建日志；线上 Core Web Vitals 回归先看按版本和页面切分的 field data。

## 官方资料

- [Getting started with measuring Web Vitals](https://web.dev/articles/vitals-measurement-getting-started)
- [Chrome DevTools Performance panel](https://developer.chrome.com/docs/devtools/performance/overview)
- [Core Web Vitals workflows with Google tools](https://web.dev/articles/vitals-tools)
- [Why lab and field data can be different](https://web.dev/articles/lab-and-field-data-differences)
