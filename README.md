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

系统通知覆盖 Canvas 新公告、新作业、新文件，以及 24 小时内截止且尚未提交的作业。文件实际下载归档和 UI 尚未实现。

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
│       └── 0005_add_notification_events.py
├── sjtu_learning_assistant/
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
cd "/Users/augety/Desktop/Academic/2609-2701/文本分析与大模型/Project/sjtu-learning-assistant-phase1a"
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

Phase 3B 不新增 Python 依赖；通知直接使用 macOS 自带的 `/usr/bin/osascript`。更新已有数据库后必须先运行 `python3 db_manage.py upgrade` 应用 Alembic `0005`。

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

测试不需要真实凭证，覆盖 Canvas API、安全分页、ETag 304、IMAP UID 游标、数据库 URL 与密码脱敏、后台运行器，以及通知命令安全性、通知专属首次基线、`items.id` 游标、发送失败跨轮重试、通知数据库异常隔离、跨轮幂等、每类最多 3 条并合并溢出、`--no-notify` 透传和空白邮箱拒绝。

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
