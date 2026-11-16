# Vite、webpack、Next.js、Nuxt：构建与交付适配

确认过配置、构建命令或依赖里的框架后，再看这一页。先记下框架和主版本、SSR/SSG/静态导出方式、实际部署目录，以及 CDN/运行时适配器。不同主版本或部署方式下，同名配置未必是一个意思。

| 框架 | 先确认 | source map 决策 | 资源与缓存检查 |
|---|---|---|---|
| Vite | `vite.config.*`、`build.outDir`、是否 SSR | `build.sourcemap` 默认 `false`；若用 `'hidden'` 供错误上报，仍须上传 map 后从公开部署中排除。 | 审计实际 `outDir`（常为 `dist`）；检查导入资源是否带 hash，`public/` 文件是否仍使用稳定名称并配短缓存/版本策略。 |
| webpack | `webpack.config.*`、`output.path`、`mode`、`devtool` | `devtool: false` 不生成 map；错误上报可评估 `hidden-source-map`，并不把 map 放到 Web 服务器。 | 检查 `output.filename`/asset module 的内容哈希策略、`publicPath` 与实际 CDN URL；以部署响应头而非 config 猜测缓存。 |
| Next.js | `next.config.*`、App/Pages Router、`output`、自托管还是静态导出 | `productionBrowserSourceMaps` 默认关闭；开启前确定错误上报/公开调试策略。 | 不审计 `.next/cache`；静态导出审计 `out`，自托管确认运行包与 `/_next/static` 一起部署。若使用 `assetPrefix`，将额外 DNS/TLS 成本纳入 Network 证据。 |
| Nuxt | `nuxt.config.*`、Nuxt 主版本、Nitro 预渲染/服务器模式 | `sourcemap` 可分别设 client/server；当前文档的默认是 client `false`、server `true`，仍要检查版本与错误上报需求。 | 预渲染静态交付通常审计 `.output/public`；`public/` 文件不会经过构建指纹化，`app/assets/` 经过构建处理。对 `public/` 长缓存必须自行版本化或确认不会变。 |

## 不管什么框架，都按这个顺序

1. 运行项目定义的生产构建，不用 development server 代替。
2. 对实际会上传/运行的输出目录运行 `node "$FRONTEND_PERF_SKILL_DIR/scripts/audit-build-output.mjs" <output-dir>`；排除构建缓存和临时目录。
3. 仅在需要时生成 source map：上传错误平台后验证其可符号化，再确认公开资产路径无法访问 map。
4. 对 HTML、哈希静态资源、未指纹化 `public/` 资源分别用生产 URL 核验 `Cache-Control`、`Content-Type`、`Content-Encoding`、`Vary` 与最终 URL。
5. 用生产构建的冷/热缓存 trace/HAR 和关键路径回归测试复核。框架的“自动缓存”不替代 CDN、反向代理、对象存储或多实例部署的验证。

## 各框架最容易踩的坑

- **Vite**：`'hidden'` 不是“不生成 map”；它仍产出文件。`public/` 资源不因 Vite 自动改名而获得内容缓存安全性。
- **webpack**：`hidden-source-map` 只是去除浏览器引用；如果 map 被发布到公开目录，仍可能被直接访问。
- **Next.js**：不要将动态/个性化 HTML 与带 hash 的 `/_next/static` 资源使用同一缓存规则。静态域名/CDN 也不是免费优化，它会新增连接成本。
- **Nuxt**：不要把 `public/` 当成构建处理资产目录；稳定 URL 的更新、缓存与回滚需要交付层明确处理。SSR/Nitro 的 server map 策略也不能从 client map 设置推断。

## 官方参考

- [Vite build options](https://vite.dev/config/build-options)
- [webpack devtool](https://webpack.js.org/configuration/devtool/)
- [Next.js self-hosting and caching](https://nextjs.org/docs/15/app/guides/self-hosting)
- [Nuxt assets](https://nuxt.com/docs/4.x/getting-started/assets)
- [Nuxt sourcemap configuration](https://nuxt.com/docs/4.x/api/nuxt-config)
