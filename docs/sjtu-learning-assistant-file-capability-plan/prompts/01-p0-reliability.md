# Prompt 01：P0 云盘归档、恢复与任务可靠性

请完善 SJTU Learning Assistant 的云盘归档、下载恢复和持久化任务系统，使文件在网络中断、App 重启、路径变化和冲突情况下仍可追踪、校验和安全恢复。

## 开始前

1. 检查实际分支、status、staged/unstaged diff、untracked files 和 Alembic current/head。
2. 审计实际仓库是否已经存在 `ArchiveEntry`、`ArchiveVersion`、授权根、任务、事件和恢复 service。若存在，增量完善；若不存在，以当前 `0017` 为基线设计下一迁移。
3. 给出数据模型、路径规则、状态机、幂等策略、迁移与回滚风险，获得确认后实施。
4. 保护其他 Agent 改动；不执行 reset hard、clean、全量 restore/stash 或强制切分支。

## 数据模型

至少建立或补齐：

- ArchiveEntry：原始绝对路径、授权根快照、相对路径、文件名、MIME、当前状态
- ArchiveVersion：size、mtime、archived_at、SHA-256、cloud object ID/path/etag、version status
- AuthorizedRoot：规范化路径、卷身份、授权来源和时间
- PersistentTask：kind、idempotency key、payload version、status、processed/total、retry count、last error
- ArchiveEvent：task/entry/version、event type、脱敏 message、timestamp

旧记录缺少可靠原路径时统一标记为“需要选择恢复位置”，并保留旧云端路径和大小供人工选择。

## 归档状态机

实现并测试：

- 同一身份 + 同 SHA-256：跳过并记录 verified/skipped 事件
- 同一身份 + 内容变化：创建不可变新版本
- 同名不同路径：独立 entry，不互相覆盖
- 云端缺失：remote_missing
- 本地缺失：local_missing
- 缺强校验：needs_verification
- 运行中断：interrupted，可安全重试
- 连续点击：命中同一幂等任务

上传成功但数据库终态写入失败时，下一次 reconcile 能识别云端对象并完成或进入可诊断状态。

## 恢复流程

- 默认目标来自可信归档记录，不接受前端传入任意绝对路径。
- native picker 产生 AuthorizedRoot。
- 逐级 `lstat`/no-follow 检查，拒绝路径穿越和符号链接绕过。
- 恢复 plan 展示目标目录、是否需创建目录、卷状态和冲突信息。
- 冲突支持 skip、save_as、overwrite、compare。
- 下载到目标同文件系统临时文件。
- 完成 size + SHA-256 校验后 `fsync` 并原子替换。
- 覆盖等破坏性动作在重试时重新确认授权和冲突状态。
- 事件日志记录目标、版本、校验结果和时间，前端展示脱敏路径。

## 任务与实时事件

- 数据库任务是事实源。
- 新增 `DesktopEventEmitter` 抽象和 pywebview 实现，推送真实字节进度。
- 事件载荷包含 schema version、event ID、task ID、processed、total、status、timestamp。
- 对 SQLite 进度写入和 `evaluate_js` 做节流；终态强制写入。
- 前端初始加载读取任务快照，运行期间事件更新，事件丢失后按 task ID 回读。
- App 重启后标记并恢复 interrupted 状态。

## 前端

在现有云盘页面内完善：

- 列表、搜索、状态筛选、排序和分页
- 文件详情、版本历史、哈希和最近操作
- 上传、下载、校验、失败和离线状态
- 单文件/批量归档、恢复、另存为、失败重试
- 恢复计划与冲突对比
- 键盘、焦点、ARIA、reduced-motion

## 参考代码与开源规范

可以参考 `reference/SJTU-Canvas-Helper/src/lib/events.ts` 的监听生命周期，以及 `file_download_table.tsx` 的任务状态表达；Rust 下载代码只作为分块下载和进度节流的算法参考。适配到 Python/pywebview 和现有 UI，不引入 Tauri/MUI。

发生代码复制或实质性改编时，必须执行 `OPEN_SOURCE_ATTRIBUTION.md` 中的 README、THIRD_PARTY_NOTICES、LICENSE、文件注释和发布包要求。

## 测试

新增单元、集成和路径安全测试：

- 中文、空格、超长路径、空文件、同名不同目录
- 大文件与进度节流
- 网络中断、上传/下载中断、校验失败
- 云端删除、本地移动、本地修改
- 原目录缺失、不可写、外接盘未挂载
- symlink、`..`、恶意绝对路径
- App 重启、幂等重复点击
- 四种冲突策略和原子写入
- 从旧 schema 升级及旧记录降级

运行完整 Python 测试、前端 test/lint/build、许可证检查、秘密扫描和桌面 App smoke。

## 交付

列出模型/迁移、API、路径恢复规则、安全机制、测试结果、App 落地结果、开源来源映射和尚存风险。未经允许不 commit、push 或创建 PR。
