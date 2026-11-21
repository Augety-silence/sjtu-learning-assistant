# 视频同行控制、macOS后台运行与Token用量（2026-11-21）

## 现场与授权范围

- 正确Mac目录 `/Users/bytedance/Projects/sjtu-pr-audit-20261101-5c3127d`；开发沙盒 `/workspace/ws1/sjtu-history-Oyj1dB`。
- 两端开始分支 `aime/1793618241-ai-document-multimodal`，工作树干净。Mac HEAD `dd9bb9f582828722766edfbe50887fea455f5654`；沙盒 HEAD `85000b87fae59982ec591b71a33e3aba4412460c`，共同tree `42de5d5df7ef69654d59640da0a39869e171f028`。两端历史不同但内容相同，保持各自历史、不reset或rebase。
- 已执行status、最近5提交、diff统计、完整diff、cached diff、fetch和HEAD/origin核对。Mac origin/main `d71c19bd34c4d53dba1201aa63999d77ae94a63c`；沙盒没有此ref，未伪造。开放PR为0，无开放项需核对依赖／冲突／审查。
- 开始已安装App 1.8.12，主程序SHA-256 `e7f4146b13b5ff6fa6fc0de204f111b34fe0e905a9d4e196699ec6491ddc5c86`。旧记录PID62039已不存在，不作为运行事实。
- 本轮授权为本地功能改进与App替换。没有推送、PR创建／合并、版本变更、远端分支删除或Release发布。未进入两个禁止旧脏树，未触碰其修改；未删除课件、字幕、人工稿、旧App回退。

## 审计证据与处理

1. 截图顶部实际是VideosView的课程选择与来源Tabs；不是上轮移除背景的资料Tabs。`VideosView.tsx`的来源仍启用indicator，`index.css`将工具栏设为单列。改为正常流双列、紧凑课程选择，移除来源移动背景，全部来源和键盘语义保留。1050／760宽原生窗口同行且标签无覆盖；600以下按可读性换行，避免硬塞溢出。删除旧单列断点与无效跨行规则，既有SJTU蓝视觉不另换主题。
2. `desktop_app.py::run_desktop_app`原最后窗口关闭结束webview循环，finally停止DesktopScheduler和服务。Mac原生窗口delegate现在关窗orderOut并暂停媒体、保持窗口／服务；应用delegate新增Dock重开，继承原applicationShouldTerminate，明确退出／关机不拦截。没有关闭事件假取消标志，没有另起常驻收费进程，不修改系统登录自启。Windows保持原退出行为，尚未实现托盘方案；睡眠／关机不能持续执行任务。
3. 原`ai_tasks.py::usage_summary`只累计最近500个任务且未保存缓存字段，不能代表所有课件／字幕调用。新增独立ProviderUsageLedger，不改变任务预算账本；模型共同HTTP入口观察每次实际尝试，覆盖普通、工具、流式、分类、知识编译、字幕与App后台分类。UI只读此新账本，不与旧账本叠加，不补猜历史统计。
4. 每个流式请求含最终usage只记一次；stream_options不兼容回退分别记录两次HTTP尝试，未报告usage保留未知。即使正文格式无效，已经返回的计数仍入账。记录失败不让成功请求重新发送，只记录异常类型。存储只有随机ID、模型、计数、时间，无Base URL、API Key、提示词或学习正文。
5. 缓存兼容OpenAI nested cached_tokens、DeepSeek hit及cache_read别名。缓存作为输入子集，不加到总量；字段缺失／矛盾不标零。总量只采接口报告，不根据估算预算造数。仪表盘分四项，今日／近7天／升级以来，30秒无重叠只读刷新，离开设置释放计时器，错误保留重试。不统计价格，不设置每日API上限，学校原限速仍有效。
6. SQLite增加可回放的0022迁移，仅新增计量表与索引。旧学习与任务表不重建。回退到旧App时应使用迁移前数据库备份并先判断新数据，不能直接把新schema降级或覆盖用户数据。

## 修改与整合顺序

- 视频：`VideosView.tsx`、`index.css`、`aura-pages.css`及VideosView回归。
- 后台：`desktop_app.py`及`tests/test_background_lifecycle.py`、原生Cocoa验收脚本。
- 计量：`ai_classifier.py`、`provider_usage.py`、models、desktop_database、0022迁移、dashboard_service、sync_data_to_db；仅加计量，不改变调用额度策略。
- 设置：`TokenUsageDashboard.tsx`、SettingsView、api.ts及独立用量UI回归。刷新失败不影响设置加载和保存。
- 最后加入合成原生夹具、真实API隔离探针、升级与计量回归；更新产品路线及此记录。两端通过index增量应用并核对tree，不覆盖脏树。

## 验证结果

