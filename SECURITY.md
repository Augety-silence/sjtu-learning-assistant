# 安全政策

## 支持范围

当前仅维护默认分支的最新版本。发布版本出现安全修复时，会在发行说明中标注受影响范围。

## 私下报告漏洞

请不要在公开 Issue、Discussion、提交信息或日志中披露未修复漏洞、凭据或个人数据。

请进入本仓库的 **Security** 页面，选择 **Advisories → New draft security advisory**（“Report a vulnerability”）提交私密报告。若仓库尚未启用 Private vulnerability reporting，请由仓库维护者先在 **Settings → Security → Code security and analysis** 中启用；在启用前不要公开披露。

报告建议包含：

- 受影响版本和环境；
- 可复现步骤或最小复现；
- 影响与攻击前提；
- 建议修复（如有）；
- 已采取的保密措施。

维护者会尽快确认收到报告，在完成评估后通过 Security Advisory 协作、分配 CVE（如适用）并协调披露。请勿发送真实 Canvas Token、邮箱密码、数据库 URL、Keychain 导出或含个人数据的数据库。

## 安全边界

桌面版默认只访问本地 SQLite、macOS Keychain、用户选择/配置的归档目录以及用户主动配置的 SJTU/Canvas/邮箱公开服务端点。应用不启动 HTTP 服务，也不应监听网络端口。发现偏离此边界的行为时，请按上述私密渠道报告。
