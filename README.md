# SJTU Learning Assistant

当前完成阶段：

- **Phase 1A**：通过 IMAP SSL 只读访问交大邮箱。
- **Phase 1B**：获取 Canvas active courses。
- **Phase 1C**：逐门课程获取 Canvas 公告和作业。
- **Phase 2A**：使用 PostgreSQL、SQLAlchemy 与 Alembic 建立本地持久化基础，并支持未来切换远程 PostgreSQL。

目前尚未把公告、作业、邮件写入数据库，也未加入定时器、通知、文件归档或 UI。

## 当前目录

```text
sjtu-learning-assistant-phase1a/
├── alembic.ini
├── db_manage.py
├── migrations/
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       └── 0001_create_core_tables.py
├── sjtu_learning_assistant/
│   ├── __init__.py
│   ├── database.py
│   ├── models.py
│   └── repository.py
├── sync_courses_to_db.py
├── test_canvas.py
├── test_mail.py
├── requirements.txt
└── tests/
    ├── test_canvas_client.py
    └── test_database.py
```

## 安装或更新依赖

```bash
cd "/Users/augety/Desktop/Academic/2609-2701/文本分析与大模型/Project/sjtu-learning-assistant-phase1a"
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

新增依赖：

- SQLAlchemy 2.x：数据模型和事务
- psycopg 3：PostgreSQL 驱动
- Alembic：数据库结构迁移

## Phase 2A：初始化本地 PostgreSQL

默认连接使用 Postgres.app 的本地 Unix Socket：

```text
postgresql+psycopg:///sjtu_learning_assistant?host=/tmp&connect_timeout=5
```

默认配置不包含密码，也不会写入 `.env`。

创建数据库：

```bash
createdb sjtu_learning_assistant
```

执行结构迁移：

```bash
python3 db_manage.py upgrade
```

检查连接：

```bash
python3 db_manage.py status
```

同步 Canvas 课程到数据库：

```bash
python3 sync_courses_to_db.py
```

第一次运行预期新增 7 门课程；第二次运行预期新增 0 门、更新 7 门，证明 Upsert 可以重复执行且不会产生重复记录。

## 数据表

- `courses`：Canvas 课程
- `announcements`：Canvas 公告
- `assignments`：Canvas 作业
- `emails`：邮件
- `course_files`：课程文件
- `items`：跨 Canvas 与邮件的统一事项
- `sync_state`：每类资源的成功同步游标与状态
- `sync_runs`：每次同步的数量、状态与错误记录
- `alembic_version`：数据库结构版本

所有业务表都使用稳定的远端 `source_id` 或联合唯一约束去重。数据库时间字段使用带时区的 PostgreSQL `timestamptz`。

## 稳健性原则

- Canvas 拉取成功后才进入数据库事务。
- 课程写入与 `sync_state` 更新位于同一事务；写入失败不会推进同步时间。
- PostgreSQL 使用 `pool_pre_ping` 检测失效连接，并设置 5 秒连接超时。
- Upsert 可重复执行，不会重复创建相同课程。
- Alembic 管理结构版本，禁止在业务代码中临时建表。
- 数据库连接地址不会出现在日志中；显示时自动隐藏密码。
- `.env`、数据库导出文件、虚拟环境、缓存和凭证不会提交 Git。

## 切换远程 PostgreSQL

远程数据库仍使用 PostgreSQL 时，业务代码无需修改。远程连接必须启用 `sslmode=require`、`verify-ca` 或 `verify-full`。连接地址优先级是：

1. 部署环境中的 `SJTU_DATABASE_URL` Secret。
2. macOS Keychain 中的数据库连接配置。
3. Postgres.app 本地默认连接。

在 Mac 上安全配置远程地址：

```bash
python3 db_manage.py configure
```

程序会无回显地读取并保存至 macOS Keychain。之后执行：

```bash
python3 db_manage.py upgrade
python3 db_manage.py status
```

删除 Keychain 中的远程配置并恢复本地默认连接：

```bash
python3 db_manage.py forget-config
```

远程部署时应由部署平台的 Secrets Manager 注入 `SJTU_DATABASE_URL`，不要创建或提交 `.env`。

## 数据迁移

迁移到远程 PostgreSQL 时：

```bash
pg_dump --format=custom sjtu_learning_assistant > sjtu_learning_assistant.dump
pg_restore --clean --if-exists --no-owner --dbname=<远程数据库> sjtu_learning_assistant.dump
```

导出文件已被 `.gitignore` 排除，不能提交 Git。正式迁移前应先备份并在测试数据库验证恢复。

## Phase 1 验证命令

```bash
python3 test_mail.py
python3 test_canvas.py
```

## 自动化测试

```bash
python3 -m unittest discover -s tests -v
```

测试不需要真实数据库密码，覆盖 Canvas API、安全分页、数据库 URL 校验和密码脱敏。

## 版本管理

- `main`：只保存已经验收通过的阶段。
- 每个阶段使用独立的 `aime/<timestamp>-<stage>` 分支。
- 每个阶段通过测试后单独提交。
- 未经明确要求，不推送远程仓库。
