# SJTU Learning Assistant AI Integration and API System Design
状态：逐页实施设计与视频首期实现记录；日期：2026-10-12。

目标是保留全部 14 个导航页签的 AI 融合目标，构建基于课程证据的内部学习决策支持；第一阶段优先视频闭环、能力预检和 Token 计量。

架构由独立代理审查，实施与实测由任务负责人完成。真实课程状态只读核实；生成验证使用临时合成字幕，未重写历史课程工件。限定范围的 provider 结果见 2.1。

## 1. 产品与系统结论

目标不是在每页添加一个聊天框，而是让 AI 根据学生授权的课程、任务、消息、资料和视频证据，解释当前情况、给出下一步建议并说明依据。AI 负责解释和建议；确定性服务负责权限、时间与分数计算、任务状态、缓存、校验和写操作。用户已授权的生成任务可自动保存可追溯的内部产物和未验证课程记忆；提交作业、发送消息、改分等外部或高影响变更应先呈现具体结果，再按授权执行。

当前存在分散但有价值的 AI 工作流：AI Chat 有持久化会话和受限只读工具；视频字幕任务有后台处理、缓存与 CourseLearningOrchestrator；知识库编译器有阶段、进度、取消和文件产物。最新代码还增加了只读 capability catalog/configuration preflight、provider usage normalization 和 transcript role 诊断。它们还没有统一任务执行协议、跨所有调用的用量账本、统一重启恢复或全页上下文协议。以下 14 页功能逐项标明现状和计划，未实现的均为建议，不代表已经部署。

配发接口的模型列表、一次 JSON-object 调用和一次 SSE 调用有负责人实测，详见 2.1。SSE 请求由真实 httpx HTTP POST 发往校内 Chat Completions endpoint；synthetic 指测试输入，不是伪造的 provider 响应。该次结果证明 deepseek-chat 对该输入返回 SSE，但不证明取消/断连恢复、其他模型、工具流式调用或整体稳定性。工具调用兼容性、JSON Schema 约束、各模型可用范围、配额和上下文限制仍待实测。DeepSeek 公共文档只能作为协议参考，不能证明校内端点支持相同能力。

## 2. 当前运行事实（本轮安装前的只读快照）

- AI 当前 enabled=true，model=deepseek-chat，Base URL 为校内 models.sjtu.edu.cn/api/v1。密钥不记录、不显示。
- 管理会计课程真实视频共 8 个：UI 显示 1 个已整理、5 个部分完成、2 个未整理。
- 最新 partial 任务错误为无法连接 AI 服务；另外 4 个带警告任务在 Phase 1 降级。
- 原安装视频页部分 AI 操作不可点击（本轮前的现象）。代码中总结与字幕处理入口受视频来源为 video_space 且支持字幕的条件限制；字幕服务也仅支持现有视频空间接入。不可用原因应显示清楚，不能通过编造字幕来绕过来源限制。
- Roadmap 记录了字幕任务、Phase 1 课程学习编排、源/产物指纹、知识库编译，也把从进程内状态迁移到持久化任务/事件层列为后续工作。

以上视频任务状态是本次评审时的运行快照，不保证之后仍相同。应用应读取当前状态，而不能沿用旧状态推断成功。

### 2.1 限定范围的 provider 检查结果（任务负责人执行；证据文件可读）

- 当前配置 enabled=true、model=deepseek-chat，Base URL 为 models.sjtu.edu.cn/api/v1；密钥不记录。
- GET /models 返回 HTTP 200，并列出实际 7 个模型。该结果只验证当次列表接口可读，不证明所有模型的 Chat Completions、JSON mode、tool calling 或 SSE 均可用。
- 普通 one-shot 请求使用 synthetic-connectivity-only 输入、deepseek-chat、max_tokens=32，约 20 秒超时；证据记为 ReadTimeout、无 HTTP 状态、无 usage。超时用量未知，不得记作零。
- JSON-object mode 请求是负责人经真实 HTTP POST 发到 https://models.sjtu.edu.cn/api/v1/chat/completions，使用 synthetic-contract-only 输入、deepseek-chat、max_tokens=128。响应 HTTP 200，耗时 9.316 秒，finish_reason=stop，provider usage 为 prompt 24、completion 6、total 30；返回 JSON 有效且业务侧 {ok:true} 合同通过。这证明该次 JSON-object 请求与本地合同校验通过，不证明模型遵循任意指定 JSON Schema，也不证明普通 one-shot 稳定。
- SSE 请求同样通过真实 HTTP POST 到该校内 endpoint，输入 synthetic-connectivity-only、deepseek-chat、max_tokens=32。响应 HTTP 200、Content-Type text/event-stream、首 token 1.133 秒、4 个事件、DONE=true、耗时 1.229 秒；provider usage 为 prompt 15、completion 2、total 17。此处 synthetic 只描述测试 prompt，provider 返回是真实网络响应，不是 fixture/mock。

