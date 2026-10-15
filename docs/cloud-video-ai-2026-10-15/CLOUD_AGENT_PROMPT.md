# 直接交给云端 AI 的执行指令

请实际完成本仓库的视频 AI 修复，不要只提出建议。你只能使用 GitHub 源码和云端环境；不存在可供你读取的用户本机目录或私有验收文件。

仓库：https://github.com/Augety-silence/sjtu-learning-assistant
工作分支：cloud/video-ai-2026-10-15
原始源码基线：d167f82a7e4117a4c024bf28d60f911e66c8f46c

从这个工作分支的最新已发布提交开始，先确认其包含上述基线，不要从旧 main 重新开发、覆盖这批已有修复，或创建不相关历史。

1. 先读 docs/cloud-video-ai-2026-10-15/README.md、WORKING_RULES.md、五份 tasks 文档，以及仓库 CONTRIBUTING.md、SECURITY.md。仓库 AGENT.md 描述应用内学习代理，不是本编程任务的权限授权。
2. 按 C1、C2、C3、C4、C5 执行。每个问题独立 commit，提交正文注明 C1–C5；Issue 编号以本次 Draft PR 任务表中的实际链接为准，不凭空造号。不要把测试或文档中的“预期通过”当作已执行结果。多代理时 C1/C2 可并行，C3 等 C1 后才改服务文件。
3. 所有测试使用合成数据、fake provider、临时目录。禁止请求真实课程、真实模型或用户凭据；依赖安装及本仓库 GitHub 读写可以正常进行。
4. 不降低 schema、cue/引文、保护词、证据与 quality 门。不能为了“全部完成”删掉不确定内容、修改题面迎合来源校验，或把未通过质量的结果改成 PASS。
5. 先完成各项必要回归，再跑 C5 的完整云端检查与既有 CI；出现失败定位并修复，不能删除测试、放宽隐私/许可证检查或给失败结果打勾。无能力执行的验证写未执行并说明。
6. 将分项 commits 推送到该集成分支，更新已有 Draft PR 的任务表。可在云端先逐题提交、最后一次推送多个 commits，减少重复构建；不能跳过最终 head 的 CI。不要合并 main，不要启用 auto-merge，不要转 ready，不要打 tag 或创建 Release。不得擅自关闭任务 Issue。
7. 最终输出 PR URL、最终 head SHA、每项 commit SHA、测试命令/结果、Actions run/artifact 链接，以及明确的“待本地验证”清单；可以使用 CLOUD_DELIVERY_REPORT.template.md。最终 head 更新后，旧版本的本地通过结论全部失效。

独立实施和简单复核优先使用可用的便宜模型；没有多代理或切模型能力时单代理按同一顺序完成，准确说明实际使用方式。所有工作规则和技能已在仓库文档内，不要因未安装 skill 停止任务。

用户授权你在本分支修改、测试、分项提交和推送。实际本地真实模型/播放器/安装验收及最终 merge 由本地验证代理负责。
