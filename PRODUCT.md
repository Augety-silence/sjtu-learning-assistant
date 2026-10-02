# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

上海交通大学学生，在桌面端学习助手中查询课程、课程文件、截止事项、消息和本地资料，并通过多轮对话整理学习任务。

## Product Purpose

SJTU Learning Assistant 将已同步到本机的学习数据统一组织成可查询、可追溯的 AI 工作区。成功意味着用户能够快速选择合适的 Agent、完成检索与整理，并看懂工具执行过程而不被开发细节干扰。

## Positioning

产品以本地只读学习数据为边界，通过可切换的学习 Agent 和可核验的 Activity 轨迹，把课程、资料、截止事项和消息查询集中在一个桌面工作台中。

## Operating Context

产品运行于可自由调整尺寸的 macOS 桌面窗口。核心工作流包括选择 Agent、新建或恢复对话、阅读 Markdown/表格/代码结果、调整模型与思考深度，以及按需查看工具调用轨迹。

## Capabilities and Constraints

- 保留现有 Agent Runtime、数据库工具、Bridge 契约和 schema 0014。
- 所有 Agent 工具仅访问本机已同步数据，不执行提交、删除或修改。
- 保留三栏信息架构：Agent 导航、主聊天区域、Activity 执行过程。
- 主界面必须响应窗口宽度和高度变化；窄屏时侧栏需自然折叠为 Drawer。
- 不修改现有业务逻辑、API 数据模型、会话模型或工具执行协议。

## Brand Commitments

- 产品名为 SJTU Learning Assistant，界面内使用 “SJTU / Learning Agent”。
- 使用用户提供的新 AI 机器人 Logo 作为 AI Chat 的主要 Agent 标识。
- 用户明确指定现代 SaaS / AI Workspace 的轻量、克制、高信息密度方向，并以提供的参考图作为视觉关系依据。

## Evidence on Hand

- 设计参考：`../image-afd5ce15.png`
- 当前界面：`../image-183a0cfb.png`
- 新机器人 Logo：`../image-b040bedc.png`
- 当前前端实现：`dashboard-web/src/components/AIChat*.tsx` 与 `dashboard-web/src/index.css`
- 现有前端测试：`dashboard-web/src/test/AIChatView.test.tsx`

## Product Principles

- 中央对话始终是视觉主体。
- 普通用户信息优先，调试细节按需展开。
- 保持高信息密度，但不以卡片、粗边框或高饱和色制造层级。
- 窗口任意缩放后，阅读、输入和导航仍然成立。
- 每次 Agent 行为都应可追溯，但 Activity 不喧宾夺主。

## Accessibility & Inclusion

保留语义化区域、键盘焦点、ARIA 标签、足够的文字对比度，以及在窄窗口下可操作的导航与 Activity 抽屉。
