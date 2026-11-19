# 知循 · KnowLoop

**面向个人的知识工作台。整理资料，提出有引用的问题，再用自己的材料学习。**

[English](README.md) · 简体中文

[桌面截图](#桌面截图) · [核心功能](#核心功能) · [快速开始](#快速开始) · [技术架构](#技术架构) · [测试与验收](#测试与验收)

## 项目简介

知循把三个日常任务接在一起。把资料导入私有知识库，提问并核对引用，再把结论保存为研究笔记，或用原始资料进行练习。只记得一个短句或资料编号时，也能用全文搜索把原文找回来。

![知循桌面会话页面，回答标有引用编号，右侧显示原文依据](docs/screenshots/01-cited-chat.png)

*以上是实际运行的桌面网页。演示使用虚构资料，回答与引用由应用调用已配置的模型生成。*

## 桌面截图

| 知识库详情 | 工作区搜索 |
| --- | --- |
| ![浅色主题的桌面知识库详情，展示已索引的资料、活动和知识工坊](docs/screenshots/02-knowledge-base.png) | ![桌面 Ctrl+K 搜索窗口，展示命中的资料正文片段](docs/screenshots/03-search.png) |
| 查看处理进度、文件结构、片段和近期索引活动。 | 搜索资料正文、笔记标题和正文、知识库名称和会话标题。 |

| 研究笔记 | 学习 |
| --- | --- |
| ![已保存的研究笔记，包含对应的资料引用](docs/screenshots/04-research-note.png) | ![桌面学习页面，展示基于演示资料生成的闪卡](docs/screenshots/05-study.png) |
| 把回答与来源一起保存，随时回查依据。 | 用选定的知识库出题、生成闪卡并复习。 |

## 核心功能

| 使用流程 | 你可以做什么 |
| --- | --- |
| **收集与建立索引** | 上传 PDF、DOCX、Markdown、UTF-8 TXT、PNG 或 JPEG，或直接粘贴文本。后台 worker 解析资料，对符合条件的扫描件和图片做 OCR，再建立索引。可以查看进度和重试失败任务。 |
| **提问与核对** | 接收带引用的流式回答，把单轮问题限定到指定资料，对已就绪资料生成摘要。可以查看引用片段和可用的原页预览，也可以用检索命中测试检查片段匹配情况。 |
| **查找与整理** | 按 `Ctrl+K` 找回资料片段或研究笔记，直接打开命中位置。用项目组织会话，导出 Markdown，在历史中恢复已归档会话。 |
| **沉淀研究资料** | 创建笔记或将回答存为笔记。用知识工坊生成学习指南、知识导图或公式速查，也可以把选定笔记回写为资料。 |
| **练习与复习** | 从知识库生成测验和闪卡，使用文本或照片作答，回顾练习记录和错题。 |
| **配置模型** | 使用平台默认模型，或配置兼容的语言和向量连接。各知识库拥有独立的向量连接绑定，向量维度为 1024。 |

## 快速开始

以下命令适用于 **Windows PowerShell**。请准备 Git、[Python 3.12](https://www.python.org/downloads/)、[uv](https://docs.astral.sh/uv/)、带 npm 的 Node.js 22，以及已安装 pgvector 的 PostgreSQL 16 或更高版本。CI 使用 PostgreSQL 16。[Windows pgvector 安装说明](infra/windows/README.md)

### 1. 克隆仓库并安装依赖

```powershell
git clone https://github.com/ggwenling/Study_Platform.git
Set-Location Study_Platform
Set-Location backend
uv sync --locked
Set-Location ../frontend
npm ci
Set-Location ..
```

### 2. 配置数据库和模型

首次在本地安装且 PostgreSQL 监听 5432 时，可在 `backend` 执行初始化命令。脚本会提示输入本地 PostgreSQL 管理员密码，创建应用库、测试库和所需角色，并在仓库根目录生成带随机认证密钥的 `.env`。已有数据库配置时，可参照[数据库说明](docs/database-rules.md)和[环境变量模板](.env.example)手动填写。

```powershell
Set-Location backend
uv run python -m study_platform.cli.bootstrap_database --admin-user postgres
```

在根目录 `.env` 设置 `DEEPSEEK_API_KEY` 和 `EMBEDDING_API_KEY` 即可使用平台默认模型连接。配置个人连接和其他兼容供应商的方法见[模型连接说明](docs/model-connections.md)。

| 配置 | 用途 |
| --- | --- |
| `DATABASE_URL` | 应用访问 PostgreSQL 的连接。 |
| `MIGRATION_DATABASE_URL` | Alembic 执行结构迁移时使用的管理连接。 |
| `GRAPH_DATABASE_URL` | 独立 LangGraph checkpointer schema 的连接。 |
| `AUTH_JWT_SECRET` | 至少 32 字节的随机认证签名密钥。 |
| `DEEPSEEK_API_KEY`、`EMBEDDING_API_KEY` | 平台默认语言模型和向量模型的凭据。 |
| `MODEL_CONNECTIONS_KEY` | 加密「模型连接」中保存的凭据。 |
| `STORAGE_DIR` | 可选的原件及派生文件目录，默认为 `.local/storage`。 |

### 3. 迁移并启动服务

在 `backend` 目录运行：

```powershell
uv run alembic upgrade head
uv run study-setup-checkpointer
```

从仓库根目录打开三个终端，分别启动以下进程。

| 进程 | 命令 | 用途 |
| --- | --- | --- |
| API | `cd backend`，再执行 `uv run study-api` | 监听 `127.0.0.1:8000`，OpenAPI 文档位于 `/docs`。 |
| Worker | `cd backend`，再执行 `uv run study-worker` | 处理上传资料并更新索引。 |
| Web | `cd frontend`，再执行 `npm run dev` | 监听 `127.0.0.1:5173`，将 API 请求代理到 8000。 |

打开[本机应用](http://127.0.0.1:5173/)，注册账号，创建知识库并上传一份小型 TXT 或 PDF。待资料就绪后，提出一个文件中能找到答案的问题，再打开引用核对。[后端就绪检查](http://127.0.0.1:8000/health/ready)可确认结构迁移与存储配置，供应商连接会在实际建立索引或提问时使用。

完成首次配置后，可通过仓库根目录的 `Start-Synapse.cmd`、`Status-Synapse.cmd` 和 `Stop-Synapse.cmd` 管理 Windows 上的服务。[本地运行与恢复](docs/local-runtime.md)

## 技术架构

```mermaid
flowchart LR
    Browser["React 19 · Vite 前端"] --> API["FastAPI · HTTP 与 SSE"]
    API --> DB[("PostgreSQL · study schema")]
    API --> Files["本机资料存储"]
    DB --> Worker["后台资料处理 worker"]
    Worker --> Files
    Worker --> Index["文本提取 · OCR · 切分"]
    Index --> DB
    Worker --> Models["已配置的向量与语言模型"]
    API --> Models
```

前端使用 React、TypeScript 和 Vite。FastAPI 提供认证、检索、笔记、学习和流式问答，worker 独立处理资料任务。PostgreSQL 通过 pgvector 和内建的文本搜索能力存储应用数据及索引。Alembic 管理 `study` schema，LangGraph checkpointer 使用单独的 `study_graph`。[架构说明](docs/architecture/01_Architecture_Design.md) · [API 接入](docs/api-integration.md)

## 测试与验收

- 在 `backend` 执行 `uv run ruff check .` 和 `uv run pytest -q`。数据库集成测试需要显式配置含 pgvector 的独立 `*_test` 数据库，并设置 `RUN_DB_TESTS=1`。
- 在 `frontend` 执行 `npm test` 和 `npm run build`。生产构建包含 TypeScript 类型检查。
- [GitHub Actions](.github/workflows/ci.yml) 会执行前后端检查。验证范围和样本结果见 [CI 与本地测试配置](docs/ci.md)、[本地 RAG 验收](docs/verification-local-rag.md)以及[检索评测](docs/retrieval-evaluation.md)。

## 使用边界

- 默认单文件上限为 20 MiB，PDF 最多 50 页，图片最多 2000 万像素。扫描质量会影响 OCR 结果。
- 原件与应用记录保存在配置的主机上。使用托管模型连接时，建立向量或回答所需的输入会发送给对应供应商。请按资料性质选择供应商。
- 全文搜索基于已保存的正文。模型问答和学习产物依赖已配置的模型服务和原始资料质量。
- 已提供 Windows 启停和故障恢复说明。整库备份与恢复属于后续工作，Markdown 导出面向单篇笔记或单条会话。[本地运行与恢复](docs/local-runtime.md)

## 更多文档

[产品方向](docs/product/product-positioning-and-roadmap.md) · [模型连接](docs/model-connections.md) · [数据库规则](docs/database-rules.md) · [贡献与工作区规范](AGENTS.md)