- 两次生产四角色编排使用合成两条字幕、真实校内 API、临时 CourseMemoryStore、commit_memory=false。第一次 20.774 秒 completed、schema/critic 均通过，但证据脚本误读 events 键，未捕获该次用量。第二次 15.641 秒 schema 通过、Critic 未接受，系统正确回退原文并标 partial；三次实际语义调用的 provider 总 Tokens 分别 887、1525、694，合计 3106，memory 未调用。两次差异说明模型判断仍有波动，不能把单次成功当作稳定保证。证据为 live-semantic-smoke.json 和 live-semantic-smoke-with-usage.json。

因此当前可写为“负责人实测：GET /models 成功；一个普通 one-shot 超时；一次 JSON-object 合同测试成功；一次真实 SSE 请求成功”。真实 endpoint 对取消/断连、stream tool-call、多模型功能、长期稳定性、配额和价格仍为 unknown。不同测试的成功不能抵消另一请求超时。证据记录为 docs/ai-integration-2026-10-12/evidence/api-generation-probes.json 与 docs/ai-integration-2026-10-12/evidence/api-json-mode-probe.json；其中只记录 synthetic 测试输入和响应元数据，不记录密钥。

## 3. 代码现状与缺口

### 已实现

- AI 客户端对话仍使用 OpenAI-compatible 非流式 Chat Completions；可选 response_format=json_object 已加入。仅校内 Base URL 与已实测 deepseek-chat 的组合启用 JSON-object mode；其他模型与 Base URL 默认为 false。JSON-object 仍不等于结果 schema 保证。
- 客户端允许的模型名包括 deepseek-chat、deepseek-reasoner、minimax、minimax-m2.7、qwen、qwen3.8-27b。allowlist 不代表当前校内账号或接口都可用。
- AI Chat 保存会话、消息、preset 和 Agent trace；AgentLoop 只调用受限只读工具，最多 6 步、默认 75 秒超时。已有工具覆盖课程、文件、截止事项、消息和附件读取。
- Chat quick/standard/deep 当前分别选择 deepseek-chat/800 输出 Token、deepseek-chat/1400、deepseek-reasoner/3000。这是单次 completion 上限，不是任务总预算。
- 客户端有消息数和字符长度上限。字符不是 Token，不能用字符限制代替用量控制。
- 新增的 ai_capabilities 是静态任务能力目录和纯配置 preflight：读取 enabled、allowlist 模型、凭证是否已保存；不加载密钥值、不发网络请求。ready_for_request 只表示配置字段/客户端模型可构造，不等于 endpoint 连通、账户授权或具体 task permission。UI 在配置就绪但 connection_verified=false 时仍应显示“连接尚未验证”。
- 目录逐能力的 budget_status 明示 proposed_not_enforced 或 budget_not_enforced。目录数值是设计/分类信息，不代表已做 admission control 或硬预算，不得在 UI 声称这些限额已生效。
- 当前静态目录中的 runtime task_id 与本文规划 capability_id 是两套标识；目录不是统一 task dispatcher，也不授予课程/用户权限。目录状态为 materials metadata classification=implemented，videos/knowledge/AI Chat/settings=partial，其余页面=planned。视频“partial”表示已有受限字幕学习流程，不表示当前目录定义的预算已执行。
- 视频页有启动、状态轮询、重试、取消、产物打开入口；后端能获取现有视频空间字幕并创建转录任务。CourseLearningOrchestrator 有术语、纠错、critic、memory 阶段，严格 schema 与确定性 diff 校验，按字幕 hash、课程记忆、pipeline/prompt/model 版本复用缓存。
- 视频适配层现在读取 capability catalog，显示配置、视频工具或任务读取错误；bounded polling 会跳过隐藏页和重入，并避免 start/retry 的迟到响应污染新课程。课程选择和后端 transcript start 都校验视频属于所选课程。
- 视频 AI readiness 已区分 checking/ready/blocked/unknown；读取失败不冒充未配置。start/retry/cancel 返回都有跨课程 guard。
- boundedReadPool 已接入：同一资源合并新鲜读取，过期可替换一次，最多两个未完成底层读取；两个均卡住时明确提示重开应用。不会取消底层任务。播放与字幕也有独立的有界 UI 等待。
- Drawer 的「AI 用量」汇总该校对产物四个语义角色的 provider 已报告 Tokens，包括有响应的 schema 失败；未知和部分已知分开。它不包含 v1 原始整理、小结或其他页面调用，也不代表全应用总账。
- ai_classifier 新增 token usage 归一化；CourseLearningOrchestrator 将每个角色阶段的 usage、latency、finish reason、结构诊断写入 pipeline event。这不是统一 task 总账；超时或丢失响应的用量仍未知。
- CourseLearningOrchestrator 加入 terminology/correction/critic/memory 明确角色 prompt、JSON schema 文本契约、本地严格解析与脱敏诊断。json_object response_format 只约束 JSON 对象语法，不是 JSON Schema enforcement；本地 parser/schema/cue/diff 校验仍决定是否接受结果。
- Orchestrator 默认提交经过 schema/仓库校验的课程记忆增量，保存版本和证据；单 AI 来源保留 single_ai/UNVERIFIED，不能被提升为已确认课程事实。已授权学习整理可执行这一内部保存；事实冲突、外部提交或高影响判断需单独处理。
- Knowledge compiler 有 foundation/full 后台模式、阶段进度、事件与取消；Roadmap 记载 foundation 3 阶段、full 12 阶段，worker 使用 deepseek-chat、全局推理使用 deepseek-reasoner。它会写多份目标文件，且还未迁移到统一持久任务/用量账本。

