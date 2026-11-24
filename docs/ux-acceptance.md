# 浏览器用户旅程验收

被测产品是 `cursor/deep-testing-prep-9b8e` 的精确提交 `783e680b3682058786a4f6511ea6010b01a42584`。本文件和 `frontend/e2e/` 属于证据提交，不改产品代码。证据提交 SHA 与产品 SHA 不同。

这次验收使用隔离库 `knowloop_ux_acceptance_test`、合成账号和本地模型桩。它不代表真实模型的回答质量或检索排序质量。真实模型端到端仍是 **NOT_VERIFIED**，由 P4 父任务另行完成。本结果也不表示可以合并。

上一轮产品 SHA `ac8d8bf6079834c4a9d6c28e407f38a546a659fb` 的记录留在证据历史和 `.local/ux-acceptance/baseline-ac8d8bf/`。那一轮生成任务是用预加载 API 的包装脚本跑完的，不能当作正式 worker 的通过结果。

## 环境

| 项 | 值 |
| --- | --- |
| 产品 SHA | `783e680b3682058786a4f6511ea6010b01a42584` |
| Node | v22.23.4 |
| Python | 3.12.3 |
| uv | 0.13.9 |
| PostgreSQL | 16.15 |
| pgvector | 0.6.0 |
| Playwright | 1.65.1，Chromium |
| 页面 | `http://127.0.0.1:5173` |
| API | `http://127.0.0.1:8000`，本轮由 `uv run study-api` 重启 |
| Worker | `uv run study-worker`，进程是 `.venv/bin/study-worker` |
| 模型桩 | `http://127.0.0.1:18080/v1` |
| 数据库 | `knowloop_ux_acceptance_test`，schema `study` 与 `study_graph` |
| 学习生成 | `LEARNING_STUB_GRADER=true` |

模型桩的向量是常数 `[0.2] * 1024`。带检索资料的对话固定返回 `概念说明 [S1]`。分类请求返回 `{"topic":"虚构矿物","tags":["青柠晶体","验收夹具"]}`。付费密钥为空。

## 冷启动

新解释器只执行 `from study_platform.services import studio`，没有先导入 API 包。结果是导入成功，`count_due_flashcards` 可调用。相对 `ac8d8bf`，产品改动是 `resources.py` 把该导入移进 `dashboard`。日志：`.local/ux-acceptance/logs/cold-start-783e680.txt`。

## 命令与结果

隔离环境已经存在时，不要重跑 `frontend/e2e/run-isolated.sh`，以免覆盖 `.env`。在产品代码包含上述 SHA、API 与正式 worker 已启动后：

```bash
cd frontend/e2e
npm test
```

2026-11-24 06:21:41 UTC，11 项全部通过，耗时 44.8 秒。逐步记录在 `.local/ux-acceptance/journey.json`（该目录不入库）。

| 步骤 | 结果 | 耗时 | 说明 |
| --- | --- | --- | --- |
| 访客不能进入私有页 | 通过 | 3.7s | `/knowledge` 回到首页登录框 |
| 注册并在刷新后保持会话 | 通过 | 2.6s | 合成账号 `ux-a-1795501257519@example.test` |
| 个人模型连接指向本地桩 | 通过 | 4.7s | 先建连接并设为默认，再建知识库 |
| 拒绝坏文件并索引合成 TXT | 通过 | 6.9s | `.exe`、空文件被拒绝；夹具状态 `ready` |
| 空库在调用模型前拒绝 | 通过 | 3.9s | 页面显示资料不足；桩的 chat 计数没有增加 |
| 提问并打开引用 | 通过 | 2.0s | 阅读器定位到 `synthetic-concept.txt` 行 1，原文含「37 度下保持稳定」。回答是桩文本 |
| 笔记保存并在刷新后仍在 | 通过 | 4.5s |  |
| 搜索、历史、学习、设置、首页 | 通过 | 1.8s |  |
| 闪卡生成、评分、刷新后仍记住 | 通过 | 5.8s | 评分「良好」后待复习为 0；自由重温显示已复习 1 次。卡片来自学习桩 |
| 第二个账号看不到第一个账号 | 通过 | 5.1s | 页面无该库；知识库与会话 API 均为 404 |
| 退出后私有路由回到登录 | 通过 | 2.7s |  |

