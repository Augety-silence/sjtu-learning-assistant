# SJTU Learning Assistant

当前完成阶段：

- **Phase 1A**：通过 IMAP SSL 只读访问交大邮箱。
- **Phase 1B**：获取 Canvas active courses。
- **Phase 1C**：逐门课程获取 Canvas 公告和作业。
- **Phase 2A**：建立 PostgreSQL、SQLAlchemy 与 Alembic 持久化基础。
- **Phase 2B**：将 Canvas 公告、作业和邮件增量写入 PostgreSQL，并生成统一事项。
- **Phase 2C**：同步 Canvas 课程文件、文件夹、模块与模块项元数据。
- **Phase 3A**：增加单实例后台运行器、JSONL 审计日志、有限重试与 macOS LaunchAgent 管理。
- **Phase 3B**：增加安全的 macOS 系统通知、首次同步基线、通知幂等账本与聚合限流。
- **Phase 4A**：按当前学期自动下载 Canvas 课程文件，保留旧版本并记录校验与下载状态。

系统通知覆盖 Canvas 新公告、新作业、新文件，以及 24 小时内截止且尚未提交的作业。Phase 4A 已实现本地文件归档；UI 尚未实现。

## 数据表如何关联

系统同时使用两类标识：

- `id`：PostgreSQL 内部主键，用于表间外键关联。
- `source_id`：Canvas 或 IMAP 的远端稳定 ID，用于重复同步时 Upsert 去重。

关系如下：

```text
courses.id
  ├── announcements.course_id
  ├── assignments.course_id
  ├── course_folders.course_id
  ├── course_files.course_id
  ├── course_modules.course_id
  ├── course_module_items.course_id
  └── items.course_id

course_folders.id       ── course_files.folder_id
course_modules.id       ── course_module_items.module_id
course_files.id         ── course_module_items.content_file_id
announcements.id        ── items.announcement_id
assignments.id          ── items.assignment_id
emails.id               ── items.email_id
course_files.id         ── items.course_file_id
items.id                ── notification_events.item_id

sync_state(source, resource)  每类资源唯一同步游标
sync_runs                     每次同步的审计记录
notification_events           通知幂等键、发送状态与失败信息
```

`items` 是统一信息层：Canvas 公告、Canvas 作业、课程文件和邮件都会生成对应的 `items` 记录。除了 `(source, item_type, source_id)` 联合唯一键，还通过显式外键连接原始表，数据库会阻止一个 Item 同时指向多个原始记录。

邮件通常不属于某门课程，因此 `emails` 不强制连接 `courses`；未来分类器识别出课程后，可以通过 `items.course_id` 建立课程归属。

## 当前目录

```text
sjtu-learning-assistant-phase1a/
├── alembic.ini
├── db_manage.py
├── migrations/
│   └── versions/
│       ├── 0001_create_core_tables.py
│       ├── 0002_link_items_to_sources.py
│       ├── 0003_add_canvas_content_metadata.py
│       ├── 0004_track_canvas_item_activity.py
│       ├── 0005_add_notification_events.py
│       └── 0006_add_course_file_download_fields.py
├── sjtu_learning_assistant/
│   ├── archive_service.py
│   ├── database.py
│   ├── mail_client.py
│   ├── models.py
│   ├── notifications.py
│   └── repository.py
├── sync_courses_to_db.py
├── sync_data_to_db.py
├── sync_runner.py
├── launchd_control.py
├── test_canvas.py
├── test_mail.py
├── requirements.txt
└── tests/
```

## 安装或更新依赖