### 尚未形成的公共能力

- 14 页统一的 capability request/result DTO 和 task ID。
- 统一持久任务状态、event_seq、幂等、取消、重试与重启恢复。
- AgentLoop、字幕各阶段、compiler 每个调用的聚合 Token/call 计量与硬预算。
- endpoint 网络功能探测、SSE/tool/JSON Schema preflight；现有纯配置 preflight 不是网络 probe。
- 大任务预估、队列并发控制和费用/用量提示。当前目录里的 max_input_tokens/max_output_tokens/max_model_calls 是元数据，不会拦截模型调用；尚无预算 admission control。
- 下列页面专用的 AI 入口与结果 schema。除上节列出的现有实现外，均是未来计划。
- 视频空间以外的字幕适配器及经验证的内容证据来源。

## 4. 14 个页面能力定义

所有页能力共用第 5 节的 task/API、计量、缓存和权限规则。单次指一次完整请求并完成 schema/证据校验；任务进度不是 Token 流。

### 4.1 概览 — overview.today_plan

- 输入：当前日期、用户选择的课程范围、截止/未完成 task IDs、重要消息 IDs、同步新鲜度。
- 证据：任务/消息 ID、原始时间和链接；计划只引述可定位记录。
- 价值：解释今天优先处理什么及原因。逾期判断、时间排序和分数都由确定性代码计算，不自动新建或调整任务。
- 传输/模型/预算：一次性；deepseek-chat；每 task 2,500 input / 500 output Tokens，最多 1 call。
- 现状/计划：概览已有数据，尚无页面专属 AI task；建议新增。
- 验收：每条建议可追溯到现有任务或消息；同步过期则显示过期，不把推断期限当成事实。

### 4.2 日程 — calendar.week_plan

- 输入：用户选周/课程、event IDs、截止时间、日历事实和可学习时间段。
- 证据：事件 ID、来源、时区、日期。
- 价值：解释冲突并拟定学习时间。日期计算在代码里完成；新建日历事项需用户确认。
- 传输/模型/预算：一次性；deepseek-chat；3,000 / 700 Tokens，最多 1 call。
- 现状/计划：已有日程、截止和跳转 AI prompt；Agent 无接地日历工具；建议新增。
- 验收：时区和来源明确；冲突由代码识别；只展示草稿，未经确认不写日历。

### 4.3 消息 — messages.action_extract

- 输入：用户选择的消息 IDs 或当前过滤结果、有限正文、标题/来源/时间/课程。
- 证据：消息 ID 和精确原句/段落 offset。
- 价值：提取候选截止、行动和课程关联，生成待确认 task 草稿。不得仅根据标题判断任务事实。
- 传输/模型/预算：单条一次性；批量为任务进度；deepseek-chat，单条 4,000 / 800 Tokens，首版最多 5 条。
- 现状/计划：AI Agent 已有只读搜索/详情工具，页面没有专属提取 task；建议新增。
- 验收：每项指向原文，候选与已确认分开；必须确认后创建 task。

### 4.4 作业中心 — assignments.requirement_checklist

- 输入：选定作业 ID、说明/rubric 版本、due date、用户选定参考资料版本及可选 draft。
- 证据：作业说明段落、rubric 标准、资源页码/段落。
- 价值：解释要求、生成 checklist、指出缺失证据与提交前自查。不代交、不改截止、不假装教师评分。
- 传输/模型/预算：一次性；deepseek-chat；5,000 / 1,000 Tokens，最多 2 calls。
- 现状/计划：Agent 有课程/截止/文件只读工具，但没有作业专项能力；建议新增。
- 验收：checklist 有 rubric 引用；缺字段说明未知；不触发提交或服务器写入。

### 4.5 课程资料 — materials.read_selected

- 输入：用户明确选中的 resource ID/version hash、提取文本与页/段定位。
- 证据：原始资源版本和页码、段落、cue 定位。
- 价值：提纲、解释、对比和自测；将文件作为不可信输入处理。
- 传输/模型/预算：短文件一次性，长文件按 chunk 显示后台阶段；deepseek-chat，每 chunk 6,000 / 900 Tokens，task 总 20,000 / 3,000。
- 现状/计划：AI Chat 可搜/读受限附件，Roadmap 计划固定版本“用 AI 阅读”；页面通用阅读未做。
- 验收：引用定位到同一不可变版本；格式不支持时明确告知，不默默遗漏。

