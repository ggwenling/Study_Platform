# API 接入说明

API 根路径为 `/api/v1`，交互式接口定义见 `/docs`。示例假设浏览器通过同源代理访问后端；跨源时要使用允许的 Origin，并正确配置 HTTPS 和 Cookie。注册、登录、刷新及退出请求需要 `credentials: "include"`；业务请求使用 Bearer 令牌。服务不会在响应中返回服务端密钥。

## 登录与请求

`POST /auth/register` 接收 `{email, password, display_name}`，`POST /auth/login` 接收 `{email, password}`。两者返回 `{user, access_token}`，并设置有效期默认七天的 HttpOnly `study_refresh` 与可读取的 `study_csrf` Cookie。前端只在内存中保存有效期最长 15 分钟的访问令牌。包括 `GET /me`、上传、资料原件、OCR 页图和 SSE 在内的受保护请求统一使用 `Authorization: Bearer <access_token>`。

`POST /auth/refresh` 使用刷新 Cookie 恢复登录并返回新的 `{user, access_token}`。`POST /auth/logout` 撤销会话并清除 Cookie。两者需要 `X-CSRF-Token`，值取自 `study_csrf`，并检查 Origin。客户端合并并发刷新请求。部署 HTTPS 时设置 `COOKIE_SECURE=true`。

升级到 `0003_bearer_auth` 前，在服务端设置至少 32 字节的随机 `AUTH_JWT_SECRET`。运行迁移后一起重启前后端。原会话会撤销，用户重新登录即可访问原有数据。

`GET /auth/options` 返回 Google 和邮件恢复是否可用。Google 登录需要 `GOOGLE_CLIENT_ID`、`GOOGLE_CLIENT_SECRET` 和应用地址 `AUTH_PUBLIC_URL`。Google 客户端的授权回调地址为该应用地址加 `/api/v1/auth/google/callback`。浏览器访问 `/auth/google/start` 开始授权，同邮箱账号通过 `POST /auth/google/link` 提交现有知循密码完成绑定。

`POST /auth/password/forgot` 接收 `{email}`，配置 `SMTP_HOST`、`SMTP_PORT` 和 `SMTP_FROM` 后通过 SMTP 投递一次性链接。默认使用 STARTTLS，提供商要求身份验证时设置 `SMTP_USER` 和 `SMTP_PASSWORD`。仅本地邮件收件箱可用 `SMTP_STARTTLS=false` 测试。`POST /auth/password/reset` 接收 `{token, password}`。重置链接 30 分钟过期，完成后账号原有会话均失效。

登录弹窗可根据 `401` 响应的 `code` 定位输入框。本地联调时在仓库根目录 `.env` 中设置 `LOGIN_DETAILED_ERRORS=true` 并重启后端。公开部署保留默认的 `false`，避免通过错误响应判断邮箱是否对应可登录账号。

| 错误码 | 提示 | 对应输入框 |
| --- | --- | --- |
| `invalid_account` | 此邮箱对应的账号不可登录 | 邮箱。未注册、已禁用和已删除账号共用此提示。 |
| `invalid_password` | 密码错误 | 密码。邮箱对应可登录账号时才返回。 |
| `invalid_credentials` | 邮箱或密码错误 | 表单通用提示。关闭分项提示时返回。 |

缺少字段或格式错误返回 `422 validation_error`。凭据校验失败时不设置新的会话 Cookie。

在 `frontend/src` 下可直接导入 `./api/client` 中的 `api`、`uploadMaterial`、`streamChat` 和 `retryTurn`。Vite 已配置 `/api` 转发到本地后端。原件和页图通过客户端读取后，使用临时 Blob 地址预览或下载。完整路径及响应字段见 [OpenAPI 契约](../contracts/frontend.openapi.json)。

业务错误返回 `{code, message, request_id, retryable}`。401 引导登录，403 检查 CSRF 和 Origin，404 表示资源不可访问。

## 资料流程

