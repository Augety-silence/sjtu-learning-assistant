# 知识目录合并与阅读区减负（2026-11-21）

## 现场与范围

- Mac正确目录 `/Users/bytedance/Projects/sjtu-pr-audit-20261101-5c3127d`，沙盒 `/workspace/ws1/sjtu-history-Oyj1dB`。开始分支均为 `aime/1793618241-ai-document-multimodal`，两端工作树干净。
- Mac起点HEAD `9392c7bf38a3ade06b37ba4627449a85c48d1379`；沙盒起点HEAD `c0fcc539d58892a1f70f166a2bbec1316409cf5a`；共同tree `c71fbc3a08324fe3bc90db4de500760eb6251012`。历史不同但内容一致，保留各自历史，通过受控index增量整合、没有reset/rebase。
- 起点完整status、log5、diff统计／全文／cached、fetch及HEAD/origin核验已执行。Mac origin/main起点及结束fetch均为 `d71c19bd34c4d53dba1201aa63999d77ae94a63c`。沙盒没有origin/main ref，不伪造。开放PR起点／结束均为0，无开放项需要核查检查、依赖、冲突或审查。
- 本轮为本地修复与已授权App替换；未推送、创建／合并PR、删除远端分支、改版本或发布Release。未进入、修改或搬运两个禁止旧脏树 `/Users/bytedance/Projects/sjtu-learning-assistant` 和 `/Users/bytedance/Projects/sjtu-m9-nightly-20261025`。未删除原始课件、字幕、人工稿或旧App回退。

## 发现、证据与处理

1. `CompiledKnowledge.search`原文件夹键为实际source_id和路径，同一路径课件／字幕分别建树。生产库只读核验正好112个不同文档ID，94课件＋18字幕，两棵同名学期树；并非字幕物理重复。新增 `knowledge_navigation.navigation_scopes`，只对两个系统来源按已知学期与课程身份无冲突的既有目录生成虚拟查询scope。进入该目录能查询两类来源；原source_id、文档opaque ID、正文路径、引用与阅读记忆不变，虚拟scope不能用于文件定位。显式物理来源查询仍保持单来源，用户项目不合并。
2. 全目录先判断冲突，再关键词／分页，避免部分查询误合并。真实不同学期ID或同路径不同课程ID保留隔离；未知身份只随原路径浏览，不填term_id/course_id，严格身份查询仍排除。生产目录111份有term:30，1份“建模与优化”PDF缺身份；没有按名称猜它属于哪个课程。修复后生产目录是一个 `2026-2027 Fall` 根，112份全部保留，无索引问题，无截断。
3. `CompiledKnowledgeBrowser`原根浏览状态仍渲染固定高度右阅读栏、toolbar和欢迎占位。改为未选文件时仅全宽目录；阅读时保留目录／正文双栏、窄容器单栏，收起后返回根目录仍可导航。删除无引用的welcome DOM与CSS及citation常驻样式，不删除阅读组件、文件或错误恢复操作。
4. 收起／展开目录图标从默认粗大图改为16px、1.5线宽，36px点击目标保留。“定位文件”改为“在本地显示”：真实后端按受控路径调用Finder reveal，而非重新打开文档。按钮始终传文档真实来源和ID。
5. 来源、章节、编译状态移入独立 `KnowledgeDocumentInfo` 的hover／focus提示，唯一aria-describedby ID，Esc可关闭；正文不再常驻这些注释。编译错误alert仍显式可见，不通过隐藏错误美化界面。全文自由选择复制、逐文档阅读进度、分页追加、世代隔离和AI读取契约没有改变。

## 修改与整合顺序

- 后端：`compiled_knowledge.py`、新增 `knowledge_navigation.py`；新增真实临时文件跨来源回归 `test_knowledge_navigation.py`。
- 前端：知识浏览组件／样式、新提示子组件与浏览回归；更新既有合成知识阅读夹具、构建入口选择，不覆盖生产dist。
- 验收：新增本机只读目录脚本（SQLite正常mode=ro，活跃WAL不使用immutable，不迁移或恢复DB）与原生WKWebView脚本；最后更新产品路线和此交接记录。
- 功能Mac提交 `6e7bacb07705f4c8ec1aaa9b24b840180543f9ec`；沙盒对应 `b03948574c73a48dc7a3a4d63e5ba22858cf7e0e`；共同功能tree `0abd1581c898f34512dc89dc695bbf511536f22e`。