### 4.6 成绩 — grades.explain_personal

- 输入：当前用户授权的个人成绩汇总、权重、选定课程。
- 证据：grade item ID、权重口径和确定性计算公式。
- 价值：解释加权结果和 hypothetical score scenario；计算由代码完成，AI只解释。
- 传输/模型/预算：一次性；deepseek-chat；2,500 / 600 Tokens，最多 1 call。
- 现状/计划：成绩 UI/adapter 存在，Agent 无成绩工具；建议新增个人范围能力。
- 验收：计算与确定性用例一致；不发送全班成绩，不以模型推断学生画像。

### 4.7 花名册 — roster.aggregate_support

- 输入：授权教师角色下的聚合人数和非身份化筛选结果；默认不传姓名/个人成绩。
- 证据：聚合查询时间和筛选条件。
- 价值：解释整体覆盖率、缺交分布或工作量模式；小样本和可识别组合抑制。
- 传输/模型/预算：一次性；deepseek-chat；2,000 / 500 Tokens，最多 1 call。
- 现状/计划：名单 UI 存在，无 AI 工具；仅建议作为服务端权限限制的教师能力。
- 验收：输出不可反推出个人；角色检查在服务端；数值与查询结果一致。

### 4.8 作业批改 — grading.feedback_draft

- 输入：教师/助教选择的提交版本、rubric 版本、课程说明。
- 证据：rubric criterion 和提交内容的精确片段/页码。
- 价值：逐标准草拟反馈供人工审核。模型不得定最终分、发布反馈或批量写回提交。
- 传输/模型/预算：一次性；deepseek-chat；6,000 / 1,200 Tokens，最多 2 calls；长文可走任务阶段。
- 现状/计划：批改 UI 有，AI grading service 无；建议新增且需角色/隐私复核。
- 验收：草稿逐条引用标准和学生材料；发布和改分必须教师再次确认。

### 4.9 课程视频 — videos.transcript_learning_pack

- 输入：视频 source ID、已核实字幕 bytes/hash、cue IDs/time range、course ID 和当前 course memory version。当前前置条件是已有 video-space 来源/字幕支持；其他来源必须先实现独立字幕 adapter。
- 证据：video ID、raw SHA-256、cue ID、起止时间。
- 价值：生成校对字幕、章节摘要、术语、时间戳复习题和“问这一段”。没有可核验 transcript 时显示不可用原因，不得生成事实性总结。课程 memory 必须带证据、来源验证状态和可追溯版本，不能将模型建议当作已确认事实。
- 传输/模型/预算：后台 task 事件反馈；各 chunk 一次性且过 schema/diff 验证后落盘。字幕文件不使用 token streaming。deepseek-chat 做限定 chunk，reasoner 仅在 preflight 可用并确需课程级综合时使用；单视频初始上限 24,000 input / 6,000 output Tokens、16 calls，涵盖重试。批量默认一部，显示预计用量后用户再选更多。
- 现状/计划：已修复播放/字幕耦合、分动作锁、不可用原因、产物补充入口、迟到响应隔离、读取恢复、完整角色契约、校对 Token 用量、旧学习材料误标失败、顶层知识点被 study_guide 隐藏的问题，以及真实旧 concepts/review_questions 格式和新 concept/statement/evidence 合同的展示兼容。统一预算与总账尚未落地。
- 验收：产物全有视频/字幕 hash/cue/time 证据；原文不可变；缓存命中不重复请求；失败、降级、警告、重试和取消状态准确；计量与 provider usage 在有返回时一致。

### 4.10 云盘备份 — backup.explain_recovery

- 输入：选择的 backup task/version 元数据、校验和、状态/错误、拟定 source/target；默认不发送文件正文。
- 证据：artifact/task/version IDs。
- 价值：解释状态并给恢复草案。覆盖、恢复、删除都要先展示 diff 并确认。
- 传输/模型/预算：解释一次性，实际备份 task 用阶段进度；deepseek-chat 2,500 / 600 Tokens，最多 1 call。
- 现状/计划：备份与任务元数据 UI 存在，无 AI 工具；建议新增。
- 验收：建议精确引用 metadata，不触发恢复；默认不将文件内容送往模型。

### 4.11 本地项目 — projects.inspect_scope

- 输入：用户选定 root、有限索引/manifest、明确选择的文件 hash。不能扫描父目录或其他目录。
- 证据：相对路径和内容 hash，不暴露主机绝对路径。
- 价值：解释结构、关联课程、制定编译计划。编译前确认 source/target、文件清单和写入计划。
- 传输/模型/预算：目录盘点一次性；编译使用独立长任务预算；deepseek-chat 4,000 / 700 Tokens，盘点最多 1 call。
- 现状/计划：项目发现/跳转编译已存在；统一 AI 检查与 task 未有。
- 验收：仅访问已授权 root；计划列举文件与目标；确认后若路径改变必须重新确认。