1. `POST /knowledge-bases`，JSON 例如 `{"name":"数学","description":null}`；记录返回的知识库 `id`。`GET /knowledge-bases` 获取列表。
2. 向 `POST /knowledge-bases/{kb_id}/materials` 上传一个 `file` multipart 字段。返回 202 和 `{material_id, version_id, job_id, status}`。上传进入的知识库由路径确定。
3. 轮询 `GET /jobs/{job_id}` 或 `GET /materials/{material_id}`，直到资料状态 `ready`、`failed` 或 `cancelled`。处理中可见 `queued`、`parsing`、`ocr`、`indexing`；`POST /jobs/{job_id}/cancel` 取消，失败后可用 `POST /materials/{material_id}/retry` 显式重试。worker 必须持续运行。
4. `GET /material-versions/{version_id}/preview` 返回文字及 locator；`/content` 提供原件，`/pages/{page_no}` 提供 OCR 渲染页图（存在时）。这些接口均需登录。PDF locator 的 page 为真实页码；DOCX 为段落，TXT 为行，图片和 OCR 文本包含位置坐标。OCR 只提取文字，不解释图表或判题。

默认限制：文件 20 MiB、PDF 50 页、图片 2000 万像素；支持 PDF、DOCX、UTF-8 TXT、PNG、JPEG。扫描页使用 RapidOCR 与 ONNX Runtime CPU。模型首次使用可能需要下载模型文件。原件保存在服务端私有目录，不能直接暴露该目录。

## 对话、引用与恢复

知识库同时作为侧栏项目文件夹。会话的 `project_knowledge_base_id` 表示固定整理归属，独立于每轮检索范围：`POST /conversations` 可指定该字段；`GET /conversations?project_knowledge_base_id=<UUID>` 分页读取项目会话；列表和详情均返回该字段。首页首次发送会自动归入所选知识库，后续切换知识库不改变归属。删除知识库后会话转为未分类，历史消息保留。

`0004_project_conversations` 迁移只将单一有效知识库的旧会话自动归档；多库、空会话及已删除知识库的会话保留未分类。升级后重启 API。

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

请求需要 `Authorization: Bearer <access_token>` 和 `Idempotency-Key`。同一次提交在响应丢失后可以复用原请求和键。已结束的轮次会重放结果，进行中的轮次返回 409。`answer_mode` 默认 `knowledge_only`，用户可选 `with_general`。`intent` 为 `qa` 或 `summary`。问答省略 `material_ids` 时检索当前库已就绪的资料。摘要须明确选择已就绪资料，上限为 40 个分块且总计 24,000 字符。每轮保存自己的知识库与索引范围，切库后原消息仍可查看。资源仅对所属用户可访问，删除后历史引用可能标为不可用。

这是 **POST SSE**：用 `fetch` 读取 `text/event-stream`，不能直接用仅支持 GET 的 `EventSource`。事件为 `meta`、`phase`、`delta`、`sources`、`done`、`stopped` 或 `error`；`meta` 包含 `conversation_id` 和 `turn_id`，应立即保存以便恢复。`delta` 逐段追加，`sources` 的 citations 含位置与引用 ID，`done` 提供最终消息。可通过 `GET /sources/{citation_id}` 查看来源，相关原件和预览 URL 也需要鉴权。

`phase` 格式为 `{ "type": "phase", "turn_id": "UUID", "phase": "searching" }`，由真实执行阶段触发：`working` 表示准备上下文或校验与保存回答，`searching` 表示检索资料（含查询向量生成），`composing` 表示模型输出阶段。当前模型关闭深度思考，不发送 `solving`；前端为其保留类型和展示映射。阶段仅用于当前请求的临时视觉反馈，不写入历史记录或 GET 轮次结果。`meta.replay=true` 表示重放已结束结果，不播放动画；重放不发送阶段事件。旧客户端可忽略新增字段与事件。完成、停止、错误或断线后立即结束动画，恢复查询只显示文字状态，不根据旧阶段猜测当前进度。

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

来源变为不可用时，所有引用读取路径（包括历史、轮次查询和 SSE 重放）返回 `quote: ""`、`locator: {}`、`content_url: null` 和 `preview_url: null`；保留来源标识、名称、版本、定位标签及失效原因。历史回答正文仍保留，不能把它当作来源仍可读取的凭据。

可选查询改写由后端 `RAG_QUERY_REWRITE_ENABLED` 控制，默认关闭；不增加请求字段或 SSE 事件，实验步骤仍显示为 searching。它只补充当前库内的检索问题，最终回答针对原问题，不能扩大知识范围。行为与实测边界见 [质量回归说明](rag-quality.md)。

## 知识库详情与片段

