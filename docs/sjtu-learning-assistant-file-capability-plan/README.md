# SJTU Learning Assistant 文件能力完善计划（云端审计产物）

本目录用于后续 Agent 在云端连续执行 SJTU Learning Assistant 的文件能力完善工作，包含两个仓库的审计基线、参考代码摘取、开源归属要求、支持缺口和四个可直接执行的代码任务 Prompt。

## 审计基线

- 目标项目：`Augety-silence/sjtu-learning-assistant`
  - 云端审计目录：`SJTU-Learning-Assistant-online/`
  - Commit：`e342eeb4390a9617f630a1b370a532d7612643fe`
  - 说明：该远端提交停留在 schema `0017`，不包含用户本地尚未提交的 `0018` 云盘归档/恢复改动。后续执行必须先检查实际目标分支，不能把本地未提交实现当成远端事实。
- 参考项目：`Okabe-Rintarou-0/SJTU-Canvas-Helper`
  - 源地址：https://github.com/Okabe-Rintarou-0/SJTU-Canvas-Helper
  - Commit：`8f57a2b1c50998b4b2b7e53168824625d7e81b78`
  - License：MIT
  - Copyright：`Copyright (c) 2025 Zihong Lin`

## 目录

- `prompts/00-current-capability-consolidation.md`：现有能力完善与工程基线 Prompt
- `prompts/01-p0-reliability.md`：P0 云盘归档、恢复和任务可靠性 Prompt
- `prompts/02-p1-file-experience.md`：P1 统一文件身份、预览和 AI 文件上下文 Prompt
- `prompts/03-p2-study-task.md`：P2 学习任务与课程上下文 Prompt
- `SUPPORT_GAPS.md`：目标项目需要补充的模型、API、前端与测试支持
- `OPEN_SOURCE_ATTRIBUTION.md`：开源归属和发布检查要求
- `reference/SJTU-Canvas-Helper/`：按原路径保留的最小参考代码包及原始 MIT License

## 参考代码的使用方式

参考包用于阅读、对照和适配，不代表可以直接覆盖目标项目文件。两个项目的运行时不同：

- 参考项目：Tauri + Rust + React + MUI
- 目标项目：Python + pywebview + React + 自有组件/CSS

建议分层处理：

1. **事件生命周期、渲染器注册、任务状态表达**：吸收接口设计和状态机思想，再按目标项目架构实现。
2. **React 组件代码**：只有在确认依赖、样式和可访问性兼容后才改编；保留 MIT 归属。
3. **Rust 下载与解析代码**：作为算法和边界处理参考，在 Python service 中重新实现，不把 Rust/Tauri 依赖引入目标应用。
4. **第三方预览库**：逐个评估包体积、许可证、WebView 兼容性和大文件性能，再决定依赖。

## 开源合规落点

若后续实现复制或改编了参考包中的代码，必须同时完成：

- 将原始 MIT License 保存在目标仓库，例如 `third_party/SJTU-Canvas-Helper/LICENSE`。
- 在 `THIRD_PARTY_NOTICES.md` 记录项目名、作者、源地址、固定 commit、许可证及改编范围。
- 在 `README.md` 的“致谢与开源归属”中说明哪些能力参考或改编自该项目。
- 在发布包中携带完整许可证文本。
- 对新增 npm/Python 依赖运行现有许可证检查。
- 在最终变更清单中列出“参考思想”“改编代码”“新增依赖”三类来源，避免来源模糊。

可直接使用的声明模板见 `OPEN_SOURCE_ATTRIBUTION.md`。