```bash
cd sjtu-learning-assistant-phase1a
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

Phase 4A 不新增 Python 依赖。更新已有数据库后必须先运行 `python3 db_manage.py upgrade` 应用 Alembic `0006`。

## 数据库管理

检查连接：

```bash
python3 db_manage.py status
```

应用最新迁移：

```bash
python3 db_manage.py upgrade
```

本地默认连接：

```text
postgresql+psycopg:///sjtu_learning_assistant?host=/tmp&connect_timeout=5
```

远程 PostgreSQL 连接必须启用 `sslmode=require`、`verify-ca` 或 `verify-full`。在 Mac 上可通过以下命令无回显地保存到 Keychain：

```bash
python3 db_manage.py configure
```

远程部署时应由部署平台的 Secrets Manager 注入 `SJTU_DATABASE_URL`，不要提交 `.env`。

## Phase 2B / 2C：增量同步

Phase 2C 仍使用现有 Canvas Personal Access Token，不需要额外申请 API Key。它只是在同一 Canvas REST API 下增加 Files、Folders、Modules 和 Module Items 接口调用。

同时同步 Canvas 和邮箱：

```bash
python3 sync_data_to_db.py
```

脚本会从 Keychain 读取现有 Canvas Token 和邮箱密码，并提示输入邮箱地址。也可显式指定邮箱地址：

```bash
python3 sync_data_to_db.py --email "your.name@sjtu.edu.cn"
```

只同步 Canvas：

```bash
python3 sync_data_to_db.py --canvas-only
```

只同步邮箱：

```bash
python3 sync_data_to_db.py --mail-only --email "your.name@sjtu.edu.cn"
```

不发送/登记通知，仅执行同步：

```bash
python3 sync_data_to_db.py --no-notify
```

首次邮箱同步默认导入最近 100 封邮件，可调整：

```bash
python3 sync_data_to_db.py --mail-only --initial-mail-limit 500
```

## Phase 4A：Canvas 文件归档

默认归档根目录是 `~/Documents/SJTU Study`。程序仍同步所有 active courses 的公告、作业、文件夹、文件、模块和模块项元数据，但自动下载只选择“当前学期”。当前学期由运行日期推导：8–12 月为当学年 `Fall`，1–7 月为上一学年 `Spring`；Canvas 学期名按 `2026-2027 Fall` 这类格式匹配，不硬编码具体年份。

```bash
# 默认：同步元数据并自动下载当前学期文件
python3 sync_data_to_db.py --canvas-only

# 只同步全部元数据，不写归档目录
python3 sync_data_to_db.py --canvas-only --no-download

# 指定安全的测试/归档目录和当前学期
python3 sync_data_to_db.py --canvas-only \
  --archive-root "/path/to/SJTU Study" \
  --current-term "2026-2027 Fall"
```

`launchd_control.py install` 和 `run-once` 同样接受 `--no-download`、`--archive-root`、`--current-term`，参数会完整透传到同步子进程。`--no-download` 不影响任何元数据同步。

归档路径为 `<root>/<term>/<course>/<Canvas folder tree>/<filename>`。课程名、文件夹名和文件名都会清洗，最终路径必须位于 root 内；同目录同名文件追加 `[source_id]`。Canvas `folder_id`/`parent_folder_id` 用于还原目录树。

下载流程先使用带 Bearer Token 的 Canvas 同源 client 请求 `/api/v1/files/{id}`，再用完全独立且不含 `Authorization` 的 client 流式下载其 HTTPS URL，并允许对象存储跨域跳转。每个文件限制 500 MiB，最多尝试 3 次；每次尝试均累计 `download_attempts`，成功后保留历史累计值；写入时计算 SHA256 和实际大小，先落同目录临时文件，成功后 `os.replace`。远端版本变化时，旧文件移入同目录 `.versions/` 后再替换。单文件失败会写回 `failed` 与错误信息，但不会阻断其他文件。

`course_files` 的下载字段包括 `download_status`、`download_attempts`、`downloaded_at`、`downloaded_size`、`download_sha256`、`download_error`、`downloaded_source_updated_at` 和已有的 `local_path`。未来 UI 可构造 `ArchiveService` 并调用 `download_file_by_source_id(source_id)`，显式跨学期下载单个文件；自动任务仍只下载当前学期。

## Phase 3A：后台运行与 macOS LaunchAgent

后台入口不会改写 `sync_data_to_db.py` 的同步流程，而是将其作为子进程执行。运行器提供：

- `fcntl.flock(LOCK_EX | LOCK_NB)` 单实例锁；若已有任务运行，本次写入 `skip_locked` 后以 0 退出。
- 失败时最多执行 3 次，重试前分别等待 5 秒、30 秒；退出码 130 不重试。
- 收到 `SIGTERM` 或 `SIGINT` 时向同步子进程转发信号，并等待子进程退出。
- JSONL 日志只含时间、`run_id`、尝试次数、事件、退出码和耗时，不写命令参数、Token、密码或数据库 URL。

运行时文件位于：

```text
~/Library/Application Support/sjtu-learning-assistant/
├── logs/
│   ├── sync.jsonl
│   ├── launchd.stdout.log
│   └── launchd.stderr.log
└── run/
    └── sync.lock
