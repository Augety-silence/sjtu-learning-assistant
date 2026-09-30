# Prompt 02：P1 统一文件体验、预览与文件级 AI

请把 SJTU Learning Assistant 的课程文件、邮件附件、本地归档、云端版本和 AI 引用重构为统一文件体验。目标是学生从任何入口找到文件后，都进入一致的详情、预览、归档、恢复和 AI 阅读流程。

## 前置条件

- P0 的归档版本、恢复和持久化任务必须已通过测试。
- 开始前检查实际 schema、API 和工作区状态，确认 `0018` 或后续迁移是否存在。
- 先提交 `LearningResource` 身份映射和兼容方案，确认后实施。

## 统一资源模型

建立增量兼容的逻辑层或持久化表：

- LearningResource：稳定资源 ID、source type/source ID、course ID、name、MIME
- ResourceLocation：remote/local/archive/cloud 位置与 availability
- ResourceVersion：size、mtime、SHA-256、source version、verified_at
- ResourceRelation：assignment/message/AI session 与 resource version 的关系

保持现有 `CourseFile`、`EmailAttachment`、`AIManagedFile` 可用，通过映射表或 service 聚合过渡，避免一次性重写。

## API

设计并实现：

- `resource_list(query, course, source, status, sort, cursor)`
- `resource_detail(resource_id)`
- `resource_versions(resource_id)`
- `preview_prepare(resource_id, version_id?)`
- `preview_release(preview_id)`
- `resource_archive(resource_ids)`
- `resource_restore_plan(resource_id, version_id)`
- `ai_file_chat_start(resource_id, version_id, messages)`

所有路径只在后端解析；前端使用 opaque ID。列表必须后端分页，详情按需加载。

## 预览器架构

新增 `PreviewerRegistry`，预览器按 MIME、扩展名、大小和安全策略匹配。首批支持：

- PDF：分页、缩放、文本层、可见页优先
- 图片：尺寸约束和错误回退
- Markdown：经过 sanitize 的渲染
- 纯文本：编码检测和最大读取限制
- 代码：语言识别、换行和复制

使用动态 import 拆分依赖。预览会话必须可释放，避免 Blob URL、临时文件和文件句柄泄漏。WebView 不直接接收任意本地绝对路径。

## 文件详情

在现有 Materials/云盘页面共享统一详情组件，展示：

- 来源、课程、位置和 availability
- 当前版本、大小、mtime、SHA-256
- 本地、归档和云端状态
- 相关作业、消息和 AI 会话
- 打开、预览、归档、恢复、另存为、AI 阅读
- 加载、损坏、超限、移动、权限和离线状态

## 文件级 AI

- 会话固定绑定 resource ID + version ID + SHA-256。
- 建立 `BaseFileParser` 和 PDF/Markdown/text 首批解析器。
- 解析缓存包含 parser version、resource version 和内容哈希。
- 流式事件区分 chunk、done、error、cancelled。
- 回答显示来源文件和版本；文件更新后历史会话仍指向原版本。
- 限制解析大小、页数和 Token，错误可理解且可重试。

## 参考代码与开源规范

重点参考：

- `reference/SJTU-Canvas-Helper/src/components/renderers.tsx`
- `preview_modal.tsx`
- `renderer_shell.tsx`
- `pdf_renderer.tsx`
- `file_ai_chat_modal.tsx`
- `src/lib/events.ts`

参考其 renderer 注册、ResizeObserver、Blob URL 释放、监听卸载和流式消息状态。目标实现必须沿用现有组件/CSS/motion，不整体引入 MUI。Rust parser 只作为边界和错误处理参考，Python 端重新实现。

若复制或改编代码，完成 README 致谢、THIRD_PARTY_NOTICES、原始 MIT LICENSE、文件来源注释、依赖许可证和发布包检查。

## 测试

- 资源映射稳定性和旧 DTO 兼容
- 同文件多来源、多版本和同名不同目录
- MIME/扩展名冲突、未知类型、损坏和加密文件
- 大 PDF、Blob URL 释放、关闭后取消监听
- Markdown XSS 与代码内容转义
- preview prepare/release 权限和过期
- AI 引用版本固定、解析缓存命中/失效、流式中断
- 列表分页、搜索、筛选、键盘和焦点
- 前端 bundle chunk 与主线程性能

运行完整 Python 测试、前端 test/lint/build、许可证检查、秘密扫描和 Mac App 内预览 smoke。

## 交付

提供资源模型、映射规则、API 契约、预览器清单、AI 引用规则、依赖/许可证变化、测试结果、App 结果和后续格式扩展建议。未经允许不 commit、push 或创建 PR。