### 4.12 知识库编译 — knowledge.compile_or_tutor

- 输入：用户选定 source/target roots、note IDs/hash、foundation/full mode 和课程上下文。
- 证据：相对路径、文件 hash、章节定位。
- 价值：编译笔记、解释概念、从证据生成练习；材料不足就不编题。长编译需展示预算与写入预览。
- 传输/模型/预算：阶段任务进度，不流式写 Markdown；deepseek-chat 分块、reasoner 仅用于跨资料综合；初始上限 foundation 40,000/8,000 Tokens 60 calls，full 100,000/20,000 Tokens 150 calls，均为待实测初始硬上限。
- 现状/计划：已有 3 阶段 foundation / 12 阶段 full 后台 compiler、进度取消；尚无统一持久 task 和 usage；计划迁移及预估/确认。
- 验收：dry-run 有文件/阶段/预算估算；中断可恢复；文件原子写入且可审阅；取消保留已完成产物；不得静默覆盖。

### 4.13 AI 助手 — assistant.ask_with_context

- 输入：用户问题、所选 preset、显式页面上下文引用；附件范围受限。
- 证据：课程/task/file/message IDs 与其版本。
- 价值：用只读工具回答和推荐；不确定项明确标出。需改变状态时生成 proposal/preview，而非直接执行。
- 传输/模型/预算：当前一次性；SSE 仅在工具全部完成后作为最终答复候选。quick 2,500/500，standard 5,000/1,000，deep 8,000/1,500 Tokens，最多 6 calls，聚合预算。
- 现状/计划：Chat、preset、trace、只读 AgentLoop 已实现；跨轮用量与标准 envelope 未有。
- 验收：工具只读 allowlist；回答可追溯；总预算覆盖所有 rounds；最终持久内容等于校验后的完整 answer。

### 4.14 设置 — settings.provider_preflight

- 输入：当前 provider/model 标识、用户明确请求的能力探测；不能含 key。
- 证据：探测能力、时间戳、sanitized provider 错误。
- 价值：说明模型配置、做用户触发的 capability preflight；不由 AI 静默切换 provider/model。
- 传输/模型/预算：一次性 deterministic probe，尽量不使用 LLM；解释文本可选 deepseek-chat 1,500/300 Tokens，1 call。
- 现状/计划：设置和安全凭证存取/客户端创建存在；已单次实测校内 endpoint 的 JSON-object 与 SSE 请求并获得 usage，但工具调用、取消/重连、多模型和稳定性仍未知。建议新增用户主动 preflight/UI 能力矩阵。
- 验收：密钥不进入日志/任务/界面；能力状态标为 verified/unsupported/unknown 并记录时间；配置变更由用户发起。

## 5. 统一 API / task 契约

先实现内部统一服务契约，桌面端可通过 typed bridge 调用；UI 不直接访问模型服务。

### Request v1 字段

- schema_version：ai-task.request.v1
- capability_id：服务端注册的能力名。
- idempotency_key：客户端随机值；同 scope + 同规范化 fingerprint 返回既有 task；同 key 不同内容返回 conflict。
- page、course_id：上下文提示，不能授权。
- source_refs：resource_type、resource_id、version/content hash、locator。服务端重新解析来源并检查权限。
- input：intent、text、结构化 options。
- consent：scope 与用户确认时刻。来源版本变化使确认失效。
- execution：mode/model_tier/budget。客户端只能降低服务端限额，不能提高硬上限。

### Result v1 字段

- schema_version：ai-task.result.v1
- task：task_id、capability_id、status、stage/progress、时间戳、retryable、cancelable。
- result：能力专属版本化 schema；未验证结果不标 completed。
- citations：resource/version/hash/locator/quote/evidence_type。
- warnings、error category、用户可执行下一步。
- usage：known/partial/unknown、input/output/total tokens、model_calls、provider_usage_complete。
- cache：hit/miss/partial + opaque fingerprint。
- versions：model、prompt、result schema、pipeline、policy version。

可用状态：accepted、queued、running、completed、completed_with_warnings、partial、failed、cancelled、interrupted。终态由实际 task 状态决定，不能因 UI 收到空响应就假定成功。

### Events 与恢复

- 每次任务变化增加单调 event_seq；事件含 schema_version、task_id、seq、timestamp、type、stage/progress 或终态摘要。
- 数据库 snapshot 是真相；前端先读 snapshot 再订阅 event。断线按 seq replay；replay 不可用时重读 snapshot。
- 每个 provider call 后持久化 usage delta、attempt、当前 chunk checkpoint、source hash、结果 artifact IDs 和错误类别。
- 重启后 in-flight 一律标 interrupted；只有 input hash 与 model/prompt/schema/pipeline 全相同的确定性检查点才能恢复。
- 取消在下一次 provider call 前协作停止；已校验 chunk 可保留，partial 不可伪装 completed。
- Retry 建立新 attempt 并关联旧 task，只有完整 cache fingerprint 匹配的已验证 chunks 才能复用。

