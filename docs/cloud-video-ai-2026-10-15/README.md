# 视频 AI：云端分项提交 → 本地验收 → 合并

本任务包不依赖本机文件、Codex 工具或预装 skill。云端只需要本 GitHub 仓库及可运行测试的环境。

- 仓库：Augety-silence/sjtu-learning-assistant
- 源码基线：d167f82a7e4117a4c024bf28d60f911e66c8f46c
- 工作分支：cloud/video-ai-2026-10-15（从上述基线建立，包含本编排文档）
- 最终目标：main。云端禁止合并、启用自动合并或发布版本。
- 当前功能仍未完成真实模型与原生 App 验收。历史结果见 [开发快照](../VIDEO_AI_DEVELOPMENT_PROGRESS_2026-10-15.md)。

## 云端执行顺序

先读 [完整执行 prompt](CLOUD_AGENT_PROMPT.md) 与 [工作规则](WORKING_RULES.md)，无需安装技能。

| 编号 | 独立提交 | 依赖 | 主要文件 |
| --- | --- | --- | --- |
| C1 | [按阶段恢复，不重复已结束的校对](tasks/01-stage-recovery.md) | 无 | transcript_service.py、服务/集成测试 |
| C2 | [讲义 source-only 修复的程序约束](tasks/02-guide-repair-preservation.md) | 无 | transcript_pipeline.py、管线测试 |
| C3 | [稳定的处理结果与恢复状态](tasks/03-processing-state-contract.md) | C1 | transcript_service.py、types.ts、契约测试 |
| C4 | [界面区分结束待核对、恢复和耗尽](tasks/04-ui-result-states.md) | C3 | 整理面板及前端测试 |
| C5 | [汇总回归、干净构建与交付证据](tasks/05-cloud-validation.md) | C1–C4 | 验证报告；仅必要时修 CI/构建验证 |

建议顺序 C1 → C2 → C3 → C4 → C5；如使用子代理，只可并行 C1 与 C2，文件所有权不得重叠。C3 修改服务文件前必须等 C1 合入此工作分支。

每项至少一个清晰独立的提交。禁止把五项揉成一个提交；最终保留提交历史，不 squash。全部提交到同一集成分支，在唯一的 Draft PR 更新“任务→commit→测试”表。

## 云端与本地边界

云端可以完成代码、合成数据回归及 GitHub Actions 的干净构建。云端不能访问个人电脑、真实学习数据库、真实字幕、Keychain、校园登录或真实模型接口；不要要求用户提供这些资料。

GitHub 的 macOS runner 可以产出无个人证书的候选 artifact。它不证明用户电脑上的实际播放/同步、签名身份、安装或真实模型已经通过。

全部云端工作完成后，保持 PR 为 Draft，把 PR URL、最终 head SHA、分项 commit、CI run/artifact 链接交还本地验证代理。只有该代理按 [本地验收门禁](LOCAL_ACCEPTANCE.md) 对同一固定版本验收通过后，才可合并。

## 现成工作方法

[执行](skills/cloud-video-ai-execution/SKILL.md)、[验证](skills/cloud-video-ai-verification/SKILL.md)、[协作](skills/cloud-video-ai-coordination/SKILL.md)、[安全操作](skills/cloud-video-ai-safe-operations/SKILL.md) 都是可直接读取的文档，不需要任何插件或专属工具。
