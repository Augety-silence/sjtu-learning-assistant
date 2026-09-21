# SJTU Learning Assistant — Phase 1A

本阶段只验证一件事：Mac 能否通过 IMAP SSL 连接 `mail.sjtu.edu.cn:993`，并以只读方式列出 INBOX 最近 10 封邮件。

不会创建数据库、通知、UI 或下载附件；不会把密码写入代码、`.env` 或项目文件。密码在认证成功后保存到 macOS Keychain。

## 当前目录

```text
sjtu-learning-assistant-phase1a/
├── .gitignore
├── README.md
├── requirements.txt
└── test_mail.py
```

## 环境与依赖

- macOS
- Python 3.10 或更高版本
- `keyring`：访问 macOS Keychain

## 安装

在 VSCode 中打开本目录，然后在集成终端运行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
```

## 首次运行

推荐不把邮箱地址写入 shell 历史，直接运行：

```bash
python3 test_mail.py
```

按提示输入交大邮箱地址和密码。密码输入过程不会回显。只有 IMAP 登录成功后，密码才会保存到 macOS Keychain；以后运行会自动读取。

也可显式传入邮箱地址：

```bash
python3 test_mail.py --email "your.name@sjtu.edu.cn"
```

如果不希望使用 Keychain：

```bash
python3 test_mail.py --no-keychain
```

删除已保存的 Keychain 密码：

```bash
python3 test_mail.py --email "your.name@sjtu.edu.cn" --forget-password
```

## 预期输出

```text
正在通过 IMAP SSL 连接 mail.sjtu.edu.cn:993 …
密码已安全保存到 macOS Keychain。

连接成功，读取到最近 10 封邮件（从新到旧）：

1. 关于……的通知
   发件人：…… <……@sjtu.edu.cn>
   时间：2026-09-21 14:30 CST
```

## 最简单的验收方法

1. 确认终端出现“连接成功”。
2. 确认最多显示 10 封邮件，并且顺序从新到旧。
3. 对照网页版邮箱检查最近几封的标题。
4. 再运行一次，确认程序提示已从 macOS Keychain 读取密码。

## 常见错误

- **登录失败**：检查邮箱地址与 jAccount 密码；不要把密码发到聊天、代码仓库或截图中。
- **连接超时**：切换校园网或其他可正常访问交大邮箱的网络后重试。
- **Keychain 弹窗**：这是 macOS 对凭证访问的正常授权提示，确认当前运行的是自己的 Python/终端进程后允许。
- **证书错误**：不要关闭 TLS 校验；先检查系统时间、网络代理和 macOS 证书状态。

成功标准只有两个：IMAP SSL 登录成功，且最近 10 封邮件标题可正确显示。通过后再进入 Phase 1B（Canvas API）。