## 验证

- 定向Python 45 passed／1 skipped／18 subtests；定向前端27 passed。
- Mac完整pytest：1207 passed／1 skipped／499 subtests；unittest：1133 tests／1 skipped，OK。完整Vitest：63文件／576 passed。
- 沙盒完整pytest：1205 passed／3 skipped／499 subtests；unittest：1133 tests／2 skipped，OK。首次全量仍受no_proxy环境污染，20个InvalidURL失败；清除全部代理变量（含no_proxy）后完整重跑通过，未删测试或豁免。
- 两端Biome168文件、TypeScript＋Vite生产构建、compileall、Python/npm许可证（152个锁定生产依赖）、项目秘密模式扫描通过。7个非UTF-8文件未做文本秘密扫描；本轮没有重跑GitHub CI或gitleaks。
- 原生WKWebView隔离验收：根目录全宽、无空阅读栏／欢迎占位；1050／760／600窗口六份资料可切换、工具栏无横向溢出、正文无常驻来源注释、图标16px细线；tooltip焦点显示及Esc关闭、字幕原source定位、收起目录后阅读宽980px、返回根可导航。首次夹具因原生Bridge注入发生加载超时，补上同一合成数据的原生Bridge后重跑通过；未修改产品Bridge来迎合测试。
- 原生验收全部合成数据，不是全部真实课程学习过程。生产目录核验为真实本机只读112资料计数与路径／ID一致性，不将它描述成真实课程多模态内容质量验收。本轮未调用学校模型，不触发全库编译，不对学习资料作写入测试。
- 非阻断：jsdom媒体未实现、Mac多线程fork弃用、Vite主chunk超500kB、图标生成Pillow弃用提示。

## 构建与部署记录

- 起点安装App 1.8.12，主程序SHA-256 `ba52bc6a738e8b90bcce5121e2f4630a4c373147adb5d9f6b9e6d601791e624c`，PID73413。
- 精确功能Mac提交git archive到 `/private/tmp/sjtu-knowledge-navigation-build.0uXnPt`，独立生成图标、生产前端与PyInstaller构建。候选通过codesign deep/strict及包内plist／第一方个人路径／秘密／禁止模块检查。ad-hoc签名，未Apple公证。
- 候选版本1.8.12，主程序SHA-256 `bf4d498ba8d9f2415dce1b62df173c33a0900722da2860a48a91f37e893d8a63`。
- 新回退 `/Users/bytedance/Projects/sjtu-app-rollbacks/20261121_154413-knowledge-navigation/SJTU Learning Assistant.app`，签名／旧SHA核验通过，保留所有旧回退。正常Apple事件退出旧主程序，等待后台同步子进程88610自然结束，没有强杀或中断同步。随后将旧安装移动为同目录 `previous-installed.app`（可恢复），ditto新包到 `/Applications/SJTU Learning Assistant.app`。未删除任何旧回退。
- 安装后deep/strict签名通过，版本仍1.8.12，主程序SHA-256与候选相同：`bf4d498ba8d9f2415dce1b62df173c33a0900722da2860a48a91f37e893d8a63`，启动PID89288。安装后真实只读目录再次核验112／112个不同ID、单一学期根、无issues、无截断。此次未改变数据库schema，无需执行新迁移。
- Mac验证日志 `/private/tmp/sjtu-navigation-validation.4559YA`；原生夹具 `/private/tmp/sjtu-knowledge-audit.ryZsl7`。
- 结束功能检查点：两端仍为原功能分支且工作树干净，Mac HEAD为上述6e7bacb完整哈希，沙盒HEAD为上述b039485完整哈希，功能tree相同。结束origin/main与起点一致；开放PR仍0。最后另作只含本验收记录的本地提交，不改变已部署功能，最终提交哈希随交付回执报告。

## 限制与后续

- 缺失课程身份的1份PDF没有自动修复数据库关联；可以浏览，但不能当作严格学期／课程范围的已核验资料。未来需基于原Canvas文件ID修复关联，不能仅凭目录名称。
- 已知真实身份冲突的同名目录仍保留隔离，这是避免混课程的保护，不全局按label去重。
- Windows真机路径／拖拽／网络盘、真实云盘登录与写入恢复、大目录性能、全部课堂多模态内容质量未在本轮执行。
- 不设置每日API限额，不改变学校限速，不改后台运行策略或学习库数据结构。
