# 课程知识稿 v2：来源层级、完整编译与验收

本次工程范围：资料入口职责区分、课程／课时文件夹收纳、PPT／PDF 完整图文编译及可追溯知识稿。不更改版本号，不自动推送或发布 Release。

## 现场事实与审计依据

2026-11-17 正确工作树：`/Users/bytedance/Projects/sjtu-pr-audit-20261101-5c3127d`。
开始分支 `aime/1793618241-ai-document-multimodal`，HEAD `3ac9c87b9bfa82caa5fa14c2e6d726767bedd410`；fetch 后 origin/main 为 `d71c19bd34c4d53dba1201aa63999d77ae94a63c`。主工作树干净，开放 PR 为 0。相较上轮记录没有变化。

| 问题及位置 | 触发条件与影响 | 处理依据 |
| --- | --- | --- |
| AppShell、LocalProjectsView 与 KnowledgeCompilerView 的入口重复 | 两个页面均默认显示同一浏览器；无法区分资料登记和知识阅读 | “资料项目”仅管理外部项目，按需查看指定项目结果；“课程知识库”集中编译／阅读 |
| CompiledKnowledge.search 与 CompiledKnowledgeBrowser 平铺列表 | 分页仅 20 条，直接按当前页分组会漏掉其他课程 | 全量目录计数独立于文档分页，默认先显示课程文件夹，支持按原来源目录筛选 |
| CompiledKnowledge._catalog 项目递归枚举 | 页稿、镜像和旧版本都可作为独立知识稿 | 同一源课件选择当前契约／最近版本，不展示内部页稿和历史副本；磁盘不删除 |
| CompiledKnowledgeBrowser.read 替换前文 | “继续阅读”后上一段消失，且“读到 N 字”被误认成编译进度 | 续页追加并保持原字符偏移；明确区分阅读分页与编译完成度 |
| DocumentCompiler 只拼接逐页原文与 AI 审阅稿 | 所有页面即便完成，也缺少可学习的讲义脉络 | Qwen 页解读 + DeepSeek 分组讲义，保持授课顺序，验证每组指定页全部覆盖、引用及栏目结构 |
| DashboardService._run_knowledge_compiler_ai 错误包装 | 本机 41 份课件只记录 DashboardError，无法定位接口原因 | 已分类 AI 错误保留安全文案；空响应／长度截断不发布；扩大知识编译输出预算与专用读超时 |
| PPTX 隐藏页限制 | 文本提取包含隐藏页而默认 PDF 导出不包含，导致错配 | LibreOffice 导出明确包含隐藏页；仍验证渲染／文本总页数一致，不移除宏、嵌入对象或外链安全闸门 |

开始时本机登记 91 份课件：37 完成、53 失败、1 待续编。错误包含 41 份通用 DashboardError，另有隐藏页、嵌入对象与外链限制；这些是当时的现场值，不代表本轮已完成全部 91 份重编。

## 编译契约

契约标识 `course-lecture-v2`，应用内约束在 `sjtu_learning_assistant/knowledge_prompts.py`。

1. 整页图像与提取文字共同输入 Qwen；保留章节内容、条件、推导、公式、图表关系和例题，缺失项必须具体记录。
2. 每页响应必须有页面主题、知识点、公式与图表、例题与易错点、待确认项。拒绝空响应、泛泛拒答和结构缺失。
3. 每组最多四页并受输入长度限制；DeepSeek 返回 JSON 页码列表及完整 Markdown。全部指定页必须出现对应来源引用，栏目保持顺序。
4. 完整新稿从讲义脉络开始，原文与逐页证据放在后面。全部页面和知识组验证后才写 `document.md` 及完成状态。
5. 每个页面和知识组持久化断点。额度耗尽、取消或模型响应不完整时，不将部分稿标成完成；恢复复用已确认结果。
6. 契约升级时保留旧镜像直至新稿完成。替换前保存按内容哈希命名的历史副本；未登记／人工编辑的文件不覆盖。

“完整知识稿”表示页面覆盖与结构检查通过，不表示教师或人工已核实全部 AI 推理。不能据此隐去实际不可辨认项。

## 本地结果与 AI 调用

自动课件输出位于归档根的 `Knowledge Markdown/Courses/<原学期>/<原课程>/<原课时或资料文件夹>/<原文件名含扩展名>.md`。每份源文件一份镜像，保留 PPT 与同名 PDF 的区分；不会根据标题猜造课时目录。

