# 视频 AI 首期修复与逐页 API 设计

2026-10-12。本轮以视频为优先，完成相关代码并更新 `/Applications/SJTU Learning Assistant.app`，同时提供全部14个导航页签的 API 实施设计。当前工作区包含原有未提交修改；此次不是发布标签、提交或全系统功能上线。

## 已修复

- 播放地址和字幕独立读取；字幕等待不再锁住播放或其他工具。播放、手动字幕、配置、课程及任务读取有30秒界面等待上限；同一资源最多两个未完成底层读取，不能无限堆叠。两个都卡住时明确要求重开应用。
- 整理、重试、下载、课件和阅读分开管理操作状态；同一生成任务防重复。切课和卸载时隔离迟到响应。写任务的底层操作不简单超时解锁，避免重复生成。
- 显示 AI 配置 checking/ready/blocked/unknown 和具体不可用原因，配置就绪不冒充网络验证；材料状态和视频工具读取错误不再被静默吞掉。
- 已有学习材料可以阅读并补充深度校对。旧完成记录缺少 AI 校对不再被误标成失败；可阅读且 AI 校对未通过的材料显示部分完成。顶层 partial 任务保留部分完成状态，并可直接查看已有结果，不必先重新生成。
- 知识点与 study_guide 并存时，读取顶层知识点、课堂例子和训练；兼容真实旧格式 concepts/emphasized_points/cases_formulas_conclusions/review_questions，以及生产 concept/statement 结构和 cue_id/时间/quote 证据。案例公式与课堂例子同时存在时均保留；无答案的旧复习题不编造答案。
- 四角色术语/校对/复核/记忆提供完整 JSON 字段、类型、枚举和保守合法模板；本地严格解码、cue/diff/证据规则保留。prompt v3使旧提示缓存失效。
- JSON-object 仅对已实测的校内 endpoint + deepseek-chat 启用，不推导其他模型均支持。
- 「AI 校对 → AI 用量」展示该校对产物语义调用的 provider 输入、输出及总 Tokens；包含有响应的失败调用，缺失字段保留未知，不从字符推算，不把超时记零。旧产物没有用量时显示未知；该面板不含原始规整、小结或其他页面的调用。

## 原因与实测

原安装 App 管理会计共8条录像，1已整理、5部分完成、2未整理，0处理中。最新部分任务曾连接失败；历史18份校对工件共335 chunks，均为 schema 未通过/降级，491个语义调用没有 usage。旧提示只点名数据类而没有完整字段合同，是已定位的契约缺口；不能据此认定全部历史失败都只有这一原因。另外真实第1周第10节 summary.json 含17条 concepts、20条 emphasized_points、13条案例公式和12条 review_questions；原页面没有适配这些字段，已确认会显示“暂无”，本轮补齐兼容。

通过用户已经配置的校内服务和 Keychain 凭证执行有限合成输入请求，未记录密钥，也未对真实课堂重新生成：

| 验证 | 结果 | 证据 |
|---|---|---|
| GET /models | HTTP200，7个模型ID | evidence/api-models-probe.json |
| 普通一次性小请求 | 约20秒超时，用量未知 | evidence/api-generation-probes.json |
| 真实 SSE 小请求 | HTTP200，首token1.133秒，17总Tokens | evidence/api-generation-probes.json |
| JSON-object 小请求 | HTTP200，9.316秒，30总Tokens，本地合同通过 | evidence/api-json-mode-probe.json |
| 生产四角色编排，合成两条字幕 | 第一次completed，schema/critic通过；报告误读events键未捕获用量 | evidence/live-semantic-smoke.json |
| 同样生产编排再次验证用量 | schema通过，critic未接受，正确保留原文并partial；3次调用共3106总Tokens，memory未调用 | evidence/live-semantic-smoke-with-usage.json |

两次质量判断存在波动；这是有限样本，不证明长期稳定、所有模型或所有字幕都成功。真实 SSE probe 成功也不代表产品聊天已接入流式。当前严格式产物继续一次性生成、校验后显示。

## 验证范围

