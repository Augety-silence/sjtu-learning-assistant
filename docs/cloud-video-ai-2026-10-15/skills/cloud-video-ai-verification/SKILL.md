---
name: cloud-video-ai-verification
description: Verify synthetic behavior, CI and source-to-artifact identity while reserving live-model and native App acceptance for the local verifier.
---
按目标验证状态转移、调用计数、失败后恢复、缓存绑定、取消与输出保留，不用“测试通过”替代未执行步骤。
source-only修复测试同时覆盖合法元数据更正和恶意题面/答案/主题改动；旧错误仍须失败。
本地合成测试→完整CI→干净构建证据依次记录实际SHA、命令、退出状态和artifact。
云端无真实字幕/Keychain/个人Mac，相关检查明确待本地；不伪造截图、签名、模型输出或评分。
任何后续head变动都需要对应的新CI和受影响验证。