内部版本在 `90_Inbox/Documents`；旧镜像副本在 `90_Inbox/Previous`。原始课件保持不变。

App 的“课程知识库”先显示文件夹，再显示所选文件夹的讲义。搜索覆盖完整正文。内置 AI 与 stdio MCP 的 `list_compiled_knowledge`、`search_compiled_knowledge`、`read_compiled_knowledge` 读取同一目录，仍保持只读和分页边界。

## API 事实与验证边界

官方文档 `https://claw.sjtu.edu.cn/guide/sjtu-api/` 明确 Qwen 为视觉／文本模型，DeepSeek 为文本／推理模型。2026-11-17 真实 `/models` 返回 qwen、qwen3.8-27b、deepseek-chat、deepseek-reasoner、claw；没有 MiniMax，不能根据用户提供的名称假定其可用。

真实合成图表探针：qwen 与 qwen3.8-27b 均 HTTP 200、finish_reason=stop，正确读取三年三个数值；分别用量 550 和 544 token。此探针不是实际课件验收。

真实课件验收使用独立新目录，记录来源 SHA-256、实际页数、接口用量、文档 SHA-256 与恢复调用数。数据库 `课程作业/9/Lab 7_1.pptx` 三页全部完成，知识稿 13599 字符；宏观经济学 `其他/national income.pdf` 一页全部完成，知识稿 4898 字符。两份再次编译均为零 API 调用。首次 PPT 第三页 JPEG 输入连续等待响应头超时；同页规范化 PNG 输入实测成功，因此只保留 JPEG 作为证据，接口发送 RGB PNG（最长边 2048、上限 4 MiB），客户端验证 PNG 魔数。不能据此断言所有 JPEG 输入均失败。

最后续编批次共四次真实调用（PPT 剩余一页及讲义、PDF 页解读及讲义），均 HTTP 200、finish_reason=stop，用量合计 14475 token。验收根为本机 `dist/knowledge-v2-api-20261117-151718`，包含 `verification.json`。两份完整稿通过正常同步发布事务进入原课程目录，旧镜像留历史副本；不将验收缓存当作所有课件已完成。

仍需真实 Windows／真实课程专项检查：LibreOffice 与 PDFium 的隐藏页顺序、字体与公式渲染、嵌入对象／外链的安全导出。未经验证不宣称所有平台及所有课件均成功。

## 资产保护

两个旧脏工作树仅只读核验，未执行 reset、clean、rebase 或搬运。开始状态分别为 98／18 项修改；状态哈希分别为 `c8fd354abc867f36a493ad49776ee5bd40850d46c486cbf5fad3f0229325dfa2`、`b28d58e1a1928a6b5a75e6cf8863b7c3da66a7c88cc541433afb7fe8e225aa8e`。

沙盒用户未跟踪的 `docs/course-markdown-acceptance-2026-11-14.md` 未修改、未暂存、不会包含在本轮代码交付快照中。API 凭据不写入仓库、编译清单或测试记录。

## 全量验证及交付边界

2026-11-17 沙盒全量：pytest 1066 passed、2 skipped、442 subtests；unittest 993 tests、1 skipped；Vitest 58 文件 592 用例通过。本机 macOS Python 3.12 全量：pytest 1067 passed、1 skipped、442 subtests；unittest 993 tests、1 skipped；Vitest 58 文件 592 用例通过。平台条件跳过差异不混算。最后预算说明文案的 Biome 换行错误已按格式规则修正，随后复验；TypeScript／Vite、compileall、Python／npm 许可证、秘密模式扫描及 diff-check 通过。秘密扫描有七个既有非 UTF-8 文件不纳入文本扫描；Vite 主 chunk 大于 500 kB、macOS 多线程 fork、jsdom 媒体方法及测试临时目录警告仍属非阻断提示。

不声明 Windows 真机、所有 91 份课件或全部 AI 推导已验证；失败、待续编和具体待确认项仍展示。每日额度不自动提高。仅本地提交与本地 App 更新，版本保留 1.8.11；无 push、PR、合并、远端标签或 Release。App 从精确提交构建、deep strict ad-hoc 验签后部署，新建时间戳回退，不删除旧回退；不是 Developer ID 签名或公证。
