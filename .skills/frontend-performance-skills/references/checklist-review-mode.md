# Front-End Performance Checklist 审查模式

这个模式把 [thedaviddias/Front-End-Performance-Checklist](https://github.com/thedaviddias/Front-End-Performance-Checklist) 的 HTML、CSS、字体、图片、JavaScript 和交付检查面，接到本 skill 的证据驱动调查上。上游项目覆盖很广；这里不照搬全文，也不把任何“最佳实践”当成无条件改动。

## 什么时候用

在用户明确要“按 checklist 审查”“做性能 readiness review”“上线前检查”时使用。先确认页面/路由、生产环境、目标设备与浏览器、冷/热缓存、部署方式和可提供的 artifacts。然后按表格给每项状态：

- **已验证**：有 trace/HAR、产物、响应头、源码或项目配置直接支持。
- **发现**：证据显示它正在影响重要路径；写明影响、风险和验证。
- **不适用**：说明架构或业务原因，例如没有自托管字体、不是 PWA、或资源不是关键路径。
- **未调查**：当前材料不足；列出最小取证动作，不用“未勾选”冒充 finding。

优先级来自用户影响、证据和修复风险。可将直接阻塞关键路径的 findings 标为 P0；有强线索但需要权衡的标为 P1；卫生项或未来风险标为 P2。不要沿用固定页面重量、总加载时间或单次 TTFB 阈值作为所有项目的通过线。

## 检查面怎么对应

| 上游检查面 | 本 skill 中的审查问题 | 读取的详细参考 |
|---|---|---|
| HTML | 首屏文档是否有不必要体积、错误的资源发现/顺序、无效 iframe、内联阻塞脚本或不合理的资源提示？ | [network-assets-and-caching.md](network-assets-and-caching.md)、[chrome-devtools-artifacts.md](chrome-devtools-artifacts.md) |
| CSS | 生产 CSS 是否压缩、是否有明显未使用部分、是否阻塞首屏，以及拆分/关键 CSS 是否真的改善渲染？ | [network-assets-and-caching.md](network-assets-and-caching.md)、[bundles-and-build.md](bundles-and-build.md) |
| Fonts | 是否使用合适格式、子集/字重、缓存、跨域与 `font-display`，且没有造成布局或可读性回归？ | [static-asset-performance.md](static-asset-performance.md)、[static-asset-compression.md](static-asset-compression.md) |
| Images | 实际请求的尺寸、格式、压缩、候选、LCP 优先级和非关键懒加载是否合理？ | [static-asset-performance.md](static-asset-performance.md)、[static-asset-compression.md](static-asset-compression.md) |
| JavaScript | 生产压缩、依赖/重复模块、导入边界、解析执行、脚本加载语义和第三方初始化是否影响路径？ | [bundles-and-build.md](bundles-and-build.md)、[runtime-and-rendering.md](runtime-and-rendering.md)、[dependency-and-delivery-controls.md](dependency-and-delivery-controls.md) |
| Server / delivery | HTTPS、跳转、DNS/TLS、CDN、缓存、内容编码、404、Cookie、source map 与 Service Worker 策略是否和发布流程一致？ | [network-transport-and-domains.md](network-transport-and-domains.md)、[dependency-and-delivery-controls.md](dependency-and-delivery-controls.md) |

上游清单中“性能工具”部分，映射到本 skill 的 baseline、Chrome trace/HAR、Lighthouse、field data 与验证流程；不要因为工具列在清单中就安装、接入或上传项目数据。

## 按现在的项目条件裁决

以下上游类型的条目必须依证据裁决，不能逐条机械勾选：

- **CSS/JS 合并**：HTTP/1.1、HTTP/2/3、缓存边界和请求瀑布不同；不以“请求数越少越好”为规则。
- **内联/关键 CSS、preload、prefetch、preconnect**：只在当前关键路径、资源候选和带宽竞争得到证明时采用。
- **`async`/`defer` 与内联脚本**：服从脚本依赖顺序、DOM 时机、安全策略和初始化需求；不对所有脚本一刀切。
- **Service Worker**：它不是前端性能的必选项。仅在离线、重复访问或后台能力的收益超过更新与缓存复杂度时考虑。
- **SVG 与图片格式**：根据内容、透明度、视觉质量、显示尺寸与浏览器覆盖选择；不将 SVG 一律替换为位图，也不将所有位图转 AVIF。
- **CDN、HTTPS、Cookie、Brotli**：分别验证业务边界、缓存键、协议/响应头、认证和实际 `Content-Encoding`；“产物中有 `.br`”不是开启 Brotli 的证据。
- **固定阈值**：页面重量、加载时长、字体总量和 TTFB 必须从目标用户、基线和业务路径制定预算；保留 upstream 的数字只可作讨论起点。

## 最少要交付什么

```markdown
# 前端性能 Checklist 审查

## 范围与限制
页面/路径、构建与发布版本、设备网络、缓存条件、证据来源；哪些项目未覆盖。

## P0 / P1 / P2 findings
仅列有证据且会改变优先级的项目：发现、证据、影响、建议、风险、验证。

## 覆盖状态
| 检查面 | 已验证 | 发现 | 不适用 | 未调查 | 下一步 |
| --- | --- | --- | --- | --- | --- |
| 图片 | … | … | … | … | … |

## 未调查项
只列会改变发布或优化决定的最小取证动作。
```

正常情况下不要把所有核对项逐行倾倒给用户。先给结论和 findings；只有用户需要交付审计表、上线门禁或团队复核时，才输出完整状态表。

## 这份映射怎么维护

上游项目的条目、链接和工具会演进。本 skill 以其检查面作为补漏框架，并以当前浏览器/构建器证据为优先依据。需要更新映射时，重新阅读上游 README 和许可证；不要把过期的工具名、协议假设或固定阈值无审查搬进本地规则。