### Result schema 发布

request/result/tool/prompt/pipeline/policy 分别版本化。任务记录具体版本；兼容 reader 先于新 writer 部署；破坏字段含义、证据要求或安全边界必须升主版本。历史结果仍可显示。Prompt 改动影响引用/范围/动作边界时 bump prompt version 并使缓存失效。模型输出是不可信输入，所有 JSON、citation、course/version 和 diff 均需服务端校验。

## 6. Token 预算、路由、缓存与人工确认

### 预算与计量

现有 output token limits 是单个 request 上限；客户端消息字符上限不是 token 预算。每次模型调用（包括 Agent 多轮、critic、编译阶段和重试）都要记录 prompt/completion tokens。任务总量为 call-level usage 之和。上游丢失 usage 或未返回时标 partial/unknown，不能按字符猜“准确用量”。

调用前用本地 tokenizer 或保守 estimator 预扣输入和 output reserve，确认不会超过 server hard budget；返回后用 provider usage 对账。若上游超估算，记录实际值并提高之后的估算下限。费用只有在掌握当前对应模型/端点 rate card 后展示，否则只显示 Token 与请求次数。预算达到上限时停止并解释，可建议缩小范围；不得静默升档或换更贵模型。Provider rate/quota 和实际单价目前未验证。

可作为初始限制的预算档：quick 2,500/500 tokens、1 call；standard 6,000/1,200、2 calls；deep 12,000/2,500、4 calls；单视频 24,000/6,000、16 calls；foundation compiler 40,000/8,000、60 calls；full compiler 100,000/20,000、150 calls。以上是应用侧拟议硬阈值，须按真实用量调参，不是 provider context/quota 声明。批量八个视频应先显示估算和已选范围；若用户已明确要求该批生成，不重复确认。

### Router

能力 ID 集中映射模型与策略。短结构化抽取和解释优先 economical chat 模型；复杂多源规划才允许显式 deep tier。失败不得自动升到更贵模型。所用模型、tools/JSON/thinking 兼容性须基于预检结果。队列并发应服从已测 provider 限速，考虑当前本地六秒 limiter，排队状态对用户透明。

### Cache

服务端 fingerprint 至少包括：capability、request schema、规范化输入、source ID/version/hash、course memory version、model、prompt、output schema、pipeline 与 safety policy version。区分用户/课程/角色，不跨用户共享私密消息、成绩或 submission。截止、日程、unread 等时效数据使用短 TTL 或 source data version。授权决策不缓存。不得把 key 放进 hash 输入或回传。partial 仅能复用逐 chunk 通过校验的结果。

### 长录像的预算校准

24,000/6,000 Tokens、16 calls 是拟议的短任务起点，不能保证完整长录像处理。历史只读盘点的18份校对工件共有335 chunks；该工件数不等于唯一视频数。新的合成两 cue 单 chunk 测试已消耗 2,716 input / 390 output / 3,106 total Tokens、3个语义调用；不能将这一单样本当成所有 chunk 的固定成本。

部署 admission control 时，先本地解析实际 chunk 数 N，以正常路径 3N+1（术语/校对/复核，加课程记忆）和配置的重试余量预估调用；为每个 chunk 加入合同、上下文和最大输出 reserve，再与用户设置的任务总预算比较。超预算时在请求前明确提供缩小时间范围/分段处理方案；完整模式须给足经批准范围内的预算，不能在16 calls后静默声称整节完成。缓存命中的有效 chunk 不重新计费，未知失败用量保留未知。当前目录的固定 budget 仍是 proposed_not_enforced，不能用于生产验收声明。

### 授权与内部自动化边界

- 当前课程只读事实可在当前权限范围内用；涉及正文、成绩或提交资料的首次 task 要显式展示发送范围。
- 推荐保持 draft，带证据、时间和不确定性。
- 外部或高影响变更先提供 action proposal，包含精确 payload 和 source fingerprint；确定性 service 校验授权、权限和当前状态，执行幂等写入。已有明确授权不重复询问。
- 提交作业、发消息、发布批改、改分、删除/恢复/覆盖及改变连接身份需要相应明确授权。已选范围内的批量生成、内部产物和未验证课程记忆保存可依用户指令自动执行，记录来源、用量和版本。
- Model 从不读取、输出或日志记录 provider credential；不允许泛化的 DB/UI write tool。

## 7. 流式与一次性决策

