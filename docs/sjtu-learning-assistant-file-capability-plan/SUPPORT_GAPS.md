# 文件能力需要补充的支持

## 1. 当前云端基线

目标仓库云端 commit `e342eeb4390a9617f630a1b370a532d7612643fe` 已具备：

- Canvas 课程、作业、公告、文件和邮件附件的本地数据模型
- `ArchiveService` 本地资料归档与版本副本
- `BackupService` 向交大云盘上传 Canvas、邮件和 AI 管理文件
- 路径归一化、稳定文件名、受控目录打开和符号链接防护
- 图片、PDF、文本的基础 `FilePreviewDialog`
- AI 附件与 Agent Activity 轨迹
- Python unittest、Vitest、Biome、Vite build、许可证检查、秘密扫描和跨平台构建 CI

云端基线只有 schema `0017`。用户本地曾实现的 `0018` 云盘条目/版本/恢复任务不属于本次云端仓库事实，后续 Agent 必须在实际工作分支上重新检查。

## 2. 统一文件身份

需要新增或重构：

- `LearningResource`：统一 Canvas 文件、邮件附件、本地归档和云端版本的逻辑身份
- `ResourceLocation`：表达 source、local、archive、cloud 等位置
- `ResourceVersion`：记录 size、mtime、SHA-256、来源版本和创建时间
- `ResourceRelation`：连接课程、作业、消息、AI 会话和具体版本

最小字段：

- 稳定资源 ID
- source type + source ID
- course ID（可空）
- display name、MIME、size
- local path（仅后端保存，前端默认脱敏）
- archive entry/version ID
- SHA-256
- availability/status
- created/updated/verified time

## 3. 预览支持

当前预览通过 data URL / iframe / pre 展示图片、PDF 和文本。需要补充：

- `PreviewerRegistry`：按 MIME、扩展名、文件大小和安全策略选择预览器
- `preview_prepare(resource_id, version_id?)`：生成受控预览会话
- `preview_release(preview_id)`：释放临时 URL、缓存和文件句柄
- PDF 分页/缩放/文本层；大 PDF 延迟渲染可见页
- Markdown 安全渲染；纯文本编码检测；代码语言识别
- 文件移动、权限变化、损坏、加密和超限状态
- 预览版本与 AI 引用版本一致
- WebView 不直接接收任意 `file://` 路径

首批格式：PDF、图片、Markdown、纯文本、代码。DOCX、XLSX、音视频、压缩包和 Notebook 放在后续按需扩展。

## 4. 下载、归档与恢复任务支持

需要统一持久化任务模型：

- task ID、kind、resource/version ID
- idempotency key
- queued/running/verifying/succeeded/failed/interrupted/cancelled
- processed bytes、total bytes、真实 progress
- retry count、next retry、last error
- payload version、created/started/finished time

需要新增：

- 前端初次加载读取任务快照
- pywebview bridge 实时事件通道
- 事件丢失后按 task ID 回读
- App 重启后将不可继续任务标记 interrupted，并提供安全重试
- 指数退避仅用于幂等网络步骤
- 覆盖、删除等破坏性步骤重新要求用户确认
- 上传/下载进度节流，避免频繁写 SQLite 或高频 `evaluate_js`

## 5. 安全恢复支持

需要保证：

- 原始路径来自可信数据库记录
- 用户通过 native picker 授权恢复根
- 逐级检查符号链接和目录边界
- 同目录临时文件写入
- size + SHA-256 校验后原子替换
- skip/save_as/overwrite/compare 四种冲突计划
- 原目录缺失、不可写、外接盘未挂载时提供新位置
- 旧记录缺少可靠路径时使用“需要选择恢复位置”状态
- 每次恢复记录目标、版本、校验结果和时间

## 6. 文件级 AI 支持

需要新增：

- `ai_file_chat_start(resource_id, version_id, messages)`
- 引用快照：resource ID、version ID、SHA-256、解析器版本
- `BaseFileParser` 与 PDF/文本/Markdown 首批解析器
- 解析缓存索引和大小/Token 上限
- 流式 chunk/done/error 事件
- 中断、重试和会话恢复
- 回答中的来源与版本展示
- 文件变化后保留历史会话对应的旧版本身份

## 7. pywebview 事件桥支持

参考项目的 Tauri 事件不能直接搬入。目标架构需要：

- 前端 `useDesktopEvent<EventMap>()` Hook
- 后端 `DesktopEventEmitter` 接口
- pywebview 实现负责安全序列化和节流
- 测试实现收集事件，便于断言顺序和载荷
- 每个事件包含 schema version、event ID、task ID、timestamp
- 组件卸载时可靠取消订阅
- 事件处理器避免 stale closure

建议事件：

- `task.progress`
- `task.completed`
- `task.failed`
- `preview.invalidated`
- `ai.file.chunk`
- `ai.file.done`
- `ai.file.error`

## 8. 前端与依赖支持

目标项目当前没有 MUI、`react-pdf` 或 `@cyntler/react-doc-viewer`。实施时应：

- 延续现有 Button、Dialog、CSS token 和 motion 体系
- 优先只引入 `react-pdf`/`pdfjs-dist` 完成 PDF 首批能力
- 对依赖做 bundle size、WebView 兼容和许可证检查
- 使用动态 import 拆分预览器 chunk
- 对长任务列表采用分页或虚拟化
- 保留键盘、焦点、ARIA 和 reduced-motion

## 9. 测试支持

必须补充：

- fake cloud provider 与网络中断注入
- 临时目录中的上传、下载、校验、恢复集成测试
- 中文、空格、长路径、空文件、同名不同目录、大文件、符号链接
- App 重启与 interrupted 任务恢复
- 连续点击导致的幂等性测试
- 预览器 MIME/扩展名冲突、超限、损坏与资源释放
- pywebview 事件顺序、节流、卸载后取消订阅
- AI 文件引用版本固定和解析缓存失效
- Alembic 从 `0017` 升级及旧记录安全降级
