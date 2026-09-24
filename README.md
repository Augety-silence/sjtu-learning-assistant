# SJTU Learning Assistant

## 数据库架构（桌面版）

桌面 App 默认使用单用户 SQLite，不需要本地数据库服务或端口：

```text
~/Library/Application Support/SJTU Learning Assistant/data/app.db
```

首次打开 SQLite 时通过 SQLAlchemy metadata 创建当前 schema，并在
`desktop_schema_version` 记录版本 `0016`。每条 SQLite 连接都会启用
`foreign_keys=ON`、WAL 和 5000 ms `busy_timeout`。`sync_data_to_db.py`、
`sync_runner.py`、LaunchAgent 与 Dashboard 都通过同一个默认 URL 写入这个文件；
LaunchAgent 无需数据库参数，也不会保存秘密。

只有以下情况使用 PostgreSQL：

- 当前进程显式设置 `SJTU_DATABASE_URL=postgresql+psycopg://...`；
- 数据库 CLI 使用 `use-postgres-status` / `use-postgres-upgrade`；
- 使用 `import-postgres` 将旧数据一次性导入 SQLite。

PostgreSQL URL 仍只保存在 macOS Keychain（或由进程环境显式注入），不会写入
SQLite、plist 或日志。SQLite 采用 metadata bootstrap，历史 Alembic `0001`–`0016`
保持不变且只继续用于 PostgreSQL。

### 初始化与安全导入

```bash
# 创建/检查默认 SQLite schema；若空库且 Keychain 有旧 PostgreSQL URL，会一次性导入
python3 db_manage.py upgrade

# 只建 SQLite schema，明确跳过自动导入
python3 db_manage.py upgrade --skip-import

# 之后显式发起一次旧 PostgreSQL -> 空 SQLite 导入
python3 db_manage.py import-postgres

# 查看默认 SQLite
python3 db_manage.py status
```

导入按外键依赖顺序复制全部业务表，保留 ID、时间与 JSON，并输出每张表的精确行数。
仅允许目标 SQLite 全新且所有业务表为空时导入；任一表非空会拒绝，任一步失败会回滚
整个导入事务。数据库本来不保存 Canvas Token、邮箱密码或其他凭据，因此这些内容不会迁移。

### SQLite 自检、快照与恢复

每次打开文件型 SQLite 前会执行 `PRAGMA quick_check`。应用每天最多执行一次完整
`integrity_check`，并通过 SQLite Backup API 维护 `app.db.backup-1` 至
`app.db.backup-3` 三份轮转快照；数据库 schema 发生升级前还会强制创建快照。

若启动时确认数据库损坏，程序会保留带 UTC 时间戳的 `app.db.corrupt.*` 隔离副本，
再依次验证并恢复最近可用快照。没有有效快照时会新建空库并提示重新同步，绝不会静默
删除损坏文件。数据库忙、权限异常等临时故障不会被当作损坏处理。

### PostgreSQL 兼容与回滚

```bash
# 安全保存/更新旧 PostgreSQL URL
python3 db_manage.py configure

# 显式检查旧 PostgreSQL
python3 db_manage.py use-postgres-status

# 显式对旧 PostgreSQL 运行未修改的 Alembic 历史迁移
python3 db_manage.py use-postgres-upgrade

# 临时让任意现有入口回到 PostgreSQL（不要把 URL 写进 plist 或仓库）
env SJTU_DATABASE_URL='postgresql+psycopg://...' python3 sync_data_to_db.py --canvas-only

# 首次运行但明确不导入旧 PostgreSQL
python3 sync_data_to_db.py --canvas-only --skip-import
```

回滚到 PostgreSQL 不会删除或覆盖 SQLite 文件；去掉环境变量后程序恢复使用默认 SQLite。
`psycopg` 保留为 PostgreSQL 导入/回滚的可选运行依赖；需要这些功能时另行安装：

```bash
python3 -m pip install -r requirements-postgres.txt
```

当前完成阶段：

