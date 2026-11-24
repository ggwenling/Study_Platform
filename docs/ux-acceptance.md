# 浏览器用户旅程验收

被测产品是 `cursor/deep-testing-prep-9b8e` 的精确提交 `ac8d8bf6079834c4a9d6c28e407f38a546a659fb`。本文件和 `frontend/e2e/` 是另一次证据提交，不改产品代码。证据提交的 SHA 与上面的产品 SHA 不同。

这次验收使用隔离库 `knowloop_ux_acceptance_test`、合成账号和本地模型桩。它不代表真实模型的回答质量或检索排序质量。真实模型端到端仍是 **NOT_VERIFIED**，由 P4 父任务另行完成。本结果也不表示可以合并。

## 环境

| 项 | 值 |
| --- | --- |
| 产品 SHA | `ac8d8bf6079834c4a9d6c28e407f38a546a659fb` |
| Node | v22.23.4 |
| Python | 3.12.3 |
| uv | 0.13.9 |
| PostgreSQL | 16.15 |
| pgvector | 0.6.0 |
| Playwright | 1.65.1，Chromium |
| 页面 | `http://127.0.0.1:5173` |
| API | `http://127.0.0.1:8000` |
| 模型桩 | `http://127.0.0.1:18080/v1` |
| 数据库 | `knowloop_ux_acceptance_test`，schema `study` 与 `study_graph` |
| 学习生成 | `LEARNING_STUB_GRADER=true` |

模型桩的向量是常数 `[0.2] * 1024`。带检索资料的对话固定返回 `概念说明 [S1]`。分类请求返回 `{"topic":"虚构矿物","tags":["青柠晶体","验收夹具"]}`。付费密钥为空。

## 命令与结果

隔离环境已经存在时，不要重跑 `frontend/e2e/run-isolated.sh`，以免覆盖 `.env`。在产品代码检出为上述 SHA、API 与页面已启动后：

```bash
cd frontend/e2e
npm test
```

2026-11-24 06:04:42 UTC，11 项全部通过，耗时 43.1 秒。逐步记录在 `.local/ux-acceptance/journey.json`（该目录不入库）。

| 步骤 | 结果 | 耗时 | 说明 |
| --- | --- | --- | --- |
| 访客不能进入私有页 | 通过 | 3.4s | `/knowledge` 回到首页登录框 |
| 注册并在刷新后保持会话 | 通过 | 2.7s | 合成账号 `ux-a-*@example.test` |
| 个人模型连接指向本地桩 | 通过 | 4.1s | 先建连接并设为默认，再建知识库 |
| 拒绝坏文件并索引合成 TXT | 通过 | 6.3s | `.exe`、空文件被拒绝；夹具状态 `ready` |
| 空库在调用模型前拒绝 | 通过 | 4.0s | 页面显示资料不足；桩的 chat 计数没有增加 |
| 提问并打开引用 | 通过 | 2.6s | 阅读器定位到 `synthetic-concept.txt` 行 1，原文含「37 度下保持稳定」。回答是桩文本 |
| 笔记保存并在刷新后仍在 | 通过 | 3.7s |  |
| 搜索、历史、学习、设置、首页 | 通过 | 1.8s |  |
| 闪卡生成、评分、刷新后仍记住 | 通过 | 6.2s | 评分「良好」后待复习为 0；自由重温显示已复习 1 次。卡片来自学习桩 |
| 第二个账号看不到第一个账号 | 通过 | 4.3s | 页面无该库；知识库与会话 API 均为 404 |
| 退出后私有路由回到登录 | 通过 | 2.8s |  |

同一轮入库、分类、闪卡任务在隔离库中均为 `succeeded`。

## 测试假设

下面这些现象来自测试选择器或夹具前提，不作为产品缺陷交给父任务。

