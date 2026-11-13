# 课件多模态知识编译

本地优先：源文件只读；仅扫描界面选择或 CLI 明确指定的素材目录。
不上传到共享知识库，不调用付费检索或其他提供商。

## 能力实证（2026-11-13）

真实端点 `https://models.sjtu.edu.cn/api/v1`，`/models` 返回
`qwen`、`claw`、`deepseek-chat`、`deepseek-reasoner`、`qwen3.8-27b`。
三次合成图片推理：两个 Qwen 模型均正确识别随机标签、颜色、数字；
`qwen3.8-27b` 双图 SSE 正确保持对应关系（7 个增量事件）。
调用合计上游报告 2204 tokens。没有发送真实课件。
模型别名与能力可能变化，这不是全格式、全课程质量保证。

完整链路另用一份合成 PPTX（图片独有随机标签及数字），经真实 LibreOffice
渲染、Qwen 编译并生成带页图的 Markdown；同内容 PDF 也通过。
共 2 次真实请求、3920 tokens；重跑缓存命中 1 页、新调用 0。
加上能力探针，本轮真实请求合计 5 次、上游报告 6124 tokens。

## 使用

App 知识编译页选择独立素材／输出目录，使用“逐页多模态转 Markdown”。
PPTX 需要本机安装 LibreOffice；PDF 由 pypdfium2 渲染。
每页用整页 JPEG + 提取文字／备注请求 `qwen3.8-27b`。
源目录支持大小写扩展名；单 Markdown 2 MiB、单课件／总素材 100 MiB、
最多 1000 文件／500 页。一次 App 转换最多 100 次页面调用。
需要联网及已启用的 AI 配置；课程页图与文字会发送到该配置端点。

CLI 默认只检查目录与文件数，不调用模型：

```sh
.venv/bin/python -m sjtu_learning_assistant.knowledge_sync \
  --source '/绝对路径/课程素材' --target '/绝对路径/Vault'
```

明确授权后增量运行，可交给现有任务调度器调用，不依赖 App 打开：

```sh
.venv/bin/python -m sjtu_learning_assistant.knowledge_sync \
  --source '/绝对路径/课程素材' --target '/绝对路径/Vault' \
  --allow-api --watch 60 --max-calls 100
```

`max-calls` 是整个 CLI 进程的上限（失败也计数），不是每轮重置。
未自动安装系统定时任务，未对真实资料启动后台调用。
取消在当前 API 请求结束后生效；渲染可取消并终止子进程。

## 存储与恢复

输出 `90_Inbox/Documents/<来源ID>/<内容版本>/document.md`，
`pages/page-0001.md`、`assets/page-0001.jpg` 和 `manifest.json`。
页笔记带 source、source_sha256、page、model、contract 元数据。
原始文字与 AI 解读分开；AI 解读始终标记“需复核”。
课程内容变更生成新版本，保留旧版本；每完成一页写检查点。
重跑复用已完成页，无需再次请求；进程崩溃后锁由 OS 释放。
人工修改、缺失或未登记输出导致该课件停止更新，不覆盖或删除。
原有 3／12 轮建库仍会覆盖自身生成讲义，沿用覆盖确认；
新增页面缓存的保护不等于全 Vault 任意文件均受保护。

## 明确限制

旧 `.ppt`、宏、嵌入对象、外部资源、隐藏页 PPTX：请先安全导出包含全部页的 PDF。
渲染失败、正文截断、页数不一致、超限或接口失败均记录待处理，其他文件继续。
不保证动画、音视频、交互对象、细小文字、复杂图表／公式百分之百还原；
动态内容应额外导出静态关键页。真实课件全库调用需用户选择目录与额度。
Windows 原生依赖、真实课件质量及长时间自动运行仍须平台实测。
