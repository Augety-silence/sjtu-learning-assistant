# 课程 Wiki 与知识阅读器交付记录（2026-11-21）

## 本轮范围与结论

修复“AI 只拿到目录却没有继续读取 MD”、文件列表被目录挤出视口、阅读操作栏滚走、原生窗口禁止选择文字四类问题。接入按学期、课程隔离的 LLM Wiki：先由模型组织知识地图、概念和关系，再独立来源审校；回答时通过 Wiki 导航读取原 MD 核实。

RAG 本身并不排斥 Markdown。本次正文不可达的主要原因是 Agent 预取目录后关闭工具，不是 MD 格式无法检索。关键词检索保留为兜底；新流程不再要求整句问题精确匹配原文。

## 现场与整合顺序

- 本机仓库：`/Users/bytedance/Projects/sjtu-pr-audit-20261101-5c3127d`。
- 开始分支：`aime/1793618241-ai-document-multimodal`，不是旧交接信息中的 main。
- 开始 HEAD：`0959bd20929a2e3b589f536543c125e3d53829dd`；开始工作树干净。
- 开始及实现提交后的本地 origin/main：`d71c19bd34c4d53dba1201aa63999d77ae94a63c`。
- 已执行 status、log、stat、工作区和暂存区 diff、fetch、HEAD 与 origin/main 核验。fetch 超时；开始时通过 GitHub 公共 API 核对 main 一致、开放 PR 为空。结束阶段的公共 API 复核分别超时/HTTP 403，不能把旧结果称为最新远端状态。
- 沙盒仅作同树副本验证，基线 tree 与本机一致。每次增量补丁先 `git apply --check`；提交前核对整个 tree，未覆盖用户资产。
- 整合顺序：阅读器 → Wiki 与正文工具 → 自动构建联动 → 原生文字选择 → 自定义预设工具上限 → 真实调用暴露的概念 ID 问题 → 索引损坏回归。
- 本机实现提交：`80bf437ca7029a811e3c9fbee0b9db9115206f67`。
- 实现 tree：`f63b4f54ec248c1da1b6fbb4a2d61bd7b8ec3feb`；提交后工作树干净。

## 审计发现与处理依据

| 文件/位置 | 触发、影响与证据 | 修复与回归 |
| --- | --- | --- |
| `agent_runtime/loop.py`，run 的预取与工具定义分支 | 课程资料预取成功后不再开放工具，目录被当成知识正文 | 保持知识读取工具开放；真实轨迹确认完整 MD 被读到；工具不兼容明确报错 |
| `knowledge_wiki.py`，browse 的知识地图返回 | 模型把概念 Markdown 相对链接当 page_id，消耗步骤后不能正常作答 | 地图返回结构化概念 ID、来源 ID；提示只使用返回 ID；来源核对后及时作答 |
| `CompiledKnowledgeBrowser.tsx` 与 `compiled-knowledge.css` | 长课程树挤掉同文件夹的文件列表；截图显示选择了 6 份文件却看不到切换入口 | 目录和文件列表独立滚动；六份隔离样例含最后一份均可切换 |
| 同组件阅读操作栏 | 正文滚动带走“从头阅读”等操作 | 操作栏移出正文滚动区域；浏览器滚动 1500px 后操作栏位置不变 |
| `desktop_app.py`，create_window | pywebview 默认 text_select=False；CSS 在普通浏览器可选文字不等于原生 App 可选 | 显式 text_select=True；启动回归断言；移除该阅读器的整篇复制按钮，正文自由选择 |
| `agent_runtime/loop.py`，出站工具定义 | 自定义预设全选后由 16 变 17 项，兼容客户端拒绝 | 仅出站超限时省略冗余 list 工具；Wiki、正文读取、registry/MCP 权限保持不变 |
| `knowledge_wiki.py`，来源索引校验失败出口 | 校验正文文件后，发现未知 source_id 时仍可能残留 pages 列表 | 清空未验证页面；损坏索引不能留下可读正式页，原文件不删除 |

## 修改文件

实现提交共 19 个文件：

- 阅读器：`dashboard-web/src/components/CompiledKnowledgeBrowser.tsx`、`compiled-knowledge.css`、`dashboard-web/src/test/CompiledKnowledgeBrowser.test.tsx`。
- 隔离 UI 验收页：`dashboard-web/knowledge-reader-audit.html`、`dashboard-web/src/test/knowledgeReaderAudit.tsx`。
- 原生窗口：`desktop_app.py`、`tests/test_desktop_app.py`。
- Wiki：`sjtu_learning_assistant/knowledge_wiki.py`、`tests/test_knowledge_wiki.py`、`compiled_knowledge.py`。
- Agent：`agent_runtime/loop.py`、`tools.py`、`presets.py`、`tests/test_agent_runtime.py`、`agent_presets/manifest.json`、`agent_presets/skills/course-summarizer.md`。
- 自动构建：`course_markdown_sync.py`、`dashboard_service.py`、`tests/test_course_markdown_sync.py`。

本记录作为独立文档提交，不改变已验证实现。

## 验证结果

| 检查 | Mac 本机 | 沙盒同树副本 |
| --- | --- | --- |
| 定向回归 | 首轮 97 passed；最终 Wiki 10 passed / 7 subtests | 定向 76 passed / 38 subtests，新增安全回归包含在最终全量 |
| pytest 最终 | 1168 passed / 1 skipped / 482 subtests | 1166 passed / 3 skipped / 482 subtests |
| unittest 最终 | 1094 tests / 1 skipped / OK | 1094 tests / 2 skipped / OK |
| Vitest | 62 文件 / 623 passed | 62 文件 / 623 passed |
| Biome | 162 文件通过 | 162 文件通过 |
| TypeScript + Vite | 通过 | 通过 |
| compileall、diff check | 通过 | 通过 |
| Python/npm 许可证 | 通过 | 通过，npm 152 个锁定生产包 |
| 项目秘密模式扫描 | 通过，7 个非 UTF-8 文件未扫描 | 通过，7 个非 UTF-8 文件未扫描 |