```

### 手动运行一次

同时同步 Canvas 和邮箱（可在终端交互输入邮箱）：

```bash
.venv/bin/python launchd_control.py run-once
```

非交互方式示例：

```bash
.venv/bin/python launchd_control.py run-once --email "your.name@sjtu.edu.cn"
.venv/bin/python launchd_control.py run-once --canvas-only
.venv/bin/python launchd_control.py run-once --mail-only --email "your.name@sjtu.edu.cn" --initial-mail-limit 500
```

### 安装

安装命令会根据当前项目绝对路径生成
`~/Library/LaunchAgents/com.sjtu.learningassistant.sync.plist`，固定使用当前项目的 `.venv/bin/python`。任务登录后立即运行，之后每 900 秒触发一次。

```bash
.venv/bin/python launchd_control.py install --email "your.name@sjtu.edu.cn"
```

也可以只安装一个数据源：

```bash
.venv/bin/python launchd_control.py install --canvas-only
.venv/bin/python launchd_control.py install --mail-only --email "your.name@sjtu.edu.cn" --initial-mail-limit 500
```

也可以关闭已安装后台任务的通知：

```bash
.venv/bin/python launchd_control.py install --email "your.name@sjtu.edu.cn" --no-notify
```

`--canvas-only` 与 `--mail-only` 不能同时使用。`--email` 不能是空白字符串。若未指定 `--canvas-only`，安装时必须提供 `--email`，从而避免 launchd 在无交互环境等待输入。邮箱密码、Canvas Token 和数据库凭据仍由既有 Keychain/环境配置提供，不写入 plist。

### 验证与立即触发

```bash
.venv/bin/python launchd_control.py status
.venv/bin/python launchd_control.py kickstart
tail -n 20 "$HOME/Library/Application Support/sjtu-learning-assistant/logs/sync.jsonl"
```

查看 plist 的实际加载配置也可使用：

```bash
launchctl print "gui/$(id -u)/com.sjtu.learningassistant.sync"
```

### 卸载

```bash
.venv/bin/python launchd_control.py uninstall
```

卸载会停止 LaunchAgent 并删除 plist，不删除历史日志。后台调度仅支持当前登录用户的 macOS `gui/<uid>` LaunchAgent；不包含系统级 daemon、跨平台调度、监控告警或日志轮转。

### 验证通知

先确认当前终端进程拥有通知权限，再发送测试通知：

```bash
.venv/bin/python launchd_control.py notify-test
```

实现固定调用 `/usr/bin/osascript`，AppleScript 程序不拼接标题或正文；通知文本作为独立参数传入，不使用 shell。若 macOS 首次询问自动化/通知权限，请在“系统设置 → 通知”中允许对应终端或 Python 进程。

### Phase 3B 通知规则

- 首次通知基线只由 `sync_state(source='notification', resource='canvas_items')` 专属 marker 判断，不复用 Canvas 数据同步状态；因此即使 Phase 3B 安装前公告、作业和文件早已同步，首次启用通知仍不会弹出历史内容。
- 首次通知处理会扫描数据库中全部现有 active 公告、作业、文件以及当前 24 小时内到期候选，将对应事件写为 `suppressed`，全部成功后才写入通知 marker。
- 后续新增事件按 `items.id` 通知游标扫描，不依赖本轮数据 Upsert 返回的新增 ID；只有整轮通知候选都成功发送/记账后才推进游标。发送或账本失败时保留原游标，下一轮会重新扫描且不会漏事件。
- 每次同步也检查未来 24 小时内截止、仍为未提交状态的有效作业；这类候选不使用 Item 游标，每轮扫描并由包含作业 ID 与截止时间的 `event_key` 去重，截止时间改变后可重新提醒。
- 每个类别每轮最多显示 3 条系统通知；超过 3 条时显示前 2 条，并将其余内容合并为第 3 条。
- 正常通知发送前只查询已有 `event_key`，不插入 `pending`；发送成功后才原子写入 `sent`。发送失败不留永久占位，下一轮可重试；并发唯一键冲突通过 PostgreSQL Upsert 安全收敛。
- `notification_events.event_key` 唯一约束负责持久化幂等，稳定状态为 `sent` 或 `suppressed`；`pending`/`failed` 仅为兼容旧版本数据。
- 通知发送、通知状态写入或通知游标异常都不回滚或改变已经成功提交的数据同步；失败事件保持可重试。
- `--no-notify` 完全跳过通知生成和登记，适合临时静默同步。

## 增量策略

### Canvas

- 每门课程的公告、作业和文件分别保存 ETag。
- 后续请求发送 `If-None-Match`。
- 服务端返回 `304 Not Modified` 时不重复下载或写入该课程的数据，也不会误将历史文件标记为失效。
- 文件夹、模块和模块项进行完整分页读取；成功读取后，对远端已消失的记录做软下线，不硬删除历史数据。
- 分页资源为避免漏页不会保存单页 ETag，下一次进行安全全量读取并依靠 Upsert 去重。

### 邮箱

- 使用 `(邮箱地址, INBOX, UIDVALIDITY, UID)` 组成 `source_id`。
- `sync_state.cursor` 保存 UIDVALIDITY 和最高 UID。
- 后续只搜索 `last_uid + 1` 之后的 UID。
- 若 UIDVALIDITY 改变，自动执行安全的首次同步，而不是沿用失效游标。
- 全程使用 `BODY.PEEK` 与只读 INBOX，不改变邮件已读状态。

### 事务边界

数据 Upsert、统一 Item 写入和同步游标推进在同一事务中。任一步失败都会回滚，因此不会出现“数据未保存但游标已经前进”的漏消息情况。

## 数据表

- `courses`：Canvas 课程
- `announcements`：Canvas 公告
- `assignments`：Canvas 作业
- `emails`：邮件元数据与摘要
- `course_folders`：Canvas 文件夹层级
- `course_files`：课程文件元数据与后续本地归档路径
- `course_modules`：Canvas 课程模块
- `course_module_items`：模块中的文件、页面、作业等条目
- `items`：跨 Canvas 与邮件的统一事项；课程文件也会生成对应事项
- `sync_state`：资源级 ETag 或 IMAP UID 游标
- `sync_runs`：同步数量、状态与执行时间
- `notification_events`：通知幂等键、关联 Item、发送/抑制/失败状态
- `alembic_version`：数据库结构版本

## 自动化测试

```bash
python3 -m unittest discover -s tests -v
```

测试不需要真实凭证，覆盖 Canvas API、安全分页、ETag 304、IMAP UID 游标、数据库 URL 与密码脱敏、后台运行器，以及通知命令安全性、通知专属首次基线、`items.id` 游标、发送失败跨轮重试、通知数据库异常隔离、跨轮幂等、每类最多 3 条并合并溢出、`--no-notify` 透传和空白邮箱拒绝；还覆盖当前学期推导、路径清洗/root 边界、folder tree、同名 source id、独立无认证下载 client、流式下载、SHA256/size、500 MiB 限制、3 次重试、旧版保留、原子替换、单文件失败隔离及归档 CLI 参数透传。

数据库 Repository 集成测试默认跳过，避免误写正式库。使用专用测试库时运行：

```bash
createdb sjtu_learning_assistant_test
env SJTU_DATABASE_URL='postgresql+psycopg:///sjtu_learning_assistant_test?host=/tmp&connect_timeout=5' .venv/bin/alembic upgrade head
env SJTU_TEST_DATABASE_URL='postgresql+psycopg:///sjtu_learning_assistant_test?host=/tmp&connect_timeout=5' python3 -m unittest discover -s tests -v
dropdb sjtu_learning_assistant_test
```

该测试会验证课程、公告、作业、课程文件/文件夹/模块、邮件与统一 Item 的真实外键，以及重复执行不会产生重复数据。

## 版本管理

- `main`：只保存已经验收通过的阶段。
- 每个阶段使用独立的 `aime/<timestamp>-<stage>` 分支。
- 每个阶段通过测试后单独提交。
- 未经明确要求，不推送远程仓库。
- `.venv`、缓存、密码、Token、`.env`、SQL 导出和数据库 Dump 永不提交。

## Phase 5A：本地学习仪表盘

Phase 5A 新增 `dashboard-web/` React + Vite + Tailwind 单页应用，以及仅绑定回环地址的 FastAPI 服务。界面包含“概览、截止事项、消息、课程资料”四个顶层视图；所有页面时间均按 `Asia/Shanghai` 显示，截止事项提供 24 小时 / 7 天 / 14 天筛选，消息提供全部 / 邮件 / 公告筛选，课程资料支持按学期、课程与下载状态筛选。已下载文件可安全打开，任意学期的未下载文件均可单文件下载。

### 安装与构建

```bash
cd sjtu-learning-assistant-phase1a
.venv/bin/python -m pip install -r requirements.txt
cd dashboard-web
npm install
npm run test
npm run lint
npm run build
cd ..
```

前端生产文件构建到 `dashboard-web/dist/`，由 FastAPI 同源托管；运行时不加载 CDN 或远程字体。五个导航 / 操作图标已经保存到 `dashboard-web/src/assets/icons/` 并通过模块 `import` 打包。

### 前台启动（开发验证）

```bash
.venv/bin/python -m uvicorn dashboard_api:app \
  --host 127.0.0.1 --port 17655 --no-access-log
