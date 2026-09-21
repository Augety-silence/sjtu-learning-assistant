# SJTU Learning Assistant

当前已完成三个独立的数据连通性验证：

- **Phase 1A**：通过 IMAP SSL 只读访问交大邮箱，并显示 INBOX 最近 10 封邮件。
- **Phase 1B**：通过 Canvas REST API 获取当前账号可访问的 active courses。
- **Phase 1C**：逐门课程获取 Canvas 公告和作业，显示发布时间、截止时间、分值、提交状态与原始链接。

目前仍不包含数据库、通知、文件归档、附件下载或 UI。

## 当前目录

```text
sjtu-learning-assistant-phase1a/
├── .gitignore
├── README.md
├── requirements.txt
├── test_canvas.py
├── test_mail.py
└── tests/
    └── test_canvas_client.py
```

## 环境与依赖

- macOS
- Python 3.10 或更高版本
- `httpx`：Canvas REST API 请求
- `keyring`：访问 macOS Keychain

## 安装或更新依赖

```bash
cd "/Users/augety/Desktop/Academic/2609-2701/文本分析与大模型/Project/sjtu-learning-assistant-phase1a"
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

## Phase 1C：Canvas 公告与作业

Phase 1B 已保存 Token，因此直接运行：

```bash
source .venv/bin/activate
python3 test_canvas.py
```

程序会：

1. 从 macOS Keychain 读取 Canvas Access Token。
2. 获取所有 active courses，并正确处理分页。
3. 对每门课程请求 `/api/v1/announcements`。
4. 对每门课程请求 `/api/v1/courses/:course_id/assignments`。
5. 将 Canvas 时间统一转换为 Mac 当前本地时区。
6. 某门课程的单个接口失败时继续读取其他接口和课程，并在最后返回部分完成状态。

预期输出结构：

```text
正在读取 active courses、announcements 和 assignments …
已从 macOS Keychain 读取 Canvas Access Token。

Phase 1C 验证完成，共读取 7 门 active courses。

========================================================================
1. 文本分析与大模型（ID: 95040）
   公告 2 条；作业 3 项

   [公告]
   1. 第一周课程安排
      发布时间：2026-09-21 09:00 CST
      链接：https://oc.sjtu.edu.cn/...

   [作业]
   1. Homework 1
      截止时间：2026-09-28 23:59 CST
      分值：100.0
      提交状态：unsubmitted
      链接：https://oc.sjtu.edu.cn/...
```

如只想复查 Phase 1B 课程列表：

```bash
python3 test_canvas.py --courses-only
```

删除 Keychain 中保存的 Token：

```bash
python3 test_canvas.py --forget-token
```

## 最简单的验收方法

1. 确认仍能识别 7 门 active courses。
2. 在至少一门课程中对照 Canvas 页面核对公告标题。
3. 对照一项作业核对名称与截止时间。
4. 确认中文正常、时间显示为 Mac 本地时区。
5. 若某门课没有公告或作业，显示“暂无可见公告/作业”，而不是报错退出。

## Phase 1A：交大邮箱 IMAP

```bash
python3 test_mail.py
```

脚本通过 `mail.sjtu.edu.cn:993` 连接，并以只读方式显示最近 10 封邮件。

## 自动化测试

```bash
python3 -m unittest discover -s tests -v
```

测试不需要真实 Token，也不会访问真实 Canvas，覆盖：

- 课程分页与去重
- 公告课程上下文参数
- 作业路径与 submission 参数
- 单个课程接口失败后的继续处理
- 未授权错误
- 跨站分页 Token 泄漏防护
- HTTPS 地址校验

## 版本管理

- `main`：只保存已经由你验收通过的阶段。
- 每个新阶段使用独立的 `aime/<timestamp>-<stage>` 分支。
- 每个阶段通过自动化测试后单独提交。
- 未经你明确要求，不推送远程仓库。
- `.venv`、缓存、密码与 Token 永不提交。
