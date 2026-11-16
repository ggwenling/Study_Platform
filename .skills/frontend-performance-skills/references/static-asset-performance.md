# 静态资源：定位、判断与安全处理

静态资源优化不是比赛谁把文件压得更小。目标是少做关键路径上不必要的下载、解码、执行和缓存失效，同时不伤视觉、品牌和功能。这里处理图片、字体、CSS、静态 JavaScript、SVG、媒体和 source map；证据来自 Chrome 请求时，也读 [chrome-devtools-artifacts.md](chrome-devtools-artifacts.md)。

## 先列清单，再动文件

从真实用户旅程的 Network/HAR 开始，而不是从仓库中最大的文件开始。记录资源 URL/域名、类型、initiator、优先级、开始/结束时间、传输大小、解码大小（可见时）、缓存来源和与 LCP/交互的关系。随后在构建产物、manifest 或源码中检索它的来源：

```bash
# 在项目根目录检索可能被构建或引用的静态资源；不修改文件。
rg --files -g '*.{avif,gif,jpeg,jpg,png,svg,webp,woff,woff2,ttf,otf,mp4,webm}'
rg -n --glob '*.{html,css,js,jsx,ts,tsx,vue,svelte}' 'hero\.avif|/assets/|url\('
```

URL 可能经过 CDN 重写、构建哈希、框架图片组件或 service worker 处理；找不到完全相同的文件名时，先追 manifest/构建映射和 Initiator。不要仅因本地原图很大，就断言用户下载了它。

一条资源 finding 至少写清：它在哪条旅程、什么缓存条件下、由谁发现、什么时候开始、占用了哪种成本（传输/解码/执行/渲染）、代码或产物中的来源、以及为什么这次改动不会损坏正确性。

## 按资源类型下手

### 图片与动画

先确认实际显示尺寸、设备像素比、首屏位置、是否为 LCP、内容类型（照片、插画、文字截图、透明图）和解码成本。格式是后面的选择，不是起点。

- 响应式照片通常要提供匹配显示尺寸的候选，并用 `srcset`/`sizes` 或框架等价能力让浏览器选择；不要让一张桌面原图承担全部断点。
- LCP 图片优先检查发现时机、加载优先级和懒加载。屏外图片才考虑懒加载，且要覆盖快速滚动与轮播。
- AVIF/WebP 往往适合照片和复杂图像，但要按目标浏览器覆盖、编码耗时和视觉质量验证。对 SVG、图标、文字/线稿截图或需要无损边缘的资产，转换成有损位图可能更差。
- GIF 体积或解码成本高时，考虑静态首帧加按意图播放，或视频格式；必须保留自动播放、循环、无障碍替代和产品语义。
- 图片字节变小不代表体验必然更好。大图仍可能有昂贵解码；过强压缩、错误裁切、透明度丢失或色彩配置变化都是功能/品牌回归。

### 字体、SVG 与图标

- 字体检查实际使用的字重、字符集、`unicode-range`、跨域和 `font-display`。子集化前要覆盖多语言、动态文本、货币符号和 emoji 回退；删掉真实字符是正确性问题，不是性能收益。
- SVG 先检查是否把位图 data URI、未使用的滤镜、超大路径或重复 defs 打进来。需要保留 `viewBox`、可访问名称、CSS/JS 选择器和动画钩子；不要把 SVG 一律 rasterize。
- 图标库应从构建产物和 import chain 证明实际进入的内容，再考虑按图标导入、sprite 或内联；不能只按包名或文件个数推断。

### CSS、JavaScript、媒体与 source map

- CSS：区分首屏阻塞样式、当前路由样式和低优先级样式；拆分或内联关键 CSS 前，用 trace 验证是否真的缩短了渲染阻塞。
- 静态 JS：资源的下载体积只是开始；回到 trace 检查解析、编译与执行。对代码拆分还要检查是否让关键路由产生新请求瀑布。
- 音视频：关注码率、时长、首帧、海报图、`preload`、自适应流和用户是否真的播放。不要为了缩小文件而降低可理解性或无障碍字幕。
- source map：将生产浏览器是否会公开下载、错误监控是否需要、CDN 缓存与部署安全分开讨论。不要把 source map 当普通首屏资源，也不要未经确认删除发布调试所依赖的映射。

## 图片压缩和格式转换

只有在用户明确要求改动资源，或已批准实施建议时才运行转换。先在副本上做一个代表性资产的实验，检查视觉质量、透明度、方向、色彩和实际渲染尺寸；确认后再处理同类资源。不要对整个目录做未经审查的批处理。

当需要按 JPEG、PNG、SVG、字体、音视频等具体格式选择压缩策略、命令或在线工具时，读取 [static-asset-compression.md](static-asset-compression.md)。

本 skill 提供 [optimize-image.mjs](../scripts/optimize-image.mjs)：它一次只处理一个栅格图像、默认写入新文件、不会覆盖输入，使用本机 ImageMagick 7 的 `magick` 命令。它会自动处理 EXIF 方向并默认移除元数据；如果颜色配置或 EXIF 对产品有意义，先在副本上比较，必要时传入 `--keep-metadata`。

```bash
# 先确认命令和输出路径，不写文件。
node "$FRONTEND_PERF_SKILL_DIR/scripts/optimize-image.mjs" public/hero.jpg \
  --format webp --width 1600 --quality 78 --dry-run

# 写入 public/hero.optimized.webp；原图不变。
node "$FRONTEND_PERF_SKILL_DIR/scripts/optimize-image.mjs" public/hero.jpg \
  --format webp --width 1600 --quality 78

# 转换包含透明度的图片时，先验证视觉结果与透明边缘。
node "$FRONTEND_PERF_SKILL_DIR/scripts/optimize-image.mjs" public/illustration.png \
  --format avif --width 1200 --quality 70 --out public/illustration.avif
```

脚本不安装依赖；当 ImageMagick 没有编译目标格式的 delegate 时会失败并提示。此时不要假设格式可用：选择已有的图片 CDN/构建管线，或在经用户批准后安装相应工具。脚本只面向 PNG、JPEG、WebP、AVIF 等栅格资源；SVG 优化、GIF/视频转码和响应式候选生成需要按项目现有管线单独处理。

## 改完怎么验、怎么交接

转换或改动引用前后，至少完成以下闭环：

1. 保留原始文件、记录命令、编码器版本和输入/输出字节数；质量参数不是跨格式可比的“百分比分数”。
2. 在目标断点、设备像素比和背景色下对比视觉结果，特别检查文字、渐变、皮肤色、透明边缘、裁切与方向。
3. 在本地/预发布环境确认每个资源 URL、`srcset` 候选、响应头、MIME 类型和回退路径都正确；没有对应 HTTP 配置时，生成 AVIF/WebP 文件不会自动让浏览器使用它。
4. 用相同的冷/热缓存条件重录 Network/trace，比较 LCP 图片的发现/开始/结束、传输大小、解码/渲染影响和缓存命中。
5. 发布后按页面、设备和网络分组检查 field data；若 LCP 没有改善，回到请求发现和渲染阶段，不要继续盲目降低质量。

## 几个容易想当然的地方

- “最大的源文件”不一定是用户下载最多、最早或最关键的文件。
- “WebP/AVIF 更小”不代表更适合所有内容、浏览器或缓存策略。
- “缓存命中”不表示资源永远正确：内容哈希、HTML 引用、CDN cache key 和 service worker 更新必须一起验证。
- “删除 metadata”可能损害色彩、版权或工作流；将它作为可验证的取舍，而不是默认的质量保证。
- “格式文件已经生成”不等于线上加载路径已切换；用实际请求和回退验证。