- **Phase 1A**：通过 IMAP SSL 只读访问交大邮箱。
- **Phase 1B**：获取 Canvas active courses。
- **Phase 1C**：逐门课程获取 Canvas 公告和作业。
- **Phase 2A**：建立 SQLAlchemy 持久化基础（SQLite 默认、PostgreSQL 兼容）。
- **Phase 2B**：将 Canvas 公告、作业和邮件增量写入数据库，并生成统一事项。
- **Phase 2C**：同步 Canvas 课程文件、文件夹、模块与模块项元数据。
- **Phase 3A**：增加单实例后台运行器、JSONL 审计日志、有限重试与 macOS LaunchAgent 管理。
- **Phase 3B**：增加安全的 macOS 系统通知、首次同步基线、通知幂等账本与聚合限流。
- **Phase 4A**：按最近一次 Canvas active 课程自动下载和分类归档文件，保留旧版本并记录校验与下载状态。

系统通知覆盖 Canvas 新公告、新作业、新文件，以及 24 小时内截止且尚未提交的作业。Phase 4A/5A 已实现本地文件归档及桌面设置界面。

## 数据表如何关联

系统同时使用两类标识：

- `id`：数据库内部主键，用于表间外键关联。
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
│   ├── desktop_database.py
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
├── requirements-postgres.txt
└── tests/
```

## 安装或更新依赖

```bash
cd sjtu-learning-assistant-phase1a
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

Phase 4A 不新增 Python 依赖。更新已有数据库后先运行 `python3 db_manage.py upgrade`；SQLite 使用 metadata bootstrap，显式 PostgreSQL 使用 Alembic `0006`。

## 数据库管理

默认 SQLite 初始化、状态检查、一次性 PostgreSQL 导入及回滚命令见本文开头的
“数据库架构（桌面版）”。远程部署只允许通过 Secrets Manager 显式注入
`SJTU_DATABASE_URL`，不要提交 `.env`。

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

## Canvas 文件归档与本机设置

非敏感设置保存在：

```text
~/Library/Application Support/SJTU Learning Assistant/settings.json
```

文件采用 `0600` 权限的原子 JSON 写入，只允许 `archive_root`、
`auto_download_current_term`、`organize_by_category` 三个字段；Token、密码、邮箱和
数据库 URL 不会写入该文件。默认值分别为 `~/Documents/SJTU Study`、`true`、`true`。
设置优先级为 CLI 显式参数 > 环境变量 > 设置文件 > 内置默认值。可用环境变量为
`SJTU_ARCHIVE_ROOT`、`SJTU_AUTO_DOWNLOAD_CURRENT_TERM`、
`SJTU_ORGANIZE_BY_CATEGORY`。

设置页可选择归档目录、切换本学期自动下载和按类别整理，并可立即整理已有文件。
所有操作只通过 pywebview Bridge 完成，不启动 HTTP server。目录选择取消时不会修改设置。

开启分类整理时，真实目录结构为：

```text
~/Documents/SJTU Study/{学期}/{课程}/{分类}/{Canvas 原始文件夹链}/{文件}
```

分类固定为“课程作业 / 课件 / 补充资料 / 其他”。分类规则只有一份，资料树和物理归档
共同使用，优先级为 Canvas 模块名及模块项标题 > Canvas folder 路径 > 文件名；分类结果
不写入数据库。关闭 `organize_by_category` 后，新下载仍使用旧结构
`{学期}/{课程}/{Canvas 原始文件夹链}/{文件}`。

默认同步会下载**本轮 Canvas API 返回的 active 课程**，不依赖学期名称精确匹配，因此
本地化或不规范 term_name 不会造成漏下；历史课程仍只能在资料页逐个按需下载。
`--no-download` 会显式关闭本次下载，`--archive-root` 与
`--no-organize-by-category` 会覆盖 UI 设置。desktop app、同步 CLI 和 launchd 均读取
同一设置文件；后台同步默认同样会下载 active 课程。