- 定向Python 76 passed／34 subtests，新增后台2 passed；定向UI 53＋4 passed。
- 沙盒pytest最终1201 passed／3 skipped／497 subtests；unittest1129 tests／2 skipped。首次pytest受沙盒代理环境污染，有20个InvalidURL失败；清除代理后完整重跑通过，没有删除或跳过失败用例。
- Mac完整pytest1203 passed／1 skipped／497 subtests；unittest1129 tests／1 skipped。
- 两端完整Vitest63文件／574 passed；Biome167文件通过；TypeScript及Vite生产构建通过；Python compileall通过；git diff --check及cached diff --check通过。
- Python与npm许可证检查通过（npm152个锁定生产依赖）；秘密模式扫描通过，7个非UTF-8文件未扫描。未重跑GitHub gitleaks或平台CI，不称其为本轮验证。
- 原生Cocoa：实际关窗隐藏、隐藏时调度任务继续、Dock回调重开、明确退出允许，测试进程正常退出0。只用合成HTML和短周期调度，不接触学习库。
- 原生WKWebView：实际VideosView合成18节列表、1050／760宽顶部同行、来源无indicator且不越界、阅读页签不遮盖；仪表盘显示合成已报告数；字幕详情原文和层级回归通过。首次夹具未为新仪表盘提供响应，修正隔离夹具后完整通过，不掩称真实课程观看。
- 真实交大deepseek-chat：使用已有安全存储凭据、两次合成文字请求，普通usage输入102／输出4／总106；流式输入102／输出4／总106（reasoning0）。新账本合计输入204／输出8／总212、请求2。两次缓存均未报告，显示未知，不宣称命中为0。计数写独立临时库，不写生产学习DB；未把任何真实学习正文送作测试。
- Mac日志 `/private/tmp/sjtu-toolbar-validation.oaLBjk`；原生界面夹具 `/private/tmp/sjtu-toolbar-audit.5bvloX`。非阻断提示：jsdom媒体未实现、Mac fork弃用／资源回收提示、Vite主chunk>500kB。

## 限制

- 本机升级后计数不是整个学校API账号历史或其他Mac累计；旧调用不回填。缓存字段由上游决定，缺失时无法可靠测得。
- 不声称全部真实字幕总结完成、所有课程自动生成课件PDF成功，或Windows真机已验收。
- 后台只表示Mac关闭窗口后保持运行；明确退出、系统关机与睡眠不运行。没有擅自安装或更改登录自启任务。
- 仪表盘是运行情况观察，不是强制预算；原有学校10RPM与有界重试不取消。

## 精确提交、构建与部署

- 功能Mac提交 `baca0d3a4ecb052224934f9859bf4219d9b23d37`，沙盒对应 `e8f54540c30b990106fef9a50004a0e129f0f56b`，共同tree `9e18cf14aa8abc0711531700f1e4a6fbb20f7399`。提交用途为本地后台生命周期、视频同行与模型计量修复。
- 从上述精确Mac提交git archive到 `/private/tmp/sjtu-toolbar-build.4ML8sJ`，独立生成图标、前端生产构建与PyInstaller打包；没有用脏工作树构建。
- 候选与安装后App通过codesign deep/strict和包内plist／第一方路径／秘密／禁止模块检查；使用ad-hoc签名，未Apple公证。
- 已替换并打开 `/Applications/SJTU Learning Assistant.app`，版本仍 **1.8.12**。主程序SHA-256 `ba52bc6a738e8b90bcce5121e2f4630a4c373147adb5d9f6b9e6d601791e624c`，运行PID **73413**，安装后32秒仍在。
- 部署前核对旧App主程序SHA与起点相同；新回退 `/Users/bytedance/Projects/sjtu-app-rollbacks/20261121_140955-background-token/SJTU Learning Assistant.app` 经签名／SHA核验。旧安装目录随后移动为同目录 `previous-installed.app`；旧应用不再占据Applications位置，但可恢复，所有更旧回退未删除。
- 部署时旧App PID70166正常菜单退出；其子进程73019继续运行，待自然结束后才替换。没有SIGTERM／SIGKILL或强制终止同步。
- 安装后生产库只读核对Alembic为 **0022**，新计量表已出现1条App运行记录；这不是合成探针写入生产库，不读取其提示词或响应。
- 迁移前0021快照额外保留为回退目录的 `app-pre-0022.db`（目录700／文件600），完整性quick_check通过，SHA-256 `f75846f3f640f9c00a191277db6ae959070ebe6c40003d65dd7642ad684bdfca`。系统sqlite3只读打开无WAL伴随文件的快照失败，改用只针对闭合快照的immutable只读验证脚本通过；没有回滚或覆盖生产库。未来回退须先核对新增数据，不直接恢复旧快照。
- 结束功能提交两端工作树干净，原分支不变。Mac再次fetch后origin/main仍 `d71c19bd34c4d53dba1201aa63999d77ae94a63c`，开放PR仍0。沙盒仍没有origin/main。
- 最后的本地交接提交仅补此部署记录及只读快照验证脚本，不改变已部署功能源码。没有推送、PR创建／合并、版本号变化、远端分支删除或Release发布；两个禁止旧工作树未进入或修改。
