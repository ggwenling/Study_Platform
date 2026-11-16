# 依赖、重复实现与生产交付控制

这份手册把性能检查往依赖安装、引用边界、生产构建和发布交付前推一步。先拿到能复核的证据，再动 lockfile、依赖版本、构建配置或 CDN。磁盘里有重复包、工具函数重名、产物里出现 `.br`，都还不足以说明用户真的受影响或配置已经正确。

## 1. 重复依赖：先查来源，再谈去重

区分三件事：安装树中的多份包、构建产物中的重复模块、以及不同路由实际下载的代码。只有后两者与前端传输和执行直接相关；版本不兼容时保留两份依赖可能是正确的。

先识别 lockfile 和 workspace，再选择同一个 package manager 的只读命令。命令可能因版本差异返回非零退出码或显示 peer dependency 问题；把它作为调查信号，不要以失败码直接改 lockfile。

| 包管理器 | 只读调查 | 获得实施授权后的候选动作 |
|---|---|---|
| npm | `npm find-dupes`；`npm ls <package> --all`；`npm explain <package>` | 先复核 peer range 和生产构建，再运行 `npm dedupe`；它会修改本地安装树/lockfile。 |
| pnpm | `pnpm why <package> --recursive`；`pnpm dedupe --check` | 复核 workspace overrides、catalog 和 peer 组合后，再运行 `pnpm dedupe`。 |
| Yarn | `yarn why <package>` | Yarn Classic 与 Berry 的 dedupe 能力/插件不同；先确认 `yarn --version` 与仓库已有脚本，不要复制 npm/pnpm 命令。 |

先从 bundle stats 或模块图确认某个重复版本是否进入同一路由/同一 chunk，再追 `why`/`explain` 输出的父依赖。可选修复依次是：升级可兼容的直接依赖、统一工作区版本、使用有明确兼容依据的 override/resolution，最后才是 dedupe。不能为了数字好看强制把不兼容的 major 版本锁成一份。

也避免把新的包管理器当作“去重方案”。迁移 package manager 会改变 lockfile、安装布局和 CI 行为，是独立的工程决策。

## 2. 重复轮子与引用：按行为合并，别按文本合并

先用目录和 import 路径找到候选，再通过调用点确认输入、错误语义、缓存、权限、埋点和异步取消是否一致。两个 `formatDate`、`request` 或 `useFetch` 名字相近，不表示可以合并；反过来，两个不同名字的实现也可能重复了昂贵的依赖或初始化。

```bash
# 不改代码：查看可能分散的共享层和它们被谁引用。
rg --files src app packages | rg '/(utils|helpers|lib|shared|hooks|services)/'
rg -n --glob '*.{js,jsx,ts,tsx,vue,svelte}' "from ['\"][^'\"]*(utils|helpers|lib|shared|hooks|services)"

# 在已知某个重复库/工具后，定位全部导入和动态导入边界。
rg -n --glob '*.{js,jsx,ts,tsx,vue,svelte}' "from ['\"]<package-name>['\"]|import\(['\"]<package-name>"
```

调查顺序：

1. 从 stats/trace 确认候选是否进入重要路由，或是否在运行时重复初始化。
2. 建立调用点矩阵：输入输出、失败方式、缓存/重试、权限、埋点、副作用和 bundle 边界。
3. 仅在语义一致时抽取公共实现或统一公开导入路径；保留调用点的行为测试。
4. 删除旧实现/旧依赖前，运行类型检查、测试、生产构建和相应用户路径。

项目已配置重复代码、未使用依赖或架构边界工具时，优先运行已有脚本。不要为了扫描而未经授权安装 `jscpd`、Knip、bundle analyzer 等新开发依赖；新增工具本身也会改变 lockfile 和 CI 时间。

特别检查三种容易同时伤害体积和运行时的重复：同一 SDK/日期库/图表库的多个入口版本、多个请求客户端或缓存实例、以及每个页面各自注册的全局监听器/埋点初始化。结论必须引用 import chain、模块图或 trace，而不是仅根据文件名。

## 3. 构建产物和静态资源缓存

先构建项目的真实生产命令，然后检查产物。可用只读脚本快速建立清单：

