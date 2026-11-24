# 浏览器用户旅程

这套测试只在隔离的 `knowloop_ux_acceptance_test` 库和本地模型桩上跑完整页面流程。它不调用付费模型，也不读取真实知识库。

文件名使用 `*.e2e.ts`，因此前端 `npm test`（Vitest）不会收集它们。

```bash
frontend/e2e/run-isolated.sh
```

脚本会拒绝覆盖一份不含 `KNOWLOOP_UX_ACCEPTANCE` 标记的根目录 `.env`。验收记录写在 `docs/ux-acceptance.md`。模型回答是固定桩文本，不能当作真实回答质量。

Worker 使用产品入口 `uv run study-worker`，不预加载 API 包。