均需 Bearer。路径前缀 `/knowledge-bases/{kb_id}`。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/overview` | 库内文件数、索引/OCR 进度、主题标签与按日活跃统计 |
| POST | `/sync` | 刷新统计并写 `last_synced_at` |
| GET | `/documents/{material_id}/locations` | 按原文段落/页聚合的片段位置 |
| GET | `/fragments` | 分页列出可检索片段（`q`、`tag`、`day`、`material_id`、`section_id` 等筛选） |
| GET | `/fragments/{chunk_id}` | 读取当前已就绪版本中的指定片段。片段失效或无访问权限时返回 `404` |
| PATCH | `/fragments/retrieval` | JSON `{"ids":["UUID",…],"enabled":true\|false}` 批量开关片段是否参与检索 |
| GET/POST/PATCH | `/documents/{material_id}/classification` | 主题/标签建议与确认 |

片段 `retrieval_enabled=false` 时不进入向量检索与摘要汇总，但不影响历史消息展示；关闭后仍会参与同 scope 的对话历史（仅作表达参考，不作事实依据）。

## 工作区搜索

`GET /search` 使用 Bearer 认证。默认按知识库名、资料标题和会话标题匹配。传 `include_content=true` 后也匹配当前已就绪的资料片段以及研究笔记的标题和正文。用 `next_cursor` 作为下一页的 `cursor`，并沿用原关键词和 `include_content`。

正文命中返回 `excerpt`。`fragment` 结果的 `id` 是片段 ID，附带 `locator` 和 `retrieval_enabled`。手动搜索也能找到已关闭 AI 检索的片段。`note` 结果的 `id` 可用于 `GET /notes/{note_id}`。

## 认证强化说明

注册、登录、刷新与退出要求请求带受信任 `Origin` 或 `Referer`（与 `allowed_origins` 一致）。`POST /auth/login` 与 `POST /auth/register` 受每 IP 速率限制（默认 30 次/分钟，超出返回 `429 rate_limited`）。响应体 `AuthDTO` 含 `csrf_token`；刷新会轮转 `study_refresh` 与 `csrf_token`，并使上一枚 access JWT 立即失效（`tg` 代次）。`study_csrf` Cookie 为 HttpOnly，前端应用内存中的 `csrf_token` 设置 `X-CSRF-Token`。部署 HTTPS 时须 `COOKIE_SECURE=true`（且 `AUTH_PUBLIC_URL` 为 `https://` 时启动会校验）。

资料 `GET .../content` 仅在 `parse_status=ready` 时提供原件；上传校验文件头与扩展名一致。进行中返回 `409 content_not_ready`。

## 运行与费用

开发库执行 `uv run alembic upgrade head`；如需独立的 LangGraph 表再执行 `uv run study-setup-checkpointer`。启动 `uv run study-api` 与独立的 `uv run study-worker`。应用的问答流程通过 LangChain 调用模型、在业务数据库记录轮次，不使用 LangGraph checkpointer 恢复执行。

默认聊天模型为 `deepseek-flash`，向量模型为 `qwen3.7-text-embedding-flash`，向量维度 1024。密钥仅配置在服务端 `DEEPSEEK_API_KEY`、`EMBEDDING_API_KEY`，不要放到前端。

可选聊天备用：设置 `CHAT_FALLBACK_MODEL` 可在同一 OpenAI 兼容端点下切换到另一模型；也可同时配置 `CHAT_FALLBACK_BASE_URL` 与 `CHAT_FALLBACK_API_KEY` 指向其他提供商。未配置时行为与原来一致。非流式调用（学习出题/判题、检索改写、资料分类建议等）在主模型超时、限流（429）、5xx 或连接错误时会自动尝试备用；4xx 参数/客户端错误不切换。流式对话仅在尚未输出任何 token 时切换，已输出部分内容后不再切换。

`GET /me/model-usage?days=30` 返回当前登录用户自己的模型调用汇总（按天/用途/模型聚合），不含提示词、资料或作答正文；用量写入失败不影响主流程。设置页展示最近 30 天摘要。

模型请求可能产生费用；未配置备用时，余额、权限或额度不足仍直接返回错误，资料任务不会对此类错误反复重试。`/health/ready` 只检查配置存在，不验证密钥有效性或余额，也不会发起模型调用。