- TXT 按非空行切成片段。三行夹具得到 3 个就绪片段；现用单行夹具，断言只要求片段数大于 0。
- 知识库名称同时出现在侧栏标题和页面一级标题上。断言限定一级标题。
- TXT 阅读器没有「原文上下文」页签。该页签只在可以渲染页面时出现。阅读器已经打开，并标出行号和句子。
- 「密码」「API Key」旁边有显示按钮，宽松标签会匹配到两个控件。
- 搜索框角色是 `combobox`，名称是「搜索关键词」。
- 同一问题同时出现在侧栏会话和历史按钮「会话：…」上。
- 「练习」会同时匹配「历史练习」。
- 「翻转查看答案」同时匹配卡片表面的「点击翻转查看答案」和按钮「翻转查看答案 [Space]」。点击后者。
- 首页金属发送钮的无障碍名称「发送」在取样瞬间为 0。Enter 会提交，随后的对话页能看到向上箭头。一次 20 秒等待没有找到该名称，同一 SHA 的其他运行曾经找到。没有稳定到可以单独记成功能缺陷。
- 去掉串行后，失败会换一个新的 Playwright worker，后面的登录断言用的是尚未注册的邮箱。已恢复串行。那两次失败不是账号系统缺陷。

## 产品缺陷：生成任务让 worker 退出

直接运行 `uv run study-worker` 时，第一笔笔记、闪卡或练习任务会让进程退出。2026-11-24 05:56:38 UTC，闪卡任务停在 `running`、进度 0。05:56:39 UTC 日志为：

```text
ERROR Worker stopped: ImportError; check local configuration and storage
```

页面停在「记忆闪卡 · 生成中 · 0%」。worker 入口只记录异常类型，完整栈是：

```text
studio.py -> api.v1.materials.create_job
api/v1/__init__.py -> chat.py -> resources.py
resources.py: from study_platform.services.studio import count_due_flashcards
ImportError: cannot import name 'count_due_flashcards' from partially initialized module 'study_platform.services.studio'
```

复现：

```bash
cd backend
uv run python -c 'from study_platform.services import studio'
```

API 进程先加载 `study_platform.api.v1`，所以问答接口不受影响。worker 在 `ai_jobs.run` 里才导入 `studio`，因此入库可以完成，生成任务才会触发。`studio.py`、`api/v1/__init__.py`、`api/v1/resources.py`、`api/v1/materials.py` 相对 `3bd2585e433105e573556add717fab22586e3f62` 没有差异，这个循环在该候选之前就已经存在。

本验收没有改这些文件。`frontend/e2e/support/run-worker.py` 先导入 API 包再调用同一个 worker `main`，只为让浏览器旅程继续。用这个入口后，闪卡任务变为 `succeeded`，复习步骤通过。产品侧的修复仍应去掉这条循环导入。

## 模型桩的边界

- 回答「概念说明 [S1]」只说明页面能展示桩文本和引用按钮。它不衡量回答质量。
- 全部向量相同，余弦相似度都是 1。它不衡量检索排序。
- 闪卡正文来自 `LEARNING_STUB_GRADER`，不衡量出卡质量。
- 桩最初对分类请求返回 `OK`，分类任务记为 `model_response_invalid`。入库仍变为 `ready`，与「分类失败不回滚已就绪索引」一致。桩现已返回分类要求的 JSON，本轮分类任务成功。这是夹具缺口，不是产品缺陷。

## 再跑下一个 SHA

1. 确认检出的就是要测的产品 SHA。
2. 保留根目录带 `KNOWLOOP_UX_ACCEPTANCE` 标记的 `.env`、`.local/ux-acceptance/` 和 `frontend/e2e/`。
3. 用该 SHA 重启 API。生成任务在产品修复前继续用 `frontend/e2e/support/run-worker.py`。
4. 在 `frontend/e2e` 执行 `npm test`。
5. 报告里分开写产品 SHA 和证据提交 SHA。

未覆盖：真实供应商模型、视觉判题、付费调用、生产数据。不要据此打 tag、部署或合并。
