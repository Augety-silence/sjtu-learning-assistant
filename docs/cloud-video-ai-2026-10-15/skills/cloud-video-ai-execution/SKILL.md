---
name: cloud-video-ai-execution
description: Implement the repository's video AI tasks as independent commits using only cloud-accessible code and synthetic data.
---
先读本任务包 README、WORKING_RULES 和对应 tasks。用已有源码与可复现失败决定最小改动；每题独立commit。
C1/C2可并行，C3须等C1且独占service，C4依赖C3，C5汇总。
修复行为要配必要回归。技术恢复、内容不确定、已成功cache身份分开；schema/事实/质量门不放宽。
没有专属工具或其他skill时使用当前AI的等价工具，不停在安装插件。
不访问用户本机或真实模型，不做merge/release。