```bash
node "$FRONTEND_PERF_SKILL_DIR/scripts/audit-build-output.mjs" dist
# Next、Nuxt、Angular 等项目应将 dist 替换为实际可部署的输出目录。
```

该脚本报告大文件、独立/内联 source map、`.br`/`.gz` 预压缩文件和看起来带内容指纹的资源名。它只看目录，无法看到 CDN、反向代理、对象存储或 service worker 的实际响应。

对真正会被部署的 URL 再做响应头核验：

```bash
curl -sSI --compressed 'https://example.com/assets/app-<hash>.js'
curl -sSI --compressed 'https://example.com/'
```

检查点：

| 内容 | 通常期望 | 需要防的回归 |
|---|---|---|
| 带内容哈希且 URL 随内容变化的 JS/CSS/字体/图片 | 长 TTL，例如 `Cache-Control: public, max-age=31536000, immutable` | 哈希未变却内容变化；CDN cache key 忽略查询/编码；旧 HTML 引用被过早删除的资源。 |
| HTML / app shell | 短 TTL 或 `no-cache`，以便重新验证并取得新资源引用 | 将 HTML `immutable` 导致发布后仍加载旧入口；个性化响应被共享缓存。 |
| source map | 根据调试/错误上报策略决定是否上传和是否公开 | map 公开暴露源码/路径；错误平台拿不到 map；误删导致无法还原线上错误。 |
| `.br` / `.gz` 预压缩文件 | 服务器/CDN 根据 `Accept-Encoding` 返回正确变体和 `Content-Encoding` | 文件被当成普通下载；`Vary: Accept-Encoding`/缓存键错误；动态压缩与预压缩重复或不一致。 |

生产环境可以由 CDN 动态压缩，因而产物目录没有 `.br`/`.gz` 不必然是问题；反之，目录中有预压缩文件也不代表它已被服务。发布要保证 HTML、哈希静态资源和 CDN/对象存储更新具备原子性或可回滚顺序，避免新旧版本互相引用不存在的文件。

## 4. 生产构建和 source map

先运行仓库定义的生产 build，而不是用开发服务器或手工设置一个环境变量代替构建模式。检查构建配置和部署流水线中：生产模式、minification、浏览器目标、环境变量注入、静态资源 public path、生成/上传 source map 的步骤，以及是否把开发调试客户端带进产物。

source map 不是一个“全开还是全关”的开关。先选清楚下面哪种策略：

1. **不需要线上符号化**：生产构建不生成 map，产物和构建时间更小。
2. **错误上报需要 map，但浏览器不应访问**：生成 map，上传到错误平台，部署时排除 map；不能只依赖“hidden”来保护未授权访问。
3. **浏览器调试确实需要 map**：公开 map 前确认源码暴露、部署边界和缓存策略；可评估不含 `sourcesContent` 的方案，但它仍可能暴露文件结构。

对于 Vite，`build.sourcemap` 默认是 `false`；`'hidden'` 只是不在 bundle 中写 source-map 注释，仍会生成 map 文件。对于 webpack，`devtool: false` 不会生成 map；`hidden-source-map` 适合 map 仅供错误上报的场景，但官方仍建议不要把 map 部署到 Web 服务器。配置应以框架、错误上报平台和实际发布流程为准。

当项目明确使用 Vite、webpack、Next.js 或 Nuxt 时，再读取 [framework-build-delivery.md](framework-build-delivery.md)，以确认实际输出目录、静态资源处理与 source map 配置，避免套用错误框架的规则。

避免在生产中使用 inline/eval 类 source map；它们会将调试信息嵌进 bundle 或偏向开发期体验。也不要因为关闭 map 就跳过压缩、浏览器兼容性和错误上报验证。

## 最少要交代什么

一份生产交付检查至少记录：

- 使用的包管理器、lockfile、重复依赖来源与是否实际进入关键路由；
- 合并/移除重复实现的语义对比、测试覆盖与产物变化；
- 生产构建命令、输出目录审计、最大文件和 source map 策略；
- 实际线上 HTML/静态资源的 `Cache-Control`、`Content-Encoding`、`Content-Type`、`Vary` 和内容哈希验证；
- 发布/回滚顺序，以及前后 Network、trace 与 field data 的验证结果。
