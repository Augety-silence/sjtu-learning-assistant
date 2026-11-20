# 往期学期课程获取

## 使用入口

1. 在设置保存 Canvas 凭据，打开“课程资料”。页面初始只读取本地缓存。
2. 点击“发现全部学期”：分页获取 Canvas `available` 和 `completed` 课程，包含真实 term ID，不限制 `enrollment_state=active`。
3. 选择学期，可进一步限定一门课程。点击“同步所选资料目录”获取公告、作业、文件夹、文件、模块；点击“同步并下载所选课件”进一步下载该范围。
4. 资料树只展示所选范围，保留原学期/课程/分类/课时文件夹，不移动旧文件。
5. 在“知识库”用相同学期、课程选择器浏览或搜索 Markdown。下载完成会唤醒已有自动课件编译，但必须已开启自动编译并有可用 AI 配置与额度。**历史导入完成不等于多模态编译完成**。

CLI 支持 `python sync_data_to_db.py --canvas-only --term-id term:123 --course-id 456 --no-download`；`--course-id` 可重复。不提供范围时仍是原默认同步行为；显式范围不发送通知。

AI/MCP：先用 `list_courses(query, term_id, offset, limit)` 找到稳定课程 ID，再用 `search_compiled_knowledge(query, term_id, course_id, offset, limit)` 检索。结果和分页读取回显课程、学期来源。没有课程关联的手工项目不会被猜测归入某个学期。

## 一致性与权限

- 学期身份优先 `term.id`，旧数据无 ID 时使用显式 legacy 标识；同名学期与同名课程不会合并为同一个身份。
- 刷新目录和范围导入不推进默认 `courses` 游标。旧版 updated_at 范围先冻结为独立来源 ID 集合，避免目录刷新改变默认批量下载、待办、日历、消息和系统通知。
- 每次导入重新获取当前账号可见目录，缓存与网页传来的课程 ID 都不是授权依据。空范围不等于“全部历史”。
- 日期受限课程、403/404 栏目保留已有缓存，结果为部分完成；401、429、网络/格式错误不吞掉为成功，不推进本轮内容游标。
- 后台任务固定范围、记录计数与结果，页面切学期只改变浏览。应用重启遇到 running 标记显示“未确认完成”，不会擅自重跑模型或下载。
- 日常同步与历史同步共用进程锁和跨进程同步锁。退出请求在课程/文件边界停止；**在途 HTTP/下载不保证立即中断**。
- 所有学校操作均为读取/下载，不提交作业、不向真实学习数据写远端。

## 审计发现与处理依据

| 位置 | 触发与影响 | 处理 |
| --- | --- | --- |
| canvas_sync / canvas_client 的 active 查询 | 仅活动选课，历史列表缺失 | 新增全目录查询，保留旧默认 API |
| repository / archive_service 默认范围标记 | 历史刷新改 Course.updated_at，误改变默认下载范围 | 冻结并持久化独立范围，历史资源分开记录 |
| dashboard_service / notifications / agent_runtime | 旧作业与新入库历史文件出现在当前待办或下一轮提醒 | 默认查询统一范围过滤，显式历史查询仍保留 |
| material_tree 学期分组 | 同名学期混在一个目录 | 真实学期 ID 分组，兼容旧数据 |
| compiled_knowledge 搜索 | 搜索无法按稳定课程身份限定 | 从数据库原始文件映射来源，正文读取前过滤 |
| sync_data_to_db 文件/模块异常 | 认证失效、限流或断网被当作栏目隐藏 | 仅 403/404 跳过，其他失败终止 |

## 真实接口证据与边界

2026-11-20 对当前账号的真实 Canvas GET 验证：旧 active 查询 8 门/1 学期；available+completed 全目录 54 门/6 学期，其中 4 门日期受限，比旧查询多 46 门。completed 选课查询 42 门/4 学期，均在全目录中。抽查一门历史课程成功取得 41 文件、16 作业，模块成功空列表。

