# C1–C5 后续回归与 issue 核对

日期：2026-10-16。基线为已合并 PR #23 的 main `6328d78f5cba1bdecc0d5647dd483a6074d989be`。
本次使用独立工作树；保留原开发目录的修改及所有历史分支。最终 head、合并提交、CI run、artifact 与 issue 关闭链接记录在后续 PR 中，避免文档自引用提交 SHA。

## 核对及改动

| Issue | 核对结论与本次工作 | 持久化回归 |
| --- | --- | --- |
| #18 C1 | 两个恢复入口均只重跑技术未完成阶段；纯内容存疑不自动循环。补上同课程记忆版本变化的缓存失效测试。 | `test_scheduled_v1_recovery_reruns_phase1_when_same_course_memory_version_changes`；原有版本未变对照、模型/端点隔离、取消及上限测试继续保留。 |
| #19 C2 | 发现并修复：较早的 schema 错误会掩盖后续错误来源声明，使通用修复改写题面后仍可被接受。现在先核对可保留的完整题目基线；来源修复须在只替换已证明错误的有限 source 集合后通过完整结构校验。否则保留首错，不请求通用修复。 | `test_guide_repair_fails_closed_when_earlier_schema_error_masks_source_mismatch` 覆盖早期 course_map、题型、空答案、后置 extensions、嵌套结构及不可信题目基线；既有题面/答案/主题/证据/题数/顺序保护与合法修复正例继续保留。 |
| #20 C3 | 未发现新实现缺陷。复核结束待核对、恢复、耗尽、取消、等待字幕与 AI 未配置的优先级及字段一致性。 | finished / recovery budget / cancel / in-progress 状态表，以及混合 V1/Phase1 失败、旧响应兼容测试。 |
| #21 C4 | 未发现新实现缺陷。复核主界面与详情对结束待核对、未来恢复、耗尽、取消及旧响应的显示；保留轮询、同步和切换保护。 | `VideoSynchronizedLearning`、`TranscriptDetailDrawer`、`VideoActions` 等实际组件测试。 |
| #22 C5 | 原桥接测试显式调用 worker。本次另补真实后台线程自动唤醒及抓取中取消；属于新增覆盖，未发现对应产品缺陷。 | `test_bridge_prepare_wakes_background_worker_and_publishes_segments`、`test_bridge_cancel_during_background_fetch_stays_cancelled_without_retry`，均经 DesktopBridge，不手动调用 `run_pending()`。 |

后台测试使用事件建立确定时序，检查处理中与终态投影、分段、可用区间、原字幕哈希及修订标识；取消后公开 jobs 列表仅保留原取消任务，无排队重试子任务。退出时释放事件并 join，确认线程停止。

## 审阅与验证口径

- 三位 GPT-6 Luna 按恢复/状态、来源约束、前端/桥接分工审阅，并交叉复核新增修复与测试。来源漏口由合成探针重现；新增防护测试先在旧行为失败，再在修复后通过。
- 本地全量后端：670 项，669 通过、1 项跳过。临时目录隔离默认路径、数据库、字幕和记忆。
- 额外运行 pytest，纳入标准 unittest discover 不收集的函数式测试。发现 archive bootstrap 测试仍期待旧版本 0020；已按实际 0021 迁移修正精确期望，并增加三张 AI task ledger 表存在断言。隔离代理及临时 home 后：712 通过、1 跳过、298 个子用例通过，无失败。没有添加可选 SOCKS 依赖或改动 CI。
- Mac CI 另暴露旧 scheduler 测试依赖 40 毫秒 sleep 推断至少两次调用，慢 runner 上只观察到一次。改用明确的第二次调用事件作有界同步，继续核对首次异常后恢复、重复 start 幂等及 stop 后线程退出；没有放宽产品计时、跳过测试或修改 CI 策略。
- 前端：Node 22.22.0 / npm 10.9.4；48 个文件、457 项测试通过；Biome 检查 121 个文件、构建通过。
- Python/npm 许可证检查、文件秘密扫描及 diff 空白检查；最终 head 的 Linux Python 3.12 全量、gitleaks、macOS/Windows 打包结果见后续 PR 的 CI 记录。未更改依赖、版本、工作流、权限或质量阈值。
- 本轮没有读取真实字幕、调用真实模型或修改个人数据库。所有新回归使用合成数据。此前 PR #23 的真实模型验收有 schema 失败和部分材料，不能解释为内容质量通过；本轮更严格的失败保护不改变该结论。
- 依据 `LOCAL_ACCEPTANCE.md`，后续提交重新检查受影响的来源保护及打包代码一致性；此前真实播放、同步、倍速和安装证据属于 PR #23 对应源码，不能冒充新提交的重新运行结果。新的本地候选与受影响检查另在后续 PR 记录。

## 关闭及回退

只有修复合并、最终提交回归与 CI 核对完成后，才逐项附验收依据并关闭 #18–#22。本文不提前声明 issue 已关闭。
回退按后续 PR 中的提交逆序使用 `git revert`，保留原 C1–C5 历史，不 reset 或强推。