```

另开终端访问 `http://127.0.0.1:17655/`。服务不会监听局域网地址。若课程归档目录不是默认的 `~/Documents/SJTU Study`，启动前可设置普通配置 `SJTU_ARCHIVE_ROOT`；不要把数据库 URL、Canvas Token 或邮箱密码写入命令、plist 或仓库。

Dashboard 的所有 POST 请求都要求同源 `Origin` 和进程级 CSRF token；服务同时限制 Host 为 `127.0.0.1` / `localhost`，并返回 CSP、`frame-deny`、`nosniff` 与 `no-referrer` 安全响应头。API DTO 不返回 `raw_data`、邮箱地址、Token、密码或数据库 URL。

### Dashboard LaunchAgent

独立服务标签为 `com.sjtu.learningassistant.dashboard`，默认端口 `17655`，日志仍位于 `~/Library/Application Support/sjtu-learning-assistant/logs/`。plist 只包含解释器、工作目录、回环监听参数和非敏感环境变量。

```bash
# 安装并常驻（RunAtLoad + KeepAlive）；端口被占用时会明确拒绝
.venv/bin/python dashboard_control.py install

# 查看状态 / 立即重启 / 打开本地页面
.venv/bin/python dashboard_control.py status
.venv/bin/python dashboard_control.py kickstart
.venv/bin/python dashboard_control.py open

# 停止并卸载，不删除日志
.venv/bin/python dashboard_control.py uninstall
```

Dashboard 内“立即同步”会优先 `kickstart` 已安装的同步 LaunchAgent；未安装时会异步启动既有 `launchd_control.py run-once --canvas-only --no-download`，继续复用 `sync_runner` 的进程锁，HTTP 请求会立即返回 `202 accepted`，前端随后轮询同步状态。单文件下载复用 `ArchiveService.download_file_by_source_id`，不受当前学期限制。

### Phase 5A 验证

```bash
# 全部 Python 单元测试；不联网、不打开文件、不写 Documents
.venv/bin/python -m unittest discover -s tests -v

# 前端测试、静态检查与生产构建
cd dashboard-web
npm run test
npm run lint
npm run build
cd ..

git diff --check
```

本轮不自动安装 LaunchAgent、不启动长期服务，也不自动打开浏览器。
