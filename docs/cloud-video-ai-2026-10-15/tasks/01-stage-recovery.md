# C1：只恢复实际未完成的阶段

## 问题与触发
基线的 prepare_for_playback 与 _schedule_due_playback_retries 两个入口，用 phase1_status != completed 决定 force_phase1。
若 V1 讲义仍有技术故障，而 Phase1 已 schema_pass=true 且仅有内容不确定，恢复讲义时会再次强制跑已经结束的校对。

## 文件所有权
- sjtu_learning_assistant/transcript_service.py
- tests/test_transcript_service.py
- tests/test_transcript_phase1_integration.py
只在必要时补相关自动调度测试。不要改 pipeline、前端或全局缓存版本。C3 必须在本提交后再改 service。

## 实施要求
用共用判定明确区分 Phase1 技术未完成与 content uncertainty，覆盖两个入口。
V1 的可恢复技术错误继续恢复；Phase1 schema/call/输入工件失效等技术问题继续恢复。
纯内容不确定不 force 重新校对；仍经现有缓存绑定与工件校验，不能因为旧 job 的 quality 字段就跳过当前输入验证。
复用 partial 工件必须保持其真实 quality.passed=false/needs_review；不得冒充成功全片缓存。
保留 cooldown、重试上限、取消、原字幕与成功片段复用。

## 验收
- 两入口分别覆盖：V1 unresolved + schema true/uncertain → V1 请求发生、force false、已结束 Phase1 的 fake agent 无新增调用。
- V1 本次恢复干净后：processing_done=true，needs_review，auto_retryable=false，内容存疑和产物仍在。
- V1 unresolved + Phase1 schema false/技术未完成 → force true，仍恢复失败块，成功检查点复用。
- 同一输入纯内容不确定不会因再次 prepare 或到期调度产生自动循环；不同模型/输入/记忆绑定不能误复用。
- 旧告警成功后清理，未解决的 V1 告警保留。

建议验证：
python -m pytest tests/test_transcript_service.py tests/test_transcript_phase1_integration.py tests/test_transcript_automation.py tests/test_transcript_playback.py -q

独立 commit 建议：fix(video-ai): recover only unfinished technical stages
交付列出测试名称、实际调用计数断言和 commit SHA。
