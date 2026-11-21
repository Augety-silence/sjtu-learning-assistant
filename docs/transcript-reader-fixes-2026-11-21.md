# 字幕详情三项修复与本地验收

## 现场与范围

用户截图 P1“字幕工件标识不正确”，P2 删除旧校对/练习/规整入口，P3 页签滑动选中块覆盖文字。本轮只做本地修复、提交和 App 回退替换；不发布远端版本。

- Mac 正确仓库：`/Users/bytedance/Projects/sjtu-pr-audit-20261101-5c3127d`。
- 开始分支：`aime/1793618241-ai-document-multimodal`，工作树干净。
- Mac 开始 HEAD：`86c233d4b3d72ee387e2d2f804c156961e67aa40`。
- 沙盒开始 HEAD：`5f8f760cc6c251abaf65e9e42a4b37d73665df30`，相同源码 tree `1d778f661390dcb0786f68b427a746318f5b95e4`，历史不同，不重写历史。
- 本轮 fetch 成功，`origin/main` 仍为 `d71c19bd34c4d53dba1201aa63999d77ae94a63c`；不是 main，也不与远端 main 相同。
- `gh pr list --state open` 返回空列表：没有待处理 PR、依赖、冲突或待审查项。不把本地测试描述成新的 GitHub CI。
- 两个旧脏工作树未进入、未修改，不清理或搬运原修改。

## 审计发现与处理依据

| 项目 | 文件与触发条件 | 影响与修复 |
| --- | --- | --- |
| P1 | `desktop_app.py::_artifact_id` 白名单只有旧五类；服务已输出 `lesson`、`lesson_summary`、`original` | 新文件在桌面桥接层被拒，正文根本未读取。复用 `transcript_service.ARTIFACT_FILES`，保留严格32位任务ID、V2不透明ID及服务路径验证。新增真实服务列表→DesktopBridge→DashboardService→正文读取/定位的临时目录回归，未知kind与路径输入仍拒绝。 |
| P2 | `TranscriptDetailDrawer.tsx` 默认两页签之外仍有历史折叠区，并自动读取V2列表 | 删除历史区、三个旧入口及相应读取/缓存/渲染代码；只读取精炼课堂总结或完整原始字幕。总结未完成不回退到长旧稿。移除混入新资料的旧校对55%进度/诊断，仍保留新材料列表/正文/操作错误与重试。原文件、人工稿未物理删除。 |
| P3 | `ui/Tabs.tsx` 将移动背景挂在选中按钮内，跨兄弟项共享布局 | 静态Chrome层级没有复现，但移动背景不应跨越不同按钮的文字层。改为组内唯一底层背景(z0)，所有按钮/标签在其上(z1)；测量选中目标位置/尺寸，支持窗口尺寸变化、键盘和减少动态效果。其他过滤卡片不改。 |

## 验证

- 定向后端：43 passed，43 subtests passed。
- 定向前端：20 passed；移除旧功能后的有效回归迁移到新资料流程，未跳过失败。
- 全量 pytest：1191 passed、3 skipped、496 subtests passed。
- 全量 unittest：1119 项、2 skipped、通过。
- Mac 全量 pytest：1193 passed、1 skipped、496 subtests passed；unittest 1119项、1 skipped、通过。
- 全量 Vitest：62 文件、614 用例通过。首次全量4项失败是 VideoActions 合成数据仍使用被移除的旧 summary 类型，更新为 lesson_summary 后全量通过。
- Biome：最终164文件通过；TypeScript + Vite生产构建、compileall、git diff --check通过。
- Python/npm许可证通过（npm152生产依赖）。秘密模式扫描通过，7个非UTF-8文件未文本扫描，不等于GitHub gitleaks。
- Impeccable：按现有Aura界面精简，机械检测无发现，不另做视觉重设计。
- 本地Chrome访问隔离合成验收页，课堂总结/原文均可读取，只有两页签，正文允许自由选择复制；背景z0、所有页签z1。交互入口临时返回 detached 错误，使用同一本地会话的页面表达式完成合成页切换；未切换云浏览器。
- Mac 本机只读生产字幕检查：19个不同来源任务，2个未保存字幕；17份lesson.md、17份original.md经DesktopBridge读取成功，失败0。lecture-summary.md当前0份，17份仍pending，5份记录timeout，不能称为全库总结已完成。
- 原生WKWebView合成验收通过：总结/原文两页签、背景与标签层级、选中块位置尺寸均通过。独立目录 `/private/tmp/sjtu-reader-native.Emg5Yc`。首次验收脚本的合成JS接口被原生桥接注入替换，改用显式合成原生桥接后通过；未将失败候选部署。构建脚本独立输出，不覆盖生产dist。
- 已知非阻断：jsdom媒体未实现、Vite主chunk超过500kB、测试资源清理警告。