沙盒前两次全量运行遇到 20 项环境错误：HTTPX 解析代理排除配置时出现 InvalidURL，并非本轮业务失败。仅对测试进程清除 HTTP/HTTPS/ALL/NO_PROXY（含小写）后定向复核、全量重跑均通过，没有修改用户网络设置。

已知非阻断：jsdom media 提示、macOS 多线程 fork 警告、Vite 主 chunk 超过 500kB。未触发 GitHub CI，未把本地秘密模式扫描称为远端 gitleaks。

本机日志：`/Users/bytedance/Projects/sjtu-wiki-validation-mHifVl/`。沙盒最终日志为 `/tmp/sjtu-wiki-final-*`。

## 真实 API 与覆盖边界

- 使用本机已保存的交大 API 凭据；没有把密钥写进仓库、导出包或日志。
- `/models` 实测 HTTP 200。DeepSeek 生成课程 Wiki、Qwen 独立来源审校通过。
- 只读真实数据库（SQLite mode=ro）及现有 MD；目录快照有 9 门课程、94 份可读文档。
- 选取“建模与优化”当前 1 份完成的精炼总结，Wiki 输出到独立目录：`/Users/bytedance/Projects/sjtu-wiki-real-2_j_aj2z`。没有修改生产 Wiki、原始课件或原 MD。
- 初次 Agent 验收未通过：有无效 page_id 与多余检索，达到步骤上限。依据真实轨迹修正结构化 ID 后复测成功。
- 成功轨迹：Wiki 目录预取 → Wiki 正文 → read_compiled_knowledge → 相关 Wiki 概念 → 第 4 轮完成回答。原 MD 全文 2376 字符（含元信息），offset=0、next_offset=null，逐字核对与真实 MD 一致。回答 1412 字符。
- 真实验收只覆盖该课程当前 1 份完成总结；不能据此宣称 94 份资料或整门课程全部完成。跨文件融合的两份来源场景另有隔离回归，不能冒充真实多文件课程验收。

生产 Wiki 默认位于 `Knowledge Markdown/Wiki/<学期>/<课程-身份>/<来源指纹>/`；审校清单位于 `99_System/Course-Wiki.json`。自动 MD 同步会前后重建变化课程，无需手动把每份 MD 再导入。只有已完成 `course-summary-v1`、正文不超过 2000 字符的总结进入正式 Wiki；未完成或旧长稿仍可读取，但不伪装成精炼正式资料。来源变化后旧 Wiki 不再用于回答，旧文件保留。

没有全局每日调用总量上限；沿用交大服务请求限速和取消控制。同一失败快照当日不无限重试，单课程超过 120000 输入字符明确失败，不截断冒充完成。人工编辑的页不覆盖、不作为已验证正式页供给。

## UI 验收与未执行项

云端浏览器隔离验收：六份文件切换、新文件从头、旧文档独立阅读记忆、操作栏跟随、跨段选中 73 字符、1200/640 容器无横向溢出、窄容器滚动到第六份。一次 UI 检测结果为空。该结果不是 Mac 原生 App 或 Windows 真机验证。

原生 text_select 开关有启动回归，但尚未在新安装 App 内验证 Cmd+C。Windows 原生拖拽、网络盘/AppData、两平台真实云盘登录及上传/覆盖/删除/恢复/大目录，均未在本轮执行。没有对真实学习数据进行这些写入测试。

## Git、版本、App 与旧工作树

本轮没有 push、PR 创建/合并、远端分支删除、版本号修改或 Release 发布，也没有 reset、clean、rebase、force push。

新 App 从精确提交 `80bf437ca7029a811e3c9fbee0b9db9115206f67` 的 git archive 构建，独立临时源码和 build/dist，不覆盖旧产物。PyInstaller 打包、ad-hoc 签名、deep/strict 验签、plist、第一方路径/秘密及禁止模块检查均通过；不是 Developer ID 公证或新 Release。

- 原始构建目录：`/private/tmp/sjtu-wiki-build.ywDWbb`。
- 持久保存的待部署 App：`/Users/bytedance/Projects/sjtu-pr-audit-20261101-5c3127d/dist/wiki-reader-20261121_095936/SJTU Learning Assistant.app`。复制后再次验签通过。
- 构建版本：1.8.12，没有改版本号。
- 新可执行文件 SHA-256：`69b8012c5f5b215d5241400306774ea7ba7188b809513441837cc808c7d4e04d`。
- 新 App 未启动、未替换 `/Applications`，未创建部署回退副本，也未删除旧回退。需要先退出正在运行的旧 App，确认没有未保存或编译任务，再创建新的时间戳回退并部署。不要同时启动两个实例。

已安装 App：`/Applications/SJTU Learning Assistant.app`，版本仍为 1.8.12；可执行文件 SHA-256 为 `bf33e67ac4d4f628c24f18c0e41e05e7eee25b90ada400f51c77f9eca283956f`，结束核验时 PID 仍为 52386。未结束该进程、未替换安装包，不能宣称正在运行的版本已经具有本轮修复。当前安装包精确来源提交没有从包内重新确认，不猜测。

两个旧脏工作树 `/Users/bytedance/Projects/sjtu-learning-assistant` 与 `/Users/bytedance/Projects/sjtu-m9-nightly-20261025` 仅只读核验状态，未修改、清理、覆盖或搬运任何内容；原本已有的用户修改保留。
