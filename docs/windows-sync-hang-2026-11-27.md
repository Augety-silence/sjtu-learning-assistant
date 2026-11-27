# Windows 持续同步问题：排查记录与防护修复

本轮已确认同步停留在 Canvas 阶段，并修复后台任务无限等待、后台凭据交互回退和 Canvas 诊断粒度不足三个代码问题。原调试包没有线程栈、请求记录或 Canvas 内部分阶段记录，尚不能确定 Windows 现场的具体阻塞调用；此次修复提供退出保障和下一轮定位证据，不能作为真实账号同步成功的证明。

代码基线为 `c954db0`（版本 `1.8.28`），源码项目为 [Augety-silence/sjtu-learning-assistant](https://github.com/Augety-silence/sjtu-learning-assistant)::cite[1]。

调试包未包含构建提交 SHA，不能证明测试用户的 EXE 与上述源码提交逐字一致。

## 附件证据

输入为 `bd5f84655ebb97faa9df2f4e8032f9c1.png` 和 `sjtu-learning-assistant-debug-20261127_184851.zip`。调试包只含五个诊断文件，不含源码、密码、Token 或用户学习正文。

| 证据 | 观察 | 含义 |
| --- | --- | --- |
| `app_info.json` | 1.8.28、Windows 11 AMD64、Python 3.12.10、打包运行 | 排查对象是 Windows 桌面发行包 |
| `logs/sync-stages.jsonl` | 两次子任务的数据库阶段均约 110 ms 完成，设置阶段亦完成 | 卡点已在数据库初始化与设置读取之后 |
| 同上 | 2026-11-27 18:02:38.619 与 18:02:49.081（北京时间）进入 `canvas / sync / started`，之后无完成或失败记录 | 最后有证据的阶段为 Canvas 同步 |
| `app_info.json.generated_at` | 2026-11-27 18:48:56.954（北京时间） | 距两次 Canvas 开始分别约 46 分 18 秒、46 分 8 秒 |
| `status/summary.json` | `running=true`；各最近成功时间为空；`sync_runs` 最近记录数为 0 | 应用仍认为同步运行中，包内没有成功落库的同步证据 |
| `app_info.json.config` | `canvas_configured=true`；`mail_configured=false`；`ai_configured=false` | Canvas 凭据存在不等于鉴权成功；当前运行实例的邮箱配置状态与报障自述不同 |
| `runtime_environment` | `mail_account_configured=false`；`credential_status_error=false` | 当前实例连邮箱账号都未配置；不能把“已在其他设备/用户保存”当作当前账号可用的证明 |
| `manifest.json` | 桌面日志、旧同步日志缺失，只含粗粒度阶段日志 | 无法进一步区分凭据读取、客户端创建、网络等待与课程资源读取 |

两次 Canvas 阶段记录可能来自多个应用进程或不同启动；日志未包含 PID，不能据此确定进程数量或断言它们一直同时存活。数据库可连接只证明本次连接与检查完成，不等于完成所有数据完整性检查。

邮箱账号为空时，密码存在性检查直接返回 `false`，没有可查询的账号。因此 `mail_configured=false` 表示当前实例没有识别到有效邮箱配置，不能进一步证明系统凭据库中所有账号的邮箱密码都不存在。

截图显示开启自动下载，但现有日志没有进入下载阶段的证据。不能把本次长等待归因于文件量、AI 归档或邮箱密码，也不能据此断言 Canvas Token 无效。

## 代码修复

`dashboard_service.py` 原先在等待线程中直接调用无超时的 `process.wait()`，直到子进程退出才释放 `_sync_lock`。子进程阻塞时，`sync_status()` 因该锁持续占用而一直返回 `syncing`；HTTP 客户端的 20 秒网络操作超时并不构成整轮同步的期限，也无法覆盖客户端构造、交互输入或底层阻塞调用。

本轮为应用启动的整轮同步设置 **15 分钟期限**。到期后停止同步进程树，等待最多 5 秒，再按需强制终止并等待 5 秒；确认进程结束后才释放同步锁。Windows 通过 `taskkill.exe /PID <应用启动的PID> /T /F` 清理包装器及后代，其他平台清理应用创建的独立进程组。超时同时写入脱敏阶段日志和失败记录；旧成功时间、同步游标不被主动推进。若操作系统无法终止进程，则保留互斥并记录失败时间，避免再启动一个写库任务。

Canvas 的 `get_token()` 新增 `interactive` 参数，保留独立 CLI 的交互能力；同步管线明确传入 `interactive=False`，凭据缺失或读取失败立即返回可处理错误。桌面启动同步子进程时同时将标准输入设为 `DEVNULL`。这项修复针对后台交互回退的潜在阻塞；原包 `canvas_configured=true`，不能声称该回退就是本次现场根因。

新增 Canvas 阶段包括 `credentials`、`http_client`、`courses`、`announcements`、`assignments`、`files`、`folders`、`modules`、`module_items`、`http_request`、`persist`、`download`、`historical_materials`。课程进度仅记录计数；阶段日志不写入 Token、账号、课程名、原始 URL、服务端正文或异常文本。403/404 跳过受限资源、鉴权和网络错误终止本轮的已有规则继续适用。

涉及文件为 `sjtu_learning_assistant/canvas_sync.py`、`sjtu_learning_assistant/dashboard_service.py`、新增 `sjtu_learning_assistant/sync_process.py`、`sync_data_to_db.py`、新增回归测试和两个测试替身的 `wait(timeout=...)` 兼容调整。

## 验证与边界

定向测试 **146 项通过，60 项参数子测试通过**，覆盖真实挂起子进程的终止、失败持久化、互斥释放、强制终止升级、回收失败保留互斥、正常退出、已提交资料继续处理、Windows 进程树命令、后台凭据不交互，以及分阶段日志的脱敏和底层错误码。新增专属回归用例 13 项。完整后端回归 **1772 项通过、3 项跳过，803 项参数子测试通过**，耗时约 184 秒；有一条原有 ZIP 重复文件名测试产生的警告。差异格式检查通过。

初次完整回归因本测试环境的 `NO_PROXY` IPv6 项与 HTTPX URL 解析不兼容，出现 `InvalidURL: Invalid port: ':'`。两个代表用例在未修改的 `c954db0` 基线同样失败；只对测试进程限定 `NO_PROXY=localhost,127.0.0.1` 后通过。完整回归使用 `env NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1 python -m pytest -q -o faulthandler_timeout=60 tests` 并通过。此处调整没有修改产品的代理策略，也不是 Windows 用户现场代理异常的证据。

测试环境为 Linux / Python 3.11。尚未在 Windows 11 / Python 3.12.10 原生发行包中验证 `taskkill`、Windows 凭据读取及学校网络；也未用真实账号访问 Canvas、IMAP 或付费模型。本轮交付为修改后的源码项目，需要重新构建 Windows 安装包才能让测试用户获得修复。

追加 EXE 构建准备：前端 71 个文件／633 项测试、Biome、TypeScript 与 Vite 构建、Python/npm 许可证检查及秘密模式扫描通过。CI 新增 `windows_only` 手动输入，Windows 打包等待完整 Windows 测试通过，并调用已有 `frontend_startup_gate()` 验证实际打包 EXE 的界面和桥接。生成安装包以独立修复分支的固定提交为准；真实学校账号的复测仍由测试机器继续完成。

15 分钟是**整轮期限**，并非无活动超时。课程很多、首次下载很慢的正常任务也可能到期；此前已提交的记录保留，可再次同步继续下载。正在执行的数据库事务由数据库在进程退出后处理回滚；不能保证异常终止时尚未提交的文件或数据已经保存。不自动清空数据库或删除已归档资料。

## Windows 复测

1. 在任务管理器中结束这套应用及其仍在运行的同步子进程，使用新源码构建的 Windows 包重新启动。结束应用窗口不一定意味着此前启动的后台同步已经结束。
2. 在测试者实际使用的 Windows 登录用户下检查设置中的邮箱账号，保存账号和密码，再确认账号存在、密码已保存。Mac 或其他 Windows 用户的系统凭据不会自动迁移；保存成功不代表 IMAP 认证成功。
3. 首先关闭自动下载并同步，单独验证 Canvas 元数据是否完成；成功后恢复自动下载。保留既有数据，避免用重建数据库掩盖问题。
4. 若仍阻塞，重新导出诊断包。`credentials` 未结束指向凭据读取；`http_client` 未结束指向客户端初始化；某资源阶段及 `http_request` 未结束指向该资源请求；`persist` 未结束指向保存；下载或往期资料阶段未结束则按对应阶段继续排查。
5. 复测正常完成、凭据缺失、断网，以及等待超时四种情况。正常任务完成后同步按钮可再次使用；失败必须可见；超时成功回收后应显示失败并允许重试。TCP 连通、页面能打开或配置标记存在，均不能替代 API/IMAP 的真实同步验收。

下一轮仍需解决的是 Windows 现场的确切阻塞原因与新安装包验收。源码防护通过模拟和本地子进程测试，不代表已消除该机器的网络、凭据或系统环境问题。
