# C3：统一整体完成与技术恢复状态

依赖 C1 完成，避免两个 agent 同改 service。

## 问题
已有 processing_done、phase1_processing_done、auto_retryable、retryable、retry_after 等字段，但组合易把“校对结束待核对”或“重试已耗尽”继续呈现成笼统 partial。
目前 V1 诊断的可重试属性也可能覆盖 job 的实际自动重试上限；不能仅看 error code 就宣称还会自动恢复。

## 文件所有权
- sjtu_learning_assistant/transcript_service.py
- dashboard-web/src/lib/types.ts
- tests/test_transcript_phase1_integration.py
- 必要的服务/桥接契约测试
C1 的测试与实现保留，不改 pipeline 或前端组件。

## 实施要求
提供一个稳定、可枚举、向后兼容的整体结果/处理状态，或等价的单一明确契约；不要让各组件各自猜业务状态。
至少能区分：处理中、已完成、已结束需核对、技术失败等待/正在恢复、自动恢复耗尽、取消。来源等待/AI未配置不得误判完成或恢复耗尽。
统一输出和测试字段关系：
- schema true 的纯内容不确定 + V1 clean + 无活动任务 → 已结束需核对，整体处理结束，不自动重跑；
- Phase1已结束但V1仍技术失败 → 整体未结束，可用产物保留，按 cooldown/上限给出实际恢复状态；
- 达到自动恢复上限 → 不再 auto_retryable/不承诺下一次自动恢复，但可按现有规则提供手动重试；
- 取消优先于过期 worker 状态，不重新入队；
- 全部成功时也应有明确完成态，不能只把 content_uncertain 当作 processing_done。
检查缓存/工件身份和 stale 保护，不以 status 字符串代替质量通过。保持现有消费者和旧 fixture 的兼容性。
若新增枚举字段，给出值和优先级表，允许前端对旧响应兼容回退。

## 验收
对上述状态及混合状态做表格化用例，验证整体/Phase1完成、重试数/时间/耗尽、取消及可用 segments/ranges 不丢失。
python -m pytest tests/test_transcript_service.py tests/test_transcript_phase1_integration.py tests/test_transcript_playback_bridge.py tests/test_transcript_bridge.py -q
独立 commit 建议：fix(video-ai): expose consistent processing and recovery states
