# 域名、传输层与重定向链

遇到“请求发出去前就慢”“DNS/SSL 是否异常”“跳转是否绕路”“域名太多会不会拖慢加载”这类问题，就按域名和连接来读网络证据。本机探测只能指方向；真实用户范围还得看 Chrome trace/HAR、CDN/服务器指标或 field data。

## 先画一张 origin 视图

对关键旅程的冷缓存 Network/HAR，按 **scheme + host + port** 分组，而不是只按文件类型分组。每个 origin 记录：归属（自有/CDN/API/第三方）、首个请求何时开始、请求数、传输量、是否关键、Initiator、连接/协议信息、重定向、缓存和响应 Timing。

在 DevTools Network 中显示 Domain，并结合第三方请求过滤、Initiator、Timing 和 Waterfall。特别留意：

- 页面 origin、静态资源 CDN、API、字体、图片 CDN、支付/风控/埋点是否各自建立连接；
- 哪些域名在 LCP/首屏资源出现前才首次请求；
- DNS、Initial connection、TLS、Waiting (TTFB)、Queueing/Stalled 分别出现在哪些请求；
- 同一 origin 的连接复用情况。复用连接的请求可能没有 DNS/TCP/TLS 阶段，这不表示该 origin 从未付出过连接成本；
- HTTP/1.1 的连接上限、请求优先级、Service Worker 和代理也会表现为 Queueing/Stalled，不能一律归因到 DNS。

将 origin 表和用户影响连在一起：一个仅在点击后才使用的支付域名与一个首屏字体/CDN 域名的优先级不同。不要仅因“域名数量多”要求合并；分域名可能是 CDN、隔离、地域路由、安全或业务合同的必要设计。

## DNS、TCP、TLS 和 TTFB 怎么看

Chrome Timing 的含义要分开：DNS Lookup 是名称解析；Initial connection 包含 TCP 握手/重试和 SSL 协商；Waiting (TTFB) 包含网络往返与服务器准备首字节的时间。Content Download 长还可能是带宽慢，或浏览器忙于读取响应。结合请求的 Remote Address、协议、响应头和服务器/CDN 指标再做判断。

用于补充本机观察的只读命令：

```bash
# 查看当前解析结果；本机 DNS 缓存、公司 VPN 和地域会影响结果。
dig +noall +answer example.com A AAAA CNAME

# 先只看第一个响应；再跟随链路，避免隐藏跳转。
node "$FRONTEND_PERF_SKILL_DIR/scripts/probe-url-timing.mjs" https://example.com/
node "$FRONTEND_PERF_SKILL_DIR/scripts/probe-url-timing.mjs" https://example.com/ --follow
```

`probe-url-timing.mjs` 使用一次无鉴权 GET 丢弃响应体，输出状态/Location、HSTS、Alt-Svc、Server-Timing（响应有提供时）及本机的 DNS、TCP、TLS、首字节、总耗时。它不打印 `Set-Cookie` 等敏感响应头。只对确认可安全读取的 URL 使用：脚本不发送请求体、Cookie 或认证信息，但服务端可能将 GET 定义为有副作用。其数值受到本机 DNS 缓存、IPv4/IPv6、VPN/代理、curl 的连接状态和所在 CDN 边缘节点影响；因此用于提出假设，不能代替多地域或 field 数据。

### 什么时候能下判断

| 证据模式 | 合理判断 | 还需验证 |
|---|---|---|
| 冷缓存 trace 中关键跨域资源在首次使用时才产生长 DNS/TCP/TLS | 连接建立可能推迟了关键资源 | 资源是否能更早发现；同一问题是否在目标地区/设备重复；是否已有连接复用。 |
| 某域名的 Waiting/TTFB 长，且 CDN/Server-Timing 或后端指标也慢 | 服务器、CDN 回源或网络往返是强线索 | 不要把 TTFB 直接写成“后端代码慢”；拆 CDN 命中、边缘位置和服务阶段。 |
| 仅本机 `dig` 或 curl 慢 | 此网络路径可能有问题 | 换网络/地区、Chrome 录制、RUM/CDN 分布；不能外推所有用户。 |
| 多个首屏请求 Queueing/Stalled | 可能有优先级、HTTP/1.1 连接、缓存或调度竞争 | 查看协议、Priority、连接复用和请求发现链，而不是只增加域名或 preconnect。 |

## 重定向和 HTTPS

