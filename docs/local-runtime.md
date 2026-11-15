# Windows 本地运行与恢复

适用于本人本机使用，几十份资料。先按 README 准备 Python 3.12 的 backend uv 环境、frontend npm 依赖、根目录 `.env`、PostgreSQL、迁移和必要的模型密钥。

## 启停

双击根目录 `Start-Synapse.cmd` 启动并打开浏览器，`Status-Synapse.cmd` 查看状态，`Stop-Synapse.cmd` 停止应用。也可在仓库根目录运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File infra/windows/local-runtime.ps1 start
powershell.exe -NoProfile -ExecutionPolicy Bypass -File infra/windows/local-runtime.ps1 status
powershell.exe -NoProfile -ExecutionPolicy Bypass -File infra/windows/local-runtime.ps1 stop
```

启动使用现有 API、worker 和 Vite 开发服务器，仅监听本机；API 端口 8000，Web 端口 5173。自动化检查可加 `-NoBrowser`。原有手动启动方式继续可用。

启动器记录 PID、创建时间、可执行路径和命令行，重复启动复用经核实的进程，并补起已退出的组件。停止时重新核实身份及子进程关系；PID 被复用时跳过。端口被其他进程占用时报告错误，不接管、不终止对方。部分启动失败只清理本次新建的进程。启停命令通过文件锁串行执行。

首次准备仍需人工执行依赖安装、数据库启动和迁移；启动器不自动执行这些步骤，也不改 PostgreSQL 服务启动类型。未就绪时检查 `.local/runtime/*.err.log`、数据库和 README 中的配置。日志与进程记录不提交 Git。

## 状态与故障恢复

- `status` 分别报告三个进程是否受管理且存活，以及 `/health/ready` 的结果。API 就绪不代表 worker 正在推进任务，也不验证模型密钥有效性、余额或 OCR 模型是否已缓存。
- worker 遇到数据库连接故障按 1、2、4、8、16、30 秒退避，之后保持最多 30 秒间隔；连接恢复后回到正常轮询。凭据、数据库名称或其他永久错误退出并记录错误类别，修复后重新运行启动入口。
- 处理中的数据库短故障可按现有最多三次尝试重新排队；进程崩溃后依赖现有五分钟租约重新领取。围栏机制阻止旧执行发布索引。磁盘和权限错误显示需处理原因，不无限重试。
- 页面显示真实阶段，不使用服务端粗略 progress 充当精确百分比。只有已就绪内容参与检索；失败或取消的资料可显式重试。状态请求失败时保留上次结果、标记尚未确认，点击“重新读取状态”恢复。
- 聊天已保存的部分输出在重启后保留。重访会话时查询轮次，超过默认 90 秒租约的运行收敛为失败；恢复查询不会重新付费生成。用户显式重试复用原轮次与消息。强制退出可能丢失最后一次保存后的少量输出，不承诺逐 token 续传。

本轮不安装 Windows 常驻服务、不做开机自启或进程自动监护。worker 退出后用状态入口识别，再运行启动入口恢复。手动离线备份与恢复列为独立后续任务；现有数据库和 `.local/storage` 持久化不等于已经具备备份。

## 校正已存储的 DOCX 表格列号

在 `backend` 运行预览，查看本机数据库名、可校正版本和跳过原因。

```powershell
uv run --no-sync python -m study_platform.cli.repair_docx_locators
```

有可校正记录时，停止访问该数据库的 API 和 worker，再把预览显示的库名传给 `--database`。例如本机默认库的执行命令如下。

```powershell
uv run --no-sync python -m study_platform.cli.repair_docx_locators --apply --database study_platform
```

命令按版本核对原件、段落、分块和引用哈希，在同一事务中校正预览、分块和历史聊天引用的单元格列号。原件缺失或内容对不上时跳过该版本。运行后再预览一次，确认可校正记录归零，再启动 API 和 worker。

## 验证入口

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File infra/windows/tests/local-runtime.tests.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File infra/windows/tests/local-runtime.process.tests.ps1
```

前者以隔离进程桩验证重复启动、过期 PID、端口冲突和部分启动清理；后者使用本机临时端口运行真实 Python 子进程，验证 venv 包装进程的子监听器识别及进程树清理。验证不会停止 PostgreSQL。后端及前端测试命令见 README，实测记录见 [本地 RAG 验收](verification-local-rag.md)。