```bash
# 默认：同步元数据并自动下载本轮 active 课程
python3 sync_data_to_db.py --canvas-only

# 只同步元数据
python3 sync_data_to_db.py --canvas-only --no-download

# 显式覆盖归档目录并维持旧目录结构
python3 sync_data_to_db.py --canvas-only \
  --archive-root "/path/to/SJTU Study" \
  --no-organize-by-category
```

已有已下载文件的整理是幂等操作，仅处理最近一次成功 Canvas 同步所标记的 active 课程。
执行前会核对数据库已有的 size/SHA-256，拒绝父目录 symlink 和越界目标；同卷原子移动，
跨卷先在目标目录复制到临时文件、`fsync` 后 `os.replace`，数据库更新成功后才删除源文件。
目标同内容时只更新数据库并移除重复源文件，冲突内容使用 source_id/版本名保留；中断后
再次执行会从已落盘目标恢复数据库路径。建议先备份，再启动桌面端，在“设置”中点击
“立即整理现有文件”；此操作不会下载历史课程。

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
- 正常通知发送前只查询已有 `event_key`，不插入 `pending`；发送成功后才原子写入 `sent`。发送失败不留永久占位，下一轮可重试；并发唯一键冲突通过当前数据库方言的 Upsert 安全收敛。
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

## Phase 5A：无端口 macOS 桌面应用

Phase 5A 使用 `pywebview` 打开本地 `dashboard-web/dist/index.html`，不启动 HTTP 服务、不监听端口，也不自动打开浏览器。React 仅通过 `window.pywebview.api.invoke(action, payload)` 调用白名单 Bridge；Bridge 只返回脱敏业务 DTO，异常不会透出凭据、数据库 URL、归档绝对路径或 Python 堆栈。

界面包含“概览、截止事项、消息、课程资料、设置”五个顶层视图。资料页按“学期 → 课程 → 课程作业/课件/补充资料/其他 → Canvas 目录 → 文件”动态构树，分类不写回数据库，判定优先级为模块、目录、文件名，并包含去重和目录循环保护。Finder 风格双栏支持面包屑、搜索、分类/下载状态筛选、下载、打开和在 Finder 中显示。

### 安装、构建与启动

```bash
cd sjtu-learning-assistant-phase1a
.venv/bin/python -m pip install -r requirements.txt
cd dashboard-web
npm ci
npm run test
npm run lint
npm run build
cd ..
.venv/bin/python desktop_app.py
```

`desktop_app.py` 要求前端已构建。Vite 使用相对资源基址 `./`，运行时不加载 CDN 或远程字体。桌面 App 启动后在进程内每 15 分钟请求同步；手动和定时同步共享防重入锁，已有同步运行时不会重复启动。退出窗口会停止调度线程。

设置页只显示非敏感的归档与同步偏好，加载时不会读取或探测任何凭据。Canvas Token 和邮箱密码仅在实际同步时按需从 macOS Keychain 读取；邮箱账号来自 `SJTU_EMAIL`。课程归档目录默认是 `~/Documents/SJTU Study`，可在设置页选择，也可通过 `SJTU_ARCHIVE_ROOT` 显式覆盖。不要把数据库 URL、Canvas Token、邮箱或密码写入 settings.json、命令、plist 或仓库。

资料操作会重新按数据库 `source_id` 查询文件，并校验解析后的本地路径位于配置的归档根目录内；打开和 Reveal 均使用固定参数数组调用 macOS `/usr/bin/open`。外部链接只允许无内嵌账号密码的 HTTPS URL。

### AI Chat 本地附件

AI Chat 输入器的第二行可选择本地附件。选择后应用只读取所选普通文件，并按 SHA-256
去重复制到归档目录下的 `.ai_attachments/objects/`；不会移动、改名或删除用户选择的
原始文件。符号链接、越界受控路径、复制期间变化及超过 2 GiB 的输入都会被拒绝。
支持的文本/代码格式会生成有界正文派生、摘要和标签，聊天消息仅持久化附件 ID 关联；
Bridge DTO 和 Agent 工具结果不包含本机绝对路径。