先分别探测不跟随和跟随重定向的 URL，再在 Chrome 关键旅程中确认谁触发了跳转。一个从手输 `http://` 到规范 `https://` 的永久跳转在首次访问可能是合理的；`http -> https -> www/non-www -> locale -> 应用路径` 这类链会增加往返与关键资源发现延迟，应尽量收敛到一次规范跳转。

检查：状态码、每个 `Location`、跨域跳转、最终 URL、是否由服务端 HTTP 跳转而非 meta refresh/JavaScript 跳转、以及短链/广告/登录提供商等业务跳转是否仍必要。登录、支付和同意流程可能必须跨域，不能以性能名义绕过安全边界。

所有页面和子资源都应使用 HTTPS；HTTP 子资源可能触发 mixed-content 阻止或额外跳转。对需要兼容用户输入 HTTP 的域名，HTTP 端应规范跳转至 HTTPS；`Strict-Transport-Security` 只能通过 HTTPS 响应建立，之后浏览器才能直接把该 host 的 HTTP 访问升级。是否启用 HSTS preload、是否包含子域名都是域名所有权和回滚风险决策，需由负责方确认，不能由前端单独开启。

## 跨域、CDN、第三方和协议

每新增一个关键 origin 都可能增加 DNS、TCP、TLS 和调度成本；HTTP/2、HTTP/3、连接复用和 connection coalescing 可以改变这个成本，但取决于证书、IP、浏览器和 CDN 配置，不能只从域名字符串推断。查看实际协议、Connection ID（可见时）和 Waterfall。

可选优化必须与证据匹配：

- **让关键资源早发现**：优先让 URL 出现在 HTML/CSS 中；这是比猜测性连接提示更可靠的第一步。
- **`preconnect`**：只用于确定、很早且重要的跨域 origin。跨域匿名资源通常需要 `crossorigin`。每个 preconnect 都会消耗连接/TLS 资源，过多会与关键下载竞争。
- **`dns-prefetch`**：适用于可能会用到但不够确定的域名，成本通常低于 preconnect；不要把它当作解决慢 API 或多跳转的替代品。
- **CDN/静态域名**：检查地理覆盖、TLS/协议、缓存命中、内容哈希、回源和 URL 稳定性。迁移域名前先比较不同区域/运营商的真实路径，且保留发布/回滚策略。
- **第三方**：将网络请求、主线程执行和业务价值放在同一表里。可考虑按用户意图、同意状态或非关键阶段加载，但分析、风控、支付等功能的业务/合规要求优先。
- **CORS 与 preflight**：观察 OPTIONS 是否落在高频关键路径；只在不改变认证、缓存、隔离和安全语义的前提下减少不必要的预检。不要为了“同源更快”把服务硬合并。

`Alt-Svc` 可能表明服务器宣传了其他协议路径，但不证明用户已使用 HTTP/3。`Server-Timing` 能将部分服务器阶段暴露给 DevTools；缺失该头不等于服务器没有可观测性。

## 交付时怎么写

```text
旅程与条件：冷缓存首访 / 设备网络 / 区域或本机探测边界。
Origin 地图：页面、CDN、API、字体、第三方；每个 origin 的关键性与连接证据。
重定向链：输入 URL -> 每个状态和 Location -> 最终页面；是否影响关键路径。
主要判断：DNS、连接/TLS、TTFB、队列、下载或资源发现中的哪一项限制体验。
建议：最小变更，例如修正规范跳转、使资源可在 HTML 中发现，或对一个确定 origin 使用 preconnect。
风险：HSTS/域名归属、缓存、跨域认证、第三方合同、带宽竞争。
验证：同条件 HAR/trace；线上响应头与协议；必要时按地区/网络的 field/CDN 指标。
```

避免“全站加 preconnect”“所有域名合并”“所有 HTTP 一律临时重写”“TTFB 高就是后端慢”这类建议。网络层需要将浏览器时序、服务端/CDN 和域名治理三类证据放在一起。

## 官方参考

- [Chrome DevTools Network Timing phases](https://developer.chrome.com/docs/devtools/network/reference)
- [curl timing variables](https://curl.se/docs/manpage.html?category=23)
- [Resource hints: preconnect and DNS prefetch](https://web.dev/articles/preconnect-and-dns-prefetch)
- [HTTP redirects](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/Redirections)
- [Strict-Transport-Security](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Strict-Transport-Security)
