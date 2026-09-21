# SJTU Learning Assistant

当前完成阶段：

- **Phase 1A**：通过 IMAP SSL 只读访问交大邮箱。
- **Phase 1B**：获取 Canvas active courses。
- **Phase 1C**：逐门课程获取 Canvas 公告和作业。
- **Phase 2A**：建立 PostgreSQL、SQLAlchemy 与 Alembic 持久化基础。
- **Phase 2B**：将 Canvas 公告、作业和邮件增量写入 PostgreSQL，并生成统一事项。
- **Phase 2C**：同步 Canvas 课程文件、文件夹、模块与模块项元数据。

目前尚未加入定时器、系统通知、文件实际下载归档或 UI。

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

sync_state(source, resource)  每类资源唯一同步游标
sync_runs                     每次同步的审计记录
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
│       └── 0004_track_canvas_item_activity.py
├── sjtu_learning_assistant/
│   ├── database.py
│   ├── mail_client.py
│   ├── models.py
│   └── repository.py
├── sync_courses_to_db.py
├── sync_data_to_db.py
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

首次邮箱同步默认导入最近 100 封邮件，可调整：

```bash
python3 sync_data_to_db.py --mail-only --initial-mail-limit 500
```

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
- `alembic_version`：数据库结构版本

## 自动化测试

```bash
python3 -m unittest discover -s tests -v
```

测试不需要真实凭证，覆盖 Canvas API、安全分页、ETag 304、IMAP UID 游标、数据库 URL 与密码脱敏。

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
