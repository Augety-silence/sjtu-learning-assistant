# SJTU Learning Assistant

当前已完成两个独立的数据连通性验证：

- **Phase 1A**：通过 IMAP SSL 只读访问交大邮箱，并显示 INBOX 最近 10 封邮件。
- **Phase 1B**：通过 Canvas REST API 获取当前账号可访问的 active courses。

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

在 VSCode 中打开本目录，然后在集成终端运行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
```

如果 Phase 1A 已经创建过 `.venv`，只需：

```bash
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

## Phase 1B：Canvas API

### 1. 创建个人 Access Token

登录 `https://oc.sjtu.edu.cn`，在个人账户设置中寻找 **Approved Integrations / 批准的集成** 或 **New Access Token / 新建访问令牌**。创建后只在本机保存 Token，不要粘贴到聊天、代码、`.env`、Git 提交或截图中。

如果页面没有创建 Token 的入口，说明该能力可能被学校管理员关闭；此时先停止，不要改用浏览器抓取登录信息。

### 2. 运行

```bash
python3 test_canvas.py
```

按提示输入 Canvas Access Token。输入过程不会回显。程序只在 API 验证成功后将 Token 保存到 macOS Keychain。

不使用 Keychain：

```bash
python3 test_canvas.py --no-keychain
```

删除已保存的 Token：

```bash
python3 test_canvas.py --forget-token
```

### 3. 预期输出

```text
正在请求 https://oc.sjtu.edu.cn/api/v1/courses …
Canvas Access Token 已安全保存到 macOS Keychain。

API 验证成功，获取到 6 门 active courses：

1. 文本分析与大模型
   ID：12345
   课程代码：COURSE-001
   学期：2026-2027-1
```

### 4. 最简单的验收方法

1. 确认终端出现“API 验证成功”。
2. 对照 Canvas 首页检查课程名称。
3. 课程数超过 100 时，确认脚本仍能自动读取后续分页。
4. 再运行一次，确认程序从 macOS Keychain 读取 Token。

## Phase 1A：交大邮箱 IMAP

```bash
python3 test_mail.py
```

脚本会安全获取邮箱密码，通过 `mail.sjtu.edu.cn:993` 连接，并以只读方式显示最近 10 封邮件。详细参数可运行：

```bash
python3 test_mail.py --help
```

## 自动化测试

```bash
python3 -m unittest discover -s tests -v
```

测试覆盖 Canvas 分页、课程去重、未授权错误和 HTTPS 地址校验，不需要真实 Token，也不会访问真实 Canvas。

## 版本管理

- `main`：保存已经验收通过的阶段。
- `aime/<timestamp>-canvas-api-validation`：Phase 1B 开发分支。
- 每个阶段通过测试后单独提交，不提交 `.venv`、缓存、密码或 Token。

当前 Phase 1A 已作为独立基线提交；Phase 1B 的代码在开发分支中提交，未经明确要求不会推送到远程仓库。