同一轮正式 worker 日志 `.local/ux-acceptance/logs/worker-783e680.log` 没有 `ImportError`，进程在旅程结束后仍是 `study-worker`。隔离库中本轮任务：

| 任务 | 状态 | 进度 | 时间 (UTC) |
| --- | --- | --- | --- |
| ingest | succeeded | 100 | 06:21:13 |
| classification | succeeded | 100 | 06:21:14 |
| flashcards | succeeded | 100 | 06:21:29 |

## 先前的 worker 导入失败

在 `ac8d8bf` 上，`uv run study-worker` 处理第一笔闪卡任务时退出，页面停在「生成中 · 0%」。原因是 `resources.py` 在模块顶层导入 `count_due_flashcards`，而 `studio` 加载 API 包时该名字尚未就绪。`783e680` 把这行导入放进 `dashboard`。本轮用正式入口冷启动和完整旅程复核，导入成功，三类任务都是 `succeeded`。包装脚本 `frontend/e2e/support/run-worker.py` 已从验收路径删除。

## 测试假设

下面这些现象来自测试选择器或夹具前提，不作为产品缺陷。

- TXT 按非空行切成片段。三行夹具得到 3 个就绪片段；现用单行夹具，断言只要求片段数大于 0。
- 知识库名称同时出现在侧栏标题和页面一级标题上。断言限定一级标题。
- TXT 阅读器没有「原文上下文」页签。该页签只在可以渲染页面时出现。阅读器已经打开，并标出行号和句子。
- 「密码」「API Key」旁边有显示按钮，宽松标签会匹配到两个控件。
- 搜索框角色是 `combobox`，名称是「搜索关键词」。
- 同一问题同时出现在侧栏会话和历史按钮「会话：…」上。
- 「练习」会同时匹配「历史练习」。
- 「翻转查看答案」同时匹配卡片表面的「点击翻转查看答案」和按钮「翻转查看答案 [Space]」。点击后者。
- 首页金属发送钮的无障碍名称「发送」在取样瞬间为 0。Enter 会提交，随后的对话页能看到向上箭头。这不是稳定的功能缺陷。
- 去掉串行后，失败会换一个新的 Playwright worker。验收保持串行。

`ac8d8bf` 的 PR 检查里，`test_timeout_and_short_key_never_exposed` 曾因随机连接 id 含有子串 `abc` 误报。产品提交 `783e680` 收紧了该断言。候选 CI 已读一次：push [45339464288](https://github.com/ggwenling/Study_Platform/actions/runs/45339464288) 与 pull request [45339470519](https://github.com/ggwenling/Study_Platform/actions/runs/45339470519) 都是 success。没有为了变绿而重跑。

## 模型桩的边界

- 回答「概念说明 [S1]」只说明页面能展示桩文本和引用按钮。它不衡量回答质量。
- 全部向量相同，余弦相似度都是 1。它不衡量检索排序。
- 闪卡正文来自 `LEARNING_STUB_GRADER`，不衡量出卡质量。

## 再跑下一个 SHA

1. 确认检出的就是要测的产品 SHA。
2. 保留根目录带 `KNOWLOOP_UX_ACCEPTANCE` 标记的 `.env`、`.local/ux-acceptance/` 和 `frontend/e2e/`。
3. 用该 SHA 重启 `uv run study-api` 和 `uv run study-worker`。不要预加载 API 包。
4. 在 `frontend/e2e` 执行 `npm test`。
5. 报告里分开写产品 SHA 和证据提交 SHA。

未覆盖：真实供应商模型、视觉判题、付费调用、生产数据。不要据此打 tag、部署或合并。