## 部署与边界

- Mac 功能提交：`087dd8b6eb82ac2630fe6248043949e8bfc42269`，沙盒对应功能提交：`404711453fbda2c6b0c09bc1c1b92ae48ff94ff4`，功能源码tree相同为 `f8327680456616e7097ea911b98f44a07f3ea81f`。
- 整合顺序：保留原功能历史→桥接白名单/精简详情/页签底层及回归→全量与真实本机只读/合成原生验收→本地功能提交→精确构建→签名验证→创建回退→正常退出旧App→可恢复替换→启动核验→记录提交。恢复测试文件原有可执行权限，避免重建文件带来无关权限变化。
- 构建：`git archive 087dd8b6eb82ac2630fe6248043949e8bfc42269` 到 `/private/tmp/sjtu-reader-build.iODriR`，独立前端生产构建和PyInstaller打包成功。图标生成弃用警告非阻断；构建不覆盖正确开发工作树。未运行含递归清理的旧打包脚本。
- 安装：`/Applications/SJTU Learning Assistant.app`，版本1.8.12。
- 签名：ad-hoc codesign，安装前后 `--verify --deep --strict` 均通过；不是Apple公证或Developer ID发行签名。bundle验证的plist、第一方路径/秘密、禁止模块检查通过。
- 新主可执行SHA-256：`3b75a7ad74c7efb6915c01c5c1da2544b8ad400d3ed3b186574acb2ac2bbd92c`，候选与安装一致。
- 新回退：`/Users/bytedance/Projects/sjtu-app-rollbacks/20261121_123829-transcript-reader/SJTU Learning Assistant.app`，旧主可执行SHA-256 `62ae000d0b278e3109454d1311cbdb7588289d7b6d8864e6eb2fa69a859ac7aa`，验签通过。原安装同时可恢复地移到同目录 `previous-installed.app`；所有更旧回退保留，未删除任何App副本或学习文件。
- 正常退出旧PID52639并确认无旧App进程；新App启动PID55387（2026-11-21部署后核验），以后须重新查询，不能视为永久当前事实。
- 原始样本VTT前后SHA-256均为 `2e768ed61fbc1ea3a2f66ce2ad0c037d7b70e4eeee84a0d72b164cd7dc8b5267`。
- 结束分支与开始相同；功能提交后两端均干净。最后仅记录部署与恢复测试文件权限；记录提交哈希由 `git log -1` 给出（不能在自身内容中递归记录自身哈希）。origin/main仍 `d71c19bd34c4d53dba1201aa63999d77ae94a63c`。
- 两个禁止工作树 `/Users/bytedance/Projects/sjtu-learning-assistant` 和 `/Users/bytedance/Projects/sjtu-m9-nightly-20261025` 本轮未进入、未修改。

版本保持1.8.12；未推送、创建/合并PR、远端分支删除、改版本或发布Release。未真实调用模型、学校同步或云盘写操作；本轮不宣称全库总结已完成、Windows真机已验证或教师质量审查通过。