Agent 默认先检索附件名称、摘要和标签，只在需要细节时通过附件 ID 调用
`read_ai_attachment_text`；单次读取最多 12000 字符，本轮消息的工具实例只能读取本轮
附件。云端归档会在上传后重新获取远端元数据，并临时下载计算 SHA-256；大小和哈希
均匹配后才记录云端副本。默认保留 `.ai_attachments` 内的应用受控副本；只有用户勾选
“归档后释放本地空间”并完成二次确认时，才删除受控副本并标记 `cloud_only`。用户原始
文件不参与删除。读取云端正文时同样临时下载并校验；显式“在 Finder 中显示”会先恢复到
受控目录，再以固定参数 `/usr/bin/open -R` 显示。

对应 PostgreSQL 迁移为 `0015_add_ai_managed_files.py` 与
`0016_link_ai_chat_attachments.py`；已有迁移文件不应改写语义。

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

## 开源发布、隐私与桌面构建

### 许可证与第三方组件

本项目由周济睿按 [MIT License](LICENSE) 开源。第三方运行时、开发/构建及可选迁移依赖的许可证与上游来源见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。新增或升级依赖后必须运行：

```bash
.venv/bin/python scripts/check_licenses.py
node scripts/check_licenses.mjs
```

许可证检查会拒绝 GPL/AGPL 强 copyleft 运行时依赖。唯一 GPL 构建例外是采用 `GPL-2.0-or-later WITH Bootloader-exception` 的 PyInstaller；`psycopg`/`psycopg-binary`（LGPL-3.0-only）严格隔离在 `requirements-postgres.txt`，只供明确执行的 PostgreSQL 迁移、检查或回滚使用，不进入默认 SQLite 桌面包。

### 隐私与本地数据位置

应用不会把学习数据发送到项目维护者。它只在用户主动配置后访问 Canvas/交大邮箱等服务，并把数据保存在本机：

- SQLite：`~/Library/Application Support/SJTU Learning Assistant/data/app.db`（旁侧可能存在 WAL/SHM 文件）；
- 同步运行状态与日志：`~/Library/Application Support/sjtu-learning-assistant/run/` 和 `.../logs/`；
- 下载资料：默认 `~/Documents/SJTU Study/`，可用 `SJTU_ARCHIVE_ROOT` 修改；
- Canvas Token、邮箱密码及可选 PostgreSQL URL：macOS Keychain；仓库中没有 Keychain 凭据文件；
- 非敏感本机设置：`~/Library/Application Support/SJTU Learning Assistant/settings.json`；
- 可选进程覆盖：`SJTU_ARCHIVE_ROOT`、`SJTU_AUTO_DOWNLOAD_CURRENT_TERM`、`SJTU_ORGANIZE_BY_CATEGORY`；邮箱账号仍仅来自 `SJTU_EMAIL` 环境变量。

删除应用本身不会自动删除上述用户数据。备份、迁移、清理前应先退出应用；不要把数据库、WAL/SHM、日志、下载资料、`.env`、plist 或 Keychain 导出提交到 Git。

### GitHub 公开前检查

公开仓库前，维护者应：

1. 运行完整测试、前端构建、两项许可证检查、`scripts/scan_secrets.py` 和 `git diff --check`；
2. 检查所有已跟踪及待提交文件，确认没有真实邮箱、Token、密码、数据库 URL、个人绝对路径、数据库、日志和课程下载资料；
3. 确认 `src/assets/icons` 不含来源不明 SVG，界面图标只来自已声明的 `lucide-react`；
4. 在 GitHub **Settings → Code security and analysis** 启用 Secret scanning、Push protection 与 Private vulnerability reporting；
5. 检查仓库历史。如历史曾包含秘密，先轮换凭据，再按团队流程清理历史；仅从当前文件删除并不足够；
6. 确认默认分支保护和 CI 已启用，并按 [SECURITY.md](SECURITY.md) 处理漏洞报告。

