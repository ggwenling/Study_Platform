# API 接入说明

API 根路径为 `/api/v1`，交互式接口定义见 `/docs`。示例假设浏览器通过同源代理访问后端；跨源时要使用允许的 Origin、`credentials: "include"`，并正确配置 HTTPS 和 Cookie。服务不会在响应中返回密钥。

## 登录与请求

`POST /auth/register` 接收 `{email, password, display_name}`，`POST /auth/login` 接收 `{email, password}`；成功后服务设置 HttpOnly 的 `study_session` 和可读取的 `study_csrf` Cookie。`GET /me` 获取当前用户，`POST /auth/logout` 退出。登录和注册也会检查 Origin。除登录注册外，写请求需要 Cookie、`X-CSRF-Token`（值为 `study_csrf`）和受允许的 Origin；GET 不需要 CSRF 头。部署 HTTPS 时配置 `COOKIE_SECURE=true`。

在 `frontend/src` 下可直接导入 `./api/client` 中的 `api`、`uploadMaterial`、`streamChat` 和 `retryTurn`。Vite 已配置 `/api` 转发到本地后端。客户端会携带 Cookie，并给写请求添加 CSRF 请求头。完整路径及响应字段见 [OpenAPI 契约](../contracts/frontend.openapi.json)。

业务错误返回 `{code, message, request_id, retryable}`。401 引导登录，403 检查 CSRF 和 Origin，404 表示资源不可访问。

## 资料流程

1. `POST /knowledge-bases`，JSON 例如 `{"name":"数学","description":null}`；记录返回的知识库 `id`。`GET /knowledge-bases` 获取列表。
2. 向 `POST /knowledge-bases/{kb_id}/materials` 上传一个 `file` multipart 字段。返回 202 和 `{material_id, version_id, job_id, status}`。上传进入的知识库由路径确定。
3. 轮询 `GET /jobs/{job_id}` 或 `GET /materials/{material_id}`，直到资料状态 `ready`、`failed` 或 `cancelled`。处理中可见 `queued`、`parsing`、`ocr`、`indexing`；`POST /jobs/{job_id}/cancel` 取消，失败后可用 `POST /materials/{material_id}/retry` 显式重试。worker 必须持续运行。
4. `GET /material-versions/{version_id}/preview` 返回文字及 locator；`/content` 提供原件，`/pages/{page_no}` 提供 OCR 渲染页图（存在时）。这些接口均需登录。PDF locator 的 page 为真实页码；DOCX 为段落，TXT 为行，图片和 OCR 文本包含位置坐标。OCR 只提取文字，不解释图表或判题。

默认限制：文件 20 MiB、PDF 50 页、图片 2000 万像素；支持 PDF、DOCX、UTF-8 TXT、PNG、JPEG。扫描页使用 RapidOCR 与 ONNX Runtime CPU。模型首次使用可能需要下载模型文件。原件保存在服务端私有目录，不能直接暴露该目录。

## 对话、引用与恢复

可先 `POST /conversations` 创建对话，也可在首次聊天省略 `conversation_id`。每轮 `POST /chat/stream` 使用 JSON：

```json
{
  "conversation_id": null,
  "knowledge_base_id": "知识库 UUID",
  "answer_mode": "knowledge_only",
  "intent": "qa",
  "content": "解释这份资料的重点",
  "material_ids": null
}
```

请求需要 `Idempotency-Key` 和 CSRF 头。同一次提交在响应丢失后可以复用原请求和键。已结束的轮次会重放结果，进行中的轮次返回 409。`answer_mode` 默认 `knowledge_only`，用户可选 `with_general`。`intent` 为 `qa` 或 `summary`。问答省略 `material_ids` 时检索当前库已就绪的资料。摘要须明确选择已就绪资料，上限为 40 个分块且总计 24,000 字符。每轮保存自己的知识库与索引范围，切库后原消息仍可查看。资源仅对所属用户可访问，删除后历史引用可能标为不可用。

这是 **POST SSE**：用 `fetch` 读取 `text/event-stream`，不能直接用仅支持 GET 的 `EventSource`。事件为 `meta`、`delta`、`sources`、`done`、`stopped` 或 `error`；`meta` 包含 `conversation_id` 和 `turn_id`，应立即保存以便恢复。`delta` 逐段追加，`sources` 的 citations 含位置与引用 ID，`done` 提供最终消息。可通过 `GET /sources/{citation_id}` 查看来源，相关原件和预览 URL 也需要鉴权。

```ts
import { streamChat } from './api/client'

const requestKey = crypto.randomUUID()
for await (const event of streamChat({
  knowledge_base_id: selectedKnowledgeBaseId,
  content: '这份资料的重点是什么？',
}, requestKey)) {
  handleChatEvent(event)
}
```

流中断后用 `api.turn(conversationId, turnId)` 查询已保存的状态和内容。用户主动停止可调用 `api.cancelTurn(conversationId, turnId)`。最后一个失败或停止的轮次可通过 `retryTurn(conversationId, turnId)` 在原资料范围内重新生成。流中的 `error` 事件会由客户端抛出 `ApiError`。界面收到 `done` 后才将回答标为完成。

## 运行与费用

开发库执行 `uv run alembic upgrade head`；如需独立的 LangGraph 表再执行 `uv run study-setup-checkpointer`。启动 `uv run study-api` 与独立的 `uv run study-worker`。应用的问答流程通过 LangChain 调用模型、在业务数据库记录轮次，不使用 LangGraph checkpointer 恢复执行。

默认聊天模型为 `deepseek-flash`，向量模型为 `qwen3.7-text-embedding-flash`，向量维度 1024。密钥仅配置在服务端 `DEEPSEEK_API_KEY`、`EMBEDDING_API_KEY`，不要放到前端。模型请求可能产生费用；余额、权限或额度不足时返回错误，不自动换模型，也不会对资料任务反复重试此类错误。`/health/ready` 只检查配置存在，不验证密钥有效性或余额，也不会发起模型调用。
