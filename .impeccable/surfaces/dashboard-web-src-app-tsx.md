---
version: 1
slug: "dashboard-web-src-app-tsx"
primary_target: "dashboard-web/src/App.tsx"
related_targets: ["dashboard-web/src/index.css","dashboard-web/src/components/AppShell.tsx","dashboard-web/src/components/ui/Button.tsx","dashboard-web/src/components/ui/Tabs.tsx"]
---

## Direction contract

THESIS: 让整个学习助手成为一块安静、通透、随时可操作的蓝色学习驾驶舱，拒绝用一张张不相干的白色卡片堆出“后台管理系统”。

OWN-WORLD: 保留 SJTU 蓝和现有 Logo；以冰蓝环境底、半透明窗口层、乳白控件层、1px 冷色高光描边和柔和下坠阴影建立三层深度。主容器 18–22px 圆角，卡片 14–16px，控件 10–12px；深色模式对应烟熏蓝玻璃。

STORY: 学生进入后先看到稳定的全局导航与当前学习工作区，再自然读取任务、资料和消息。玻璃不是装饰，而是持续表明“全局环境—当前页面—可操作控件”的层级；同步、选择、展开只用克制动效确认状态。

FIRST VIEWPORT: 1280×820 窗口内，左侧为独立圆角半透明导航舱，右侧为占据主要空间的玻璃工作区；页头悬浮在内容之上，标题在左、页面操作与主同步按钮在右。内容保持原有密度，表格、列表和对话框使用统一的内层玻璃表面，不改变任何业务入口。

FORM: 采用用户固定的 Still Today Aura 方法并翻译为 macOS 安全的 CSS/WebView 视觉系统；这是既有产品世界的系统扩展，不移植 Windows DWM，也不引入 Tauri。seed key: user-pinned-aura-20261009。

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