| 工作 | 建议 | 原因与限制 |
|---|---|---|
| JSON 提取、分类、带 citation 的结构化结果 | 一次性 | 完整 schema/evidence 校验后才可显示 completed。 |
| 字幕纠错、术语、critic、memory | 每 chunk 一次性，阶段任务进度 | 通过严格 diff/schema 后才写 artifact/memory。 |
| 视频 batch / study pack | task events + 每个模型 chunk 一次性 | 有可恢复 checkpoint 和取消；不能把半成品当最终字幕。 |
| 知识库编译/文件生成 | task progress + 一次性模型调用 | 校验后原子写文件，不流式写 Markdown 成品。 |
| 消息、作业、资料的短解释 | 一次性 | 易于限预算、校验、缓存。等待时展示 task pending。 |
| AI Chat 自由回答 | SSE 候选：tools 全完成后，只流最终可见答案 | 只在校内端点完成预检后启用；不流 reasoning/tool args，结束后持久化同一最终文本。 |
| Tool call/arguments | 不展示流 | 参数需完整解析、allowlist 和 schema 校验后才能执行。 |
| Settings capability probe | 一次性 deterministic | 不让模型自称某 feature 支持。 |

DeepSeek 官方 Chat Completions 文档描述 stream=true 返回 SSE delta 和终止标记 [DONE]，也描述流结束前 usage chunk 的协议；这仅是公网协议参考，不证明校内 endpoint 支持。[DeepSeek Chat Completions API](https://api-docs.deepseek.com/api/create-chat-completion/)

如果后续启用下游 streaming，需定义独立于上游的事件 seq、heartbeat、超时、断连、背压、取消、usage 对账、最终持久化和重连 replay。Provider error body 不透传。SSE 不支持时自动退回 one-shot，产品功能仍可用。

## 8. Provider preflight

代码已实现纯配置检查：不加载密钥值、不发网络请求；unknown connection 不应显示为连接成功。任务负责人已经执行 2.1 的限定范围真实接口测试。后续若扩展 preflight，对明确指定 endpoint/model 做最小化、用户触发的追加测试，返回 sanitized 结果：

- connectivity、authentication category、指定 model 可用性。
- 普通非流式调用与 usage shape。现有一次普通 one-shot 约 20 秒 timeout（usage unknown）及一次 JSON-object 成功，须继续区分运行波动与模式差异。
- 仅在功能需要时测试 tool call round trip、structured JSON。
- SSE framing、[DONE] 与最终 usage chunk 已有一次真实调用成功；取消/断连、重连/replay 和多轮运行仍需单独测试。
- 超时与限流用保守低频检查。
- App launch 不自动探测，不读回/显示 key，不保存原始 response 正文。结果仅存 capability、model、时间、verified/unsupported/unknown 与 sanitized error category。

任务负责人提供的官方入口：[SJTU Campus AI API guide](https://claw.sjtu.edu.cn/guide/sjtu-api/)。本轮工具无法读取该 URL，因此校园网/VPN要求和 deepseek-chat/reasoner、qwen/qwen3.8-27b 型号信息是任务负责人提供的当前背景，不由本评审独立核实。相关校内 OpenAI Chat Completions 配置也见 [SJTU OpenClaw API integration guide](https://claw.sjtu.edu.cn/guide/api-agent/)，但它不能证明每个 endpoint feature 已开放。

## 9. 阶段实施顺序

### A. 视频闭环 + preflight + Token 计量（首期）

1. 每条录像显示 source support、字幕是否存在、AI配置/preflight状态、job状态及不可用原因。明确区分配置检查失败、未配置、连接未验证和连接/模型请求失败。
2. 只启用现有视频空间字幕路径；Canvas/local 只有新增真实 adapter 和明确授权后才能宣称支持。
3. 将 transcript jobs 映射到统一 task v1；不破坏当前状态、重试、取消和 artifact UI。
4. 记录每次 provider call usage，聚合到 task；加 max calls/token 硬上限和预算前估算。
5. 维持 memory 的课程隔离、证据校验、UNVERIFIED 来源和版本追踪；不将内部记忆增量提升为已确认事实。
6. 受限 API 预检不能代替应用端到端验收；真实视频生成需在用户选择范围内执行。Fake tests 与真实服务验收分开报告。

### B. Task 平台

持久化 task/event_seq/attempt/fingerprint/checkpoint/usage/result refs；实现 snapshot、replay、cancel、retry、restart recovery；之后迁移 token-heavy 知识库 compiler；集中 model router、预算和队列；实现 cache invalidation 和 provider fake contract tests。

### C. 全页 grounding 能力

按低风险优先消息抽取、选中文件阅读、作业 checklist、日历 plan、overview 排序，再做个人成绩。花名册/批改先做 role/privacy review。云盘和本地项目严格局限于选定 metadata/root。每一项完成后再标记已部署。

### D. SSE 决策

校内 deepseek-chat endpoint 已有一次真实 SSE 请求成功记录，但这不等于应用 stream 已实现或具备生产恢复能力。先采集 one-shot latency 分布并实测取消、断连、usage 与重连；只有 AI Chat 最终答复延迟值得改造时再集成。Tools 结束前不输出 stream；回答完成后落库；断连产生 partial/cancelled task。其他严格式/文件生成/字幕修正维持一次性调用。

A–D 是实施顺序，不缩小长期目标：完成后 AI 仍需覆盖 14 页并为学生提供基于课程上下文、可追溯的内部学习决策支持。

## 10. 验收标准

### 视频

- 八个视频每条均有明确能力/不可用原因，当前课程切换不会对旧 source ID 启动任务。
- capability preflight 不得将配置 ready_for_request 说成连接/权限已验证；unknown 不得冒充 not configured。异步错误文案与按钮策略保持一致。
- cancel/start/retry 迟到结果均不能串入其他课程的前端状态；服务端仍以 job/course/source 关系作为授权校验依据。
- 一次等待超时后的重新检查行为有明确定义；不可取消的底层调用不会无限阻断 retry，也不会无界复制悬挂调用。
- 一条已整理、五条部分完成、两条未整理按真实 task 状态显示；Phase 1 降级/警告不等于干净成功。
- 网络错误和其他 error category 区分；retry 不重复有效 chunks，也不超过已同意预算。
- 每个产物含 video ID、raw hash、cue/time、model/prompt/pipeline/schema 版本。
- 没 transcript 不生成摘要；取消/重启/断线可读回正确状态；只有 fingerprint 匹配的 checkpoint 可恢复。
- usage 在 provider 有返回时与调用累计吻合；缺失时明确显示 partial/unknown。
- 任何 key 均不出现在 task、trace、event、log、UI、cache fingerprint 和 error text。
- 原有播放、字幕读取、重试、取消、产物打开保持可用。

### Task / Provider

- Fake provider 覆盖 tool calls、malformed/truncated JSON、timeout、429、请求后断连、无 usage、重复幂等键、cancel、恢复。
- Event replay 与数据库 snapshot 无状态丢失；client 不能提高 server budget 或伪造角色/课程权限。
- Schema tests 拒绝无证据 citation、错误课程、extra unsafe fields。
- 内容/模型/prompt/schema/pipeline/memory/policy 任一变化使缓存失效。
- Preflight 由用户发起并按功能、模型分别记录 verified/unsupported/unknown；现有一条 SSE 成功只证明该次 deepseek-chat synthetic-input 请求，不证明 tool streaming、取消、其他模型或长期可用。
- 每个 capability 的目录预算如未有服务端 admission control，UI/日志必须标为 proposed/not enforced，不得展示成实际硬限额。

### 产品

- 每页先有明确 capability ID、选择输入、locator、模型策略、总预算、输出 schema 和人工确认边界，才进入实现。
- 所有建议显示证据与不确定性；隐私/角色权限服务端执行。
- “计划中/不支持/未预检”不能显示成正在工作。
- Streaming 不可用时，同一个 task 降级为 one-shot，核心功能不失效。

## 11. 来源

- 当前实现/路线：STUDENT_PRODUCT_ROADMAP.md、dashboard_service、ai_classifier、AgentLoop/tool registry、TranscriptService/CourseLearningOrchestrator、KnowledgeCompiler、AppShell 与 VideosView。
- 第 2 节运行事实：由本任务负责人于 2026-10-12 提供，本文作者未复测。
- 第 2.1 节 provider 检查结果：任务负责人于 2026-10-12 通过真实 HTTP 请求执行；synthetic 指 prompt 输入，provider 响应来自真实 endpoint。证据在本目录 evidence 下，范围与局限逐项注明。
- [SJTU Campus AI API guide](https://claw.sjtu.edu.cn/guide/sjtu-api/)：任务负责人提供的校内指南链接；当前工具无法抓取页面。
- [SJTU OpenClaw API integration guide](https://claw.sjtu.edu.cn/guide/api-agent/)：相关校内 OpenAI Chat Completions 配置参考。
- [DeepSeek Chat Completions API](https://api-docs.deepseek.com/api/create-chat-completion/)：SSE 与 Chat Completions 协议参考，不代表校内服务已支持。

## 11. 本轮交付范围

已落地：视频操作解耦和有界读取恢复、配置四态与错误提示、课程隔离、旧产物阅读/补充入口、完整四角色合同和校内 deepseek-chat JSON-object、本地质量回退、语义角色 Token 用量展示。原生应用的播放、字幕读取、旧产物和未知用量需按验收记录判断。

仍为设计：14页新能力的统一 dispatcher、跨流程持久 task/event 总线、全应用 Token 总账、预算硬拦截、全部页面专用 AI 功能，以及产品端聊天 SSE。视频任务写操作的底层 bridge 若永久悬挂，当前保留该操作锁以避免重复生成；其最终恢复应由持久任务和幂等键解决，不能仅靠 UI 超时放开重复提交。

完整验收记录见 README.md；各页面预算、传输、证据、输入/输出合同与实施顺序见前文。