### 开发与验证

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
npm --prefix dashboard-web ci
npm --prefix dashboard-web run test
npm --prefix dashboard-web run lint
npm --prefix dashboard-web run build
python -m unittest discover -s tests -v
python scripts/check_licenses.py
node scripts/check_licenses.mjs
python scripts/scan_secrets.py
git diff --check
```

默认运行时依赖在 `requirements.txt`；测试和打包工具在 `requirements-dev.txt`；PostgreSQL 迁移工具在 `requirements-postgres.txt`。开发和默认桌面构建均不要安装可选 PostgreSQL 驱动。

### 构建 macOS 应用与 DMG

```bash
scripts/build_macos_app.sh
scripts/build_macos_dmg.sh
```

第一个脚本会创建/复用 `.venv`、安装开发依赖，先从保留的 AI 源图生成透明 1024 PNG、UI Logo、`.iconset` 与 `app.icns`，再执行前端 test/lint/build、Python 单测、许可证和秘密扫描，随后用 `packaging/desktop.spec` 输出：

```text
dist/SJTU Learning Assistant.app
```

第二个脚本会将已验证的 `.app` 与 Applications 快捷方式封装为压缩 DMG，并同时生成 SHA-256 校验文件：

```text
dist/SJTU-Learning-Assistant-<version>-macOS-<arch>.dmg
dist/SJTU-Learning-Assistant-<version>-macOS-<arch>.dmg.sha256
```

品牌源图保存在 `packaging/assets/app-icon-source.jpg` 与 `dashboard-web/src/assets/app-logo-source.jpg`，生成物分别为 `packaging/assets/app-icon.png`、`packaging/app.icns` 和 `dashboard-web/src/assets/app-logo.png`，可通过 `scripts/generate_macos_icon.py` 重现。

Bundle identifier 为 `io.github.sjtu-learning-assistant`，版本来自 `sjtu_learning_assistant.__version__`。打包只包含本地前端、许可证和默认运行依赖，不包含 `psycopg`，也不包含 FastAPI/Uvicorn 或监听端口的服务。`MACOS_CODESIGN_IDENTITY` 与 `MACOS_NOTARY_PROFILE` 仅预留给未来经审核的发布流程；当前脚本即使检测到变量也不会执行 `codesign` 或 `notarytool`。

当前产物**未签名、未公证**。首次打开时 Gatekeeper 可能阻止运行。请仅对自己从可信源码构建、并已核对校验和的产物，在 Finder 中按住 Control 点击应用并选择“打开”，再确认；不要建议用户全局关闭 Gatekeeper。正式公开分发前应增加 Developer ID 签名、公证和 stapling 流程。

推送与 `sjtu_learning_assistant.__version__` 完全一致的 `v*.*.*` tag 会触发 Release workflow，在 Apple Silicon runner 上重新执行测试与检查、构建并验证 DMG 和 SHA-256，然后创建 GitHub Pre-release。该自动化当前只发布未签名、未公证的 arm64 产物；完成 Developer ID 签名与公证后再移除 Pre-release 标记。

### 数据迁移与回滚

- 升级/初始化默认 SQLite：`python db_manage.py upgrade`；只建库并跳过旧 PostgreSQL 自动导入：`python db_manage.py upgrade --skip-import`。
- 一次性导入旧库：先单独安装 `requirements-postgres.txt`，再执行 `python db_manage.py import-postgres`；仅允许导入全新空 SQLite，失败时整笔事务回滚。
- 回滚到旧 PostgreSQL：临时注入 `SJTU_DATABASE_URL` 后运行现有命令；不要把 URL 写入仓库、plist 或日志。移除环境变量即可恢复默认 SQLite，SQLite 文件不会被删除。
- 应用代码回滚前先退出应用并备份整个 SQLite 数据库及同目录 WAL/SHM 文件。若新版本写入了旧版本不认识的 schema，不要直接用旧二进制打开；应恢复升级前备份，或使用与目标版本匹配的迁移工具。

本节只描述软件工程流程，不会自动迁移真实数据、安装 LaunchAgent、启动窗口、签名或公证。
