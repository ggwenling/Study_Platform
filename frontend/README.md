# 前端

需要 Node.js 20.19+ 或 22.12+。

```sh
npm install
npm run dev
```

打开 http://127.0.0.1:5173，登录或注册后即可创建知识库、导入资料并对话。Vite 将 `/api` 代理到 `127.0.0.1:8000`，可通过 `SYNAPSE_API_TARGET` 指定其他后端地址。后端必须允许浏览器实际使用的 Origin。

后端需要完成数据库迁移并运行 `uv run study-api`。导入资料还需要运行 `uv run study-worker`。模型密钥只配置在服务端，详见 [API 接入说明](../docs/api-integration.md)。

运行 `npm test` 检查 API 客户端，运行 `npm run build` 检查 TypeScript 并构建生产版本。生产部署需配置同源 `/api` 代理和 SPA 路由回退。
