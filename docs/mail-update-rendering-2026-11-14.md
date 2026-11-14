# 更新检查与邮件图片布局修复（2026-11-14）

## 证据与判断

附件 `sjtu-learning-assistant-debug-20261114_201158.zip` 为 schema 2，运行版本 1.8.9、Darwin arm64、frozen。20:08:32 与 20:09:18 更新的 release_metadata 阶段均为 HTTP 403；不能仅由状态码区分 API 限流、网络限制或 IP 拒绝。20:01:06 邮箱同步已成功导入 100 封，不将更早的 TLS 失败当作本次仍同步失败。截图显示的是邮件正文布局表格被加网格边框、logo 不见，不是邮箱认证失败。

## 修改依据与触发条件

| 文件／位置 | 触发与影响 | 处理 |
| --- | --- | --- |
| update_service.py / fetch_release、_fetch_web_release、URL 验证 | API 403/429 后检查直接失败 | 官方 Latest 受控回退，逐跳 HTTPS、固定仓库、SemVer、2 MiB 上限、canonical 与 Latest 标识。无可信资产信息时不造下载计划；其他 API 错误及 TLS 不回退 |
| text_content.py / _safe_attributes、_presentation_attributes | 源图 URL、align、跨列和宽度被丢弃 | 保留有界数字／枚举，不执行或保留任意 CSS、脚本、srcset、外部 class。资源 URL 仅作后端来源验证，前端不直接使用 |
| dashboard_service.py / message_detail、message_resource | 邮件远程图 ID 存在但 resources 为空，来源已丢失 | 重复清洗后仍能重建受控来源；资源 ID 必须出现在该邮件正文中 |
| mail_images.py | 邮件中远程图片可能追踪用户或访问不可信地址 | 只允许 oc.sjtu.edu.cn 与 s3.jcloud.sjtu.edu.cn；逐跳复验，匿名且无 Authorization/Cookie/Referer，TLS 不绕过，5 MiB 限制，拒绝 HTML/SVG |
| mail_client.py、repository.py / 富正文版本与回填 | 已同步旧邮件的图片与布局信息已不可恢复于缓存 | 在下一次既有同步流程中有界只读 BODY.PEEK 回填一次；保持明文正文、已读状态和游标，不重新发送邮件，不修改远端标志，不降级未来版本 |
| messageHtml.ts、MessageDetailContent.tsx、index.css | 布局表格套数据表样式、图片跨邮件状态污染 | 明确布局表语义和零边框；显式数据表保留网格。图片按邮件点击授权，切换立即清除旧图片与授权，CID 不受影响 |

## 验证

- 定向后端：55 passed／54 subtests；另补两个流式限制／TLS 用例随完整验证通过。
- 前端定向：12 passed。完整 Vitest：58 文件／586 passed。
- 完整 pytest：1040 passed／2 skipped／408 subtests passed。
- unittest：967 tests，OK／1 skipped。
- Biome：151 文件通过。TypeScript + Vite：通过；主 chunk 超过 500 kB 为非阻断提示。
- compileall、Python/npm 许可证检查、git diff --check、项目秘密模式扫描：通过。7 个非 UTF-8 文件未作文本扫描，不将其称为全二进制安全审计。
- 首次完整后端运行因沙盒代理环境中的异常 NO_PROXY 值导致 20 个未改动客户端初始化失败；隔离代理变量后重跑通过，没有因此修改产品网络策略。
- 本机真实匿名 GitHub Latest 请求：302 到 v1.8.9、200、149320 字节，og:url 为相对固定仓库路径，Latest 链接存在。回退触发仍使用合成 403/429；不是朋友机器的真实 403 复测。
- 本机浏览器合成 CSS 验证未完成：执行入口拒绝了表达式中的异常输出片段；已归还会话，未改用其他自动化方案。DOM 与布局规则由前端回归覆盖，不描述为浏览器实测通过。

## 使用与边界

本地更新后，点击一次邮箱同步，使旧 HTML 邮件按批次只读回填；打开邮件时用“加载此邮件外部图片”恢复受支持来源的图片。其他域名继续显示拦截说明，应在原邮箱查看。诊断包不包含原始邮件 HTML 或具体 logo URI，因此无法证明截图中原 logo 必定属于这两个受支持主机；需用户在朋友机器实际复测，必要时只提供脱敏图片来源域名，不提供密码或整封私密邮件。

本轮保留 1.8.9，不覆盖远端 Release、标签、安装资产，不推送或新建 PR。Windows 真机、朋友机器网络以及真实 logo 尚未实测；代码与本地包验证不代替这些验收。提交、构建、签名、回退、SHA-256、运行进程和起止 Git 状态在最终代码目录的 DELIVERY.md 记录。
