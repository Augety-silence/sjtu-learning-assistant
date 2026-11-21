# 课堂视频静默同步修复（2026-11-21）

## 起点与范围

- 正确 Mac 仓库：`/Users/bytedance/Projects/sjtu-pr-audit-20261101-5c3127d`。
- 分支两端均 `aime/1793618241-ai-document-multimodal`，开始工作树均干净。
- Mac 开始 HEAD `61a3ce94999267f02789bac9904ce5d30e43d246`；sandbox 开始 HEAD `a3926c500aa4c643c563fc6840a9b75ae8804e7b`，共同源码 tree `df54f1b93059eb14c047eef6ab9d9c5a45db9854`。
- Mac fetch 后 origin/main `d71c19bd34c4d53dba1201aa63999d77ae94a63c`，未变化，开放 PR 为零。sandbox 不存在 origin/main 引用。
- 开始 App 1.8.12，PID 55387，主程序 SHA-256 `3b75a7ad74c7efb6915c01c5c1da2544b8ad400d3ed3b186574acb2ac2bbd92c`。
- 本轮不推送、不创建/合并 PR、不改版本、不发布 Release。不进入两个禁止旧工作树；不删除字幕、课件、人工稿或旧回退。

## 证据与处理

1. 截图中的状态过滤条压住第一张录像卡片，播放器仍保留主动练习。VideosView 的批量栏、计数、六类状态筛选及底部任务面板反复展示后台工作。删除这些 DOM、相关状态与动作；列表头显式不收缩，材料控件正常流布局，页签不再放置移动背景。
2. PDF 之前仅手动触发，App 完成后自动打开 Finder；VideoService 每次重新读取所有图片，导出同课程文件共用名称。改为播放后静默触发，仅明确点击打开；按录像区分导出文件，源 ID 和课件版本隔离持久缓存，逐页验证后原子写入，失败保留已验证页面，重试与重启复用，签名查询参数不落盘。
3. 字幕沿用现有 prepare_for_playback 幂等任务和恢复契约，单个重试按钮继续失败任务而非另起批量任务。播放进度与原始课堂同步不依赖 AI 已配置。正常正文不混入完成、阶段、校对统计；错误保留在重试按钮提示及现有日志，不弹窗。
4. 删除无生产引用的 TranscriptPractice、TranscriptLearningFlow 及专用解析器；删除 79 个废弃 CSS 规则/选择器。保留仍被正文和诊断使用的时间跳转、质量诊断组件，不误删共用模块。
5. 删除随废弃批量/计数/旧学习流界面失效的 48 个旧测试块（52 个展开用例）及四个旧学习流用例，补 12 个静默同步回归；既有播放、快捷键、字幕载入、超时和来源隔离测试继续执行。新回归包含 StrictMode 去重、顺序任务、切换隔离、有限自动重试、无每日总额度、挂起等待释放、原始字幕可选复制、无 AI 配置仍同步、单按钮失败续传与菜单键盘操作。

## 验证记录

- sandbox 完整 pytest：1193 passed / 3 skipped / 496 subtests。
- 原生 WKWebView 验收覆盖实际 VideosView 的 18 节列表、1050/760 宽度、页签无遮盖；使用合成资料，不等于真实课堂全流程。
- 真实交大课件接口成功生成 93 页 / 13,664,309 字节 PDF，SHA-256 `cf706306e88d31aee9b635718ba0e3c01e0e21a42c7b558622967062cdab29bb`。第二次生成哈希一致并复用缓存。使用现有 Canvas 钥匙串，不调用模型，不修改原件；不将单节成功等同全部课程通过。
- sandbox 完整 unittest：1121 tests，2 skipped；完整 Vitest：62 文件 / 570 passed。首次 unittest 受代理环境污染，清除代理后重跑通过；旧界面断言已按新契约更新，没有跳过测试。
- Mac 完整 pytest：1195 passed / 1 skipped / 496 subtests；完整 unittest：1121 tests / 1 skipped；最终完整 Vitest：62 文件 / 570 passed。
- 两端 Biome：165 文件通过；TypeScript + Vite 生产构建、compileall、Python/npm 许可证、秘密模式扫描、git diff --check 通过。npm 152 个锁定生产依赖；秘密扫描有 7 个非 UTF-8 文件未覆盖，不等于重新运行 GitHub gitleaks。
- macOS WKWebView：18 节实际组件合成列表，1050/760 两宽度、3 页签，无列表/页签遮盖；旧字幕详情原文、页签图层和几何检查通过。首轮合成页缺 ToastProvider，修正验收夹具后通过；不冒称真实视频播放验收。
- Mac 验证日志 `/private/tmp/sjtu-video-validation.Ldvupv`；原生合成构建 `/private/tmp/sjtu-video-audit.xgHrK0`。
- 本机只读原始字幕核验：19 个不同任务，2 个尚未保存源；17 份 lesson.md 与 original.md 经 DesktopBridge 可读；精炼 lesson_summary 为 0，17 pending，其中 11 个 timeout。此既有摘要队列尚未完成，本轮没有掩称已经完成；正常 App 后台可继续既有任务。
- 原始字幕样本 SHA-256 前后均为 `2e768ed61fbc1ea3a2f66ce2ad0c037d7b70e4eeee84a0d72b164cd7dc8b5267`。
- 已知非阻断提示：jsdom 未实现媒体 play/pause、Vite 主 chunk 大于 500kB、macOS fork 与资源回收警告。

## 限制与接续

- 课件上游尚未生成、当前帐号无权限或网络不可用时，不能凭本地重试制造不存在的课件；保留轻量重试。
- PDF 自动同步针对当前观看的交大视频空间录像，不自动将本地视频遍历截图；上游最多 200 页和既有字节/像素上限保留。
- API 每分钟限速、取消和有界重试保留，不加人工每日总额度。界面静默不代表后台所有课程摘要都已生成。
- 本轮不能代替 Windows 真机及所有真实课程验收。