- 前端46套件、395项全量测试通过；最后两项状态/阅读新增后，视频两套件85项通过。最后知识点/用量等相关4套件64项通过；最终视频/抽屉/兼容/用量5套件112项通过，覆盖新增状态与真实旧格式。类型检查、Biome与生产构建通过。
- 后端 usage/capabilities 相关85 tests + 8 subtests；角色合同/Phase1相关52 tests +20 subtests通过（子代理）。根代理另验证桥接和字幕回归6项、收紧provider/model后桥接9项、诊断重命名1项通过；上述有重叠，不能相加为独立总数。
- 秘密扫描通过；构建完整后端，检查 PYZ 存在 ai_capabilities/ai_classifier/course_learning_prompts/orchestrator/schemas/transcript_service；原生bundle与签名校验通过。
- 合成 UI 检查浅色、深色800×900、AI关闭和已有材料阅读；窄窗口无横向溢出。图为合成验收数据，不代表新生成真实课程结果。
- 原生 App 实际打开管理会计录像，看到 Pause/Elapsed/Duration以及字幕；播放后暂停。字幕期间其他工具可用。真实课堂重新生成、批量整理、课件PDF输出、作业或消息写操作未在本轮执行。最终原生检查确认旧知识点及12条复习题恢复；另一份部分校对产物的32次历史语义调用显示用量未知，且课堂例子与案例公式同时可读。最后一次冷启动课程权限读取曾30秒超时，点重新加载后恢复，未静默忽略。
- 更新前后核对314个已存在的字幕 JSON 与设置文件，哈希未变、无缺失；不据此声称整个数据库字节未变化（App正常同步/cache行为独立）。

## 体系与后续

详见 [API_SYSTEM_DESIGN.md](API_SYSTEM_DESIGN.md)。每页均有输入范围、证据定位、具体能力、输出方式、模型路由、拟议预算、校验与实施状态；通用部分包括 request/result/events 合同、缓存失效、授权、用量未知语义、长录像预算校准和实施顺序。

视频首期、基础能力预检与视频语义用量由本轮前半段完成；后续续作已补齐页面 AI 的持久任务/计量平台和产品聊天 SSE。视频整理器、知识库编译器等已有独立后台流程仍需逐步迁移到同一任务表；页面专用 schema 按消息→资料→作业→日程→概览→成绩深化，花名册/批改继续按角色隔离。

### 续作：统一任务、页面上下文与聊天流式输出

后续云端续作已把所有页面的“询问 AI”入口接到同一套持久任务协议：任务、顺序事件、页面来源、模型调用次数、provider 已报告 Token、未知/部分用量和预算扣减均写入本机数据库。预算在模型调用前限制调用次数与输出上限；输入 Token 依赖 provider 响应记账，不能在没有 tokenizer 证据时伪装成精确预估。未知输出用量按该次已批准上限扣减，避免未知被当作零后继续超用。

页面仅传 `page_id`、选择器和有限实体 ID，不上传整页数据、文件路径或密钥。回答合同要求分开呈现可核验事实、决策建议和需用户确认的写操作。14 个导航页均可从顶栏进入 AI 工作区；各页专用 schema 和专用证据工具仍按本文路线逐步深化，不把通用入口冒充全部专用能力已经完成。

AI Chat 新增后台任务与轮询事件，确定性预检索完成后使用真实 OpenAI-compatible SSE 显示增量正文；工具选择步骤继续一次性返回并保留 Activity 审计。SSE 记录 provider usage；若 endpoint 不返回完整 usage，界面和总账保持“部分/未知”，不推算字符 Token。数据库升级到 `0021`，新增 `ai_tasks`、`ai_task_events` 和 `ai_usage_records`。

原安装包保存在 `/private/tmp/sjtu-ai-runtime-20261012-n9m2elcc/previous-installed.app`；前一候选界面也保留在同一检查点。最终界面替换时正常桌面窗口已退出，后台原有同步helper仍运行；核对新旧PYZ后端完全一致后，使用macOS原子目录交换避免路径空档，保留同步进程和全部旧包。现应用仍显示源码版本1.6.2，本轮按本地迭代记录，不冒充新发布版本。
