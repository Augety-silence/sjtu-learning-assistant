---
name: cloud-video-ai-coordination
description: Coordinate inexpensive cloud implementation agents with file ownership, independent commits and a single draft integration PR.
---
简单实施/复核优先便宜模型；不可切模型就诚实单代理执行。
任务按文件所有权分配，不同agent不同时写同一文件。C1与C3共用service必须串行。
维护任务→commit→测试→限制表。全部提交到本次集成分支，不squash、强推或混入其他工作。
PR始终Draft，最终head与CI证据交还本地验证代理；不自动合并或关闭Issues。
不依赖固定Task/MCP/CLI。
