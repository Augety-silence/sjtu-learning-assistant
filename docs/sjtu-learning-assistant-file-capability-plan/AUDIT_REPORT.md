# 云端代码审计报告

## 1. 审计对象

### SJTU Learning Assistant

- 云端仓库：`SJTU-Learning-Assistant-online/`
- Commit：`e342eeb4390a9617f630a1b370a532d7612643fe`
- 技术栈：Python、SQLAlchemy/Alembic、pywebview、React 19、Vite、Vitest、Biome
- 数据库迁移头：`0017`

### SJTU Canvas Helper

- 云端仓库：`SJTU-Canvas-Helper/`
- Commit：`8f57a2b1c50998b4b2b7e53168824625d7e81b78`
- 技术栈：Rust、Tauri 2、React 18、MUI、react-doc-viewer、react-pdf
- License：MIT，Copyright (c) 2025 Zihong Lin

本次没有访问用户本地 Mac，所有审计、复制和文档生成均在云端工作区完成。

## 2. 目标项目现状

远端目标项目已经具备良好基础：

- Canvas、邮箱、课程资料和作业的本地同步模型
- 本地归档、`.versions` 历史副本和资料分类
- 交大云盘 provider 与基础备份服务
- 受控目录读取、路径规范化、稳定文件名和符号链接防护
- 图片、PDF、文本的基础预览对话框
- AI 附件和 Activity 轨迹
- Python/前端/许可证/秘密扫描/跨平台构建 CI

核心断点：

1. 文件身份分散在 `CourseFile`、`EmailAttachment`、`AIManagedFile`、`CloudFile`。
2. 云盘基础备份缺少完整的不可变版本、恢复计划和重启后任务恢复。
3. 预览器仍是单组件条件分支，缺少注册、资源释放和大文件策略。
4. AI 附件尚未稳定绑定具体资源版本和 SHA-256。
5. DDL/Assignment 尚未形成学生可维护的 `StudyTask` 工作流。

## 3. 提取的核心参考代码

| 能力 | 原始文件 | 参考价值 | 目标项目处理 |
|---|---|---|---|
| 事件监听生命周期 | `src/lib/events.ts` | 类型化 EventMap、异步注册竞态、卸载清理、handler ref | 按思想重写为 `useDesktopEvent`，底层改为 pywebview bridge |
| 渲染器注册 | `src/components/renderers.tsx` | 统一 renderer 列表和格式匹配 | 建立目标项目 `PreviewerRegistry` |
| 预览入口 | `src/components/preview_modal.tsx` | 统一预览弹窗和文档列表 | 适配现有 Dialog、焦点和 motion |
| 预览外壳 | `src/components/renderer_shell.tsx` | 标题、状态、操作和内容区域规范化 | 改编到目标设计 token |
| PDF 预览 | `src/components/pdf_renderer.tsx` | Worker、Blob URL 生命周期、ResizeObserver、缩放 | 评估并引入 react-pdf/pdfjs，改为可见页优先 |
| 下载任务 | `src/components/file_download_table.tsx` | 状态标签、真实进度、批量重试、指数退避 | 状态机与交互参考；任务事实改由数据库提供 |
| 文件 AI UI | `src/components/file_ai_chat_modal.tsx` | 流式消息、Markdown、pending/error | 接入现有三栏 AI Chat，不创建第二套产品 |
| Rust 下载 | `src-tauri/src/client/basic.rs` | 分块响应、进度节流 | 仅算法参考；Python httpx/线程任务重新实现 |
| Rust AI 客户端 | `src-tauri/src/client/ai.rs` | 文件上下文和流式事件组织 | 转成目标 Agent Runtime/LLM client 协议 |
| 文件解析 | `src-tauri/src/client/file_parser/` | PDF/DOCX 解析边界 | Python parser 接口重新实现 |
| 错误模型 | `src-tauri/src/error/mod.rs` | 统一错误分类 | 映射为 Python domain error + bridge error DTO |

## 4. 依赖决策

### 推荐首批新增

- `react-pdf` + 对应 `pdfjs-dist`：仅用于 PDF 预览，采用动态 import。
- Python 文本解析依赖应根据目标已有环境选择；优先复用已有 PDF/文本能力，新增依赖前先做许可证和包体积检查。

### 暂缓引入

- MUI / Ant Design：与目标现有设计体系冲突且增加包体积。
- `@cyntler/react-doc-viewer`：会带来较宽依赖面，首批格式可用自有 registry 实现。
- Rust/Tauri 依赖：目标是 Python/pywebview，不改变桌面运行时。
- DOCX/XLSX/压缩包/Notebook 预览库：根据实际使用频率进入后续迭代。

## 5. 开源使用策略

参考包内原文件按字节保持不变，并带完整 MIT LICENSE。后续实现分三类记录：

1. **思想参考**：记录审计来源和设计映射；通常不复制表达。
2. **代码改编**：保留 MIT License、文件级来源注释、README 致谢和 THIRD_PARTY_NOTICES。
3. **新增依赖**：记录依赖自己的许可证、版本、锁文件和发布包许可证。

不能只在开发 Prompt 中写来源；实际目标仓库一旦落入改编代码，README、THIRD_PARTY_NOTICES、third_party LICENSE 和发布包必须同步落地。

## 6. 已生成执行 Prompt

- Prompt 00：收敛远端现有能力和工程基线
- Prompt 01 / P0：归档、恢复、任务持久化和路径安全
- Prompt 02 / P1：统一文件身份、预览器、文件详情和 AI 文件版本
- Prompt 03 / P2：StudyTask、CourseContext、今日行动和任务关联资料

每个 Prompt 都包含前置审计、数据/API、前端、测试、开源合规、验收和禁止提交边界，可作为后续 Agent 的独立执行输入。
