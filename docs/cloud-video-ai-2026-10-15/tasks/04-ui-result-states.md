# C4：界面显示真实结束、恢复和耗尽状态

依赖 C3 的稳定契约；不要改变后端业务字段来迎合 UI 测试。

## 文件所有权
- dashboard-web/src/components/TranscriptDetailDrawer.tsx
- 需要时：VideosView.tsx、VideoSynchronizedLearning.tsx、TranscriptPhase1Panels.tsx
- 对应前端 test 文件
不改 pipeline、service、全站样式、页面布局或版本号。

## 结果要求
- 纯内容不确定显示“校对已结束，部分内容需核对”；保留不确定项与可用产物，不显示处理中或自动恢复倒计时。
- 技术错误说明具体失败阶段及实际恢复时间；仅确有未来 retry_after 且未耗尽时显示等待自动恢复。
- 自动恢复耗尽明确显示“自动恢复已用尽，可手动重试”，不再承诺后台仍在运行。
- 处理中显示实际进度；成功、取消、等待字幕/未配置分别准确。
- 新枚举缺失的旧响应按现有 flags 兼容，不能崩溃或误显示成功。
- 原有自动准备、时间同步、seek/倍速、切换、取消与过期请求防护保留；不新增用户必须点击的启动步骤。

## 测试
以 C3 合同为准覆盖主要界面，而非只测试新的文案 helper。
断言状态文案、重试按钮/倒计时可见性、没有因纯内容不确定渲染而自动发起重试。
保留可用讲义/时间线和原字幕，不以清空内容解决黄色状态。

npm --prefix dashboard-web run test -- src/test/TranscriptDetailDrawer.test.tsx src/test/VideoSynchronizedLearning.test.tsx src/test/VideosView.test.tsx src/test/VideoActions.test.tsx
npm --prefix dashboard-web run lint
npm --prefix dashboard-web run build
独立 commit 建议：fix(video-ai): distinguish finished review and recovery in UI