仅证明当前账号的 API 行为，不代表所有用户、全平台课程或学校网页已逐项对照。未验证 Windows 真机、权限失效的全种类、所有历史文件下载、多模态全量编译与网页课程数一致性；不得把模拟测试说成实机或真实模型验收。本轮不改版本，不推送、不建 PR、不合并、不发布 Release，不构建或替换安装 App。

## 最初沙盒验证与交接（2026-11-20）

- 定向历史课程与通知回归：42 passed，11 subtests passed；新增历史课程测试覆盖 19 个用例。
- 完整 pytest：1087 passed / 2 skipped / 453 subtests passed。
- 完整 unittest：1014 项，OK，1 skipped。
- 完整前端 Vitest：59 文件、596 用例通过，包含新增主题变量回归。
- Biome、TypeScript/Vite production build、compileall、Python/npm 许可证、秘密模式扫描、git diff --check 通过。秘密扫描对 7 个非 UTF-8 文件明确跳过；不是声称所有二进制内容已扫描。
- Python 首轮因沙盒 `NO_PROXY/no_proxy` 被 httpx 解析为无效端口而失败；仅测试进程移除这两个字段后复跑完整套件。另补齐通知单测的旧库 Session 模拟契约，未弱化生产范围校验。测试中的 jsdom play/pause、临时目录 ResourceWarning、Vite chunk >500 kB 和 Node 代理实验提示为非阻断信息。

PC 开始基线：`aime/1793618241-ai-document-multimodal`，HEAD `b86975d1af09d09114360b42e4e3fb8e59b8d921`，origin/main `d71c19bd34c4d53dba1201aa63999d77ae94a63c`，工作树干净。fetch 后 origin/main 无变化，开放 PR 为 0。与早期交接描述的 main 不同，以本次实测功能分支为准。

开发在独立沙盒副本进行；校正后的基线源码树精确等于 PC 的 `9ebe3e308a2edfbb90d04c21ca165c3cbfda4a93`（511 个跟踪文件）。副本基线提交 `84d833b` 只是快照提交，不是 PC 的历史提交。依赖链接、构建缓存和私密配置不进入代码提交或交付目录。

最终 PC 核验时连接已离线，因此本轮**未向 PC 应用补丁、未在 PC 创建提交，结束 HEAD 与工作树状态不可重新确认**。需重新连接桌面应用后核验 HEAD、origin/main 和所有未提交修改，再按精确基线增量整合，不覆盖后来产生的修改。真实隔离数据库导入/下载与打包 App 交互验收因此尚未执行。

本轮未 push、创建/合并 PR、删远端分支、改版本或发布 Release；应用版本仍为基线 1.8.11。未构建、签名、部署 App，也未新增回退副本；安装 App 的现版本、SHA-256 与 PID 未重新读取，不作为本轮事实。两个旧脏工作树均未进入、清理、修改或搬运。

## Mac 路径修复与本地整合

首次 Mac 验证发现 LV-01：`compiled_knowledge._safe` 拒绝 `/var` 的固定系统别名，而归档设置接受它；`tests/test_course_history.py` 的默认临时目录用例因此返回空知识列表。只对测试目录做 resolve 的对照曾通过，但不算生产修复。

现在归档设置与知识读取复用 `path_safety.is_macos_system_alias`：仅 macOS 的精确 `/var → /private/var`、`/tmp → /private/tmp` 可以通过，解析失败或目标不匹配时拒绝；普通父级链接、根链接与路径越界仍拒绝。保留原设置和 manifest 的词法路径，不迁移或覆盖用户 Markdown。新增默认 Mac 临时目录搜索/读取、用户链接拒绝、目标匹配与平台限定回归，并保留原始历史学期来源检索用例不作改写。

用户已授权将修复与历史学期补丁整合到正确项目、创建清晰本地提交，并构建替换本地测试 App；不因该授权推送或发布 Release，版本保持 1.8.11。最终实际测试和部署信息以本次交付记录为准，不将前述历史沙盒记录描述为本次 Mac 结果。
