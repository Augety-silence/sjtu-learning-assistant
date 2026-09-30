# Prompt 00：现有能力完善与工程基线收敛

你正在维护 SJTU Learning Assistant。请在实际目标仓库中完成一次“现有能力收敛”开发任务，使课程、资料、云盘和 AI 文件链路具备清晰基线，再进入 P0–P2 重构。

## 云端审计基线

- 参考远端 commit：`e342eeb4390a9617f630a1b370a532d7612643fe`
- 该 commit 的数据库 schema 为 `0017`。
- 用户其他工作区可能存在尚未提交的 `0018` 云盘归档/恢复实现。开始前必须以实际分支、status、staged/unstaged diff 和迁移目录为准；不得假定远端或本地任一侧是唯一真相。

## 目标

1. 建立当前功能、数据模型、bridge API、前端页面和测试的准确能力地图。
2. 收敛课程资料、邮件附件、本地归档、云盘备份和 AI 附件之间的身份与状态表达。
3. 修复现有代码中会阻碍 P0–P2 的重复 DTO、模糊状态、缺失测试和不可诊断错误。
4. 保持现有布局、设计语言、AI Chat 三栏结构和业务 API 稳定。
5. 为后续开源代码改编建立归属目录与检查流程。

## 开始前

- 读取 `AGENT.md`、`README.md`、`THIRD_PARTY_NOTICES.md`、产品路线文档和当前迁移头。
- 记录当前分支、HEAD、git status、staged diff、unstaged diff、untracked files。
- 检查 `CourseFile`、`EmailAttachment`、`AIManagedFile`、`CloudFile`、`ArchiveService`、`BackupService`、`FilePreviewDialog`、`dashboard_service.py` 和 `desktop_app.py`。
- 输出“实际已有/部分已有/缺失/与路线冲突”矩阵，获得确认后再改代码。

## 实施要求

### A. 数据与身份审计

- 为 Canvas 文件、邮件附件、AI 管理文件和云端文件列出稳定 ID、路径、哈希、大小、mtime、云端 ID、状态和关联关系。
- 先通过 service/DTO 统一状态语义；只有现有表无法承载时才新增迁移。
- 所有前端 DTO 必须有 Python 端来源和 TypeScript 类型，不允许页面自行拼接隐含状态。

### B. Bridge 与错误收敛

- 所有 pywebview invoke 命令进入统一 allowlist 和 payload 校验。
- 错误 DTO 至少包含 code、message、retryable、task_id（若适用），并默认脱敏路径和凭据。
- 为 `api.ts` 增加协议测试，覆盖未知命令、错误载荷和离线 bridge。

### C. 前端现有页面完善

- `MaterialsView`：状态来源一致，预览/打开/归档动作有明确 loading、empty、error。
- `BackupView`：区分扫描、上传、成功、部分失败和离线；进度来自真实任务。
- `OverviewView`、`DeadlinesView`：只修正数据一致性和错误状态，不提前引入 P2 任务模型。
- `AIChatView`：文件附件引用至少能追溯到现有 source ID，保持三栏结构。

### D. 开源归属基础

若本轮复制或改编 `reference/SJTU-Canvas-Helper/` 中代码：

- 新增 `third_party/SJTU-Canvas-Helper/LICENSE`，内容与参考包 LICENSE 完全一致。
- 更新 README 的“致谢与开源归属”。
- 更新 `THIRD_PARTY_NOTICES.md`，记录源地址、commit、作者、MIT 和实际改编范围。
- 在改编文件中加入来源注释。
- 运行许可证检查并确认发布包携带许可证。

## 测试与验证

- `python -m unittest discover -s tests -v`
- `python scripts/check_licenses.py`
- `python scripts/scan_secrets.py`
- `cd dashboard-web && npm ci --no-audit --no-fund`
- `npm run test`
- `npm run lint`
- `npm run build`
- `node ../scripts/check_licenses.mjs`
- 按平台构建桌面 App，并验证静态资源、bridge 和首屏加载。

测试必须使用临时数据库、临时目录、fake service 和 fake cloud provider，不访问真实账号与云盘。

## 交付

- 修改文件清单
- 当前能力矩阵
- DTO/API 收敛结果
- 数据兼容与迁移结论
- 开源来源映射
- 测试、构建与 App smoke 结果
- P0 开始前仍需解决的问题

未经允许不要 commit、push 或创建 PR。只精确暂存本任务文件。
