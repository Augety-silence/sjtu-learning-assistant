---
name: SJTU 学习助手 2.0
description: 从审阅版重建的学生学习工作台与 AI 工作区
colors:
  v2-accent: '#2458e8'
  v2-bg: '#fff'
  v2-sidebar: '#f7f8fa'
  v2-soft: '#f2f4f7'
  v2-selected: '#e1e7ef'
  v2-line: '#e1e5eb'
  v2-ink: '#1f2329'
  v2-muted: '#667180'
  border-strong: '#cbd3df'
  focus-ring: '#5279d1'
  status-danger: '#b83b34'
  status-warning: '#a85c20'
  status-success: '#2c7550'
  aime-accent: '#15816c'
  aime-sidebar: '#f6f8f7'
  aime-soft: '#eff5f2'
  aime-selected: '#dcece5'
  aime-line: '#dce5df'
  dark-bg: '#191c22'
  dark-sidebar: '#15181d'
  dark-soft: '#242932'
  dark-selected: '#303a48'
  dark-line: '#333a45'
  dark-ink: '#edf0f5'
  dark-muted: '#b0b9c6'
  dark-accent: '#8eafff'
  dark-action-primary: '#496fcc'
typography:
  headline:
    fontFamily: -apple-system,BlinkMacSystemFont,"PingFang SC","Segoe UI","Microsoft YaHei",sans-serif
    fontSize: 25px
    fontWeight: 600
    lineHeight: 1.4
  title:
    fontFamily: -apple-system,BlinkMacSystemFont,"PingFang SC","Segoe UI","Microsoft YaHei",sans-serif
    fontSize: 17px
    fontWeight: 600
    lineHeight: 1.5
  subtitle:
    fontSize: 16px
    fontWeight: 600
    lineHeight: 1.5
  body:
    fontFamily: -apple-system,BlinkMacSystemFont,"PingFang SC","Segoe UI","Microsoft YaHei",sans-serif
    fontSize: 15px
    lineHeight: 1.6
  label:
    fontSize: 14px
    fontWeight: 400
    lineHeight: 22px
  meta:
    fontSize: 12px
    lineHeight: 1.65
  summary:
    fontSize: 28px
    fontWeight: 600
    lineHeight: 1.4
  ai-body:
    fontSize: 15px
    lineHeight: 1.85
rounded:
  tag: 5px
  tab: 6px
  control: 7px
  navigation: 8px
  floating-menu: 9px
  section: 10px
  dialog: 12px
spacing:
  compact: 4px
  icon-gap: 6px
  control-gap: 8px
  brand-gap: 10px
  row-gap: 12px
  inset: 16px
  grid-gap: 18px
  narrow-inset: 20px
  heading-gap: 22px
  page-block: 24px
  page-inline: 28px
components:
  button-primary:
    backgroundColor: '{colors.v2-accent}'
    textColor: '{colors.v2-bg}'
    rounded: '{rounded.control}'
    typography: '{typography.label}'
    padding: 4px 10px
  button-secondary:
    backgroundColor: '{colors.v2-bg}'
    textColor: '{colors.v2-ink}'
    rounded: '{rounded.control}'
    typography: '{typography.label}'
    padding: 4px 10px
  button-secondary-hover:
    backgroundColor: '{colors.v2-soft}'
  button-ghost:
    backgroundColor: transparent
    textColor: '{colors.v2-ink}'
    rounded: '{rounded.control}'
    padding: 4px 10px
  input:
    backgroundColor: '{colors.v2-bg}'
    textColor: '{colors.v2-ink}'
    rounded: '{rounded.control}'
    padding: 8px 10px
  navigation:
    textColor: '{colors.v2-muted}'
    rounded: '{rounded.navigation}'
    padding: 0 12px
  navigation-selected:
    backgroundColor: '{colors.v2-selected}'
    textColor: '{colors.v2-ink}'
  badge:
    backgroundColor: '{colors.v2-soft}'
    textColor: '{colors.v2-muted}'
    rounded: '{rounded.tag}'
    padding: 1px 6px
  section:
    backgroundColor: '{colors.v2-bg}'
    rounded: '{rounded.section}'
  tabs:
    backgroundColor: '{colors.v2-soft}'
    rounded: '{rounded.navigation}'
    padding: 3px
  tab-selected:
    backgroundColor: '{colors.v2-bg}'
    textColor: '{colors.v2-ink}'
    rounded: '{rounded.tab}'
    padding: 5px 10px
  composer:
    backgroundColor: '{colors.v2-bg}'
    rounded: '{rounded.dialog}'
---

# Design System: SJTU 学习助手 2.0

## Overview

**Creative North Star: "同一张学习工作台"**

平实的白色工作区、浅灰导航和细分隔构成一个连续的学习环境。文字、真实列表内容和当前操作建立层级，彩色动作承担方向与状态。工作台与 AI 使用同一书本品牌、侧栏宽度和品牌坐标，切换时空间关系保持稳定。

这份规范提取自已实现的 2.0 React / TypeScript 页面与独立样式。用户确认的审阅 HTML 和固定截图决定视觉世界；默认与 Aime 模式的交互均采用黑、白、灰，普通说明使用深色文字，选中状态加粗，深色模式使用新系统自己的深色 token。兼容旧组件的别名不赋予旧 Aura 视觉规范效力。

**Key Characteristics:**

- 白色内容面与浅灰侧栏形成稳定的空间分工。
- 细线、连续列表和低彩度选中态组织信息。
- 中文系统无衬线支持紧凑导航与长文阅读。
- 品牌坐标固定，工作台与 AI 短淡入切换。
- 独立主题 token 与经典样式隔离。

提取来源为 `dashboard-web/src/v2/{design,features,Schedule,video}.css`、`dashboard-web/src/ui-2.css`，并抽样核对 Shell、Overview、Schedule、Materials、MessageList、Settings、AI 中央配置与 WorkspaceTransition。页面构图与业务边界分别见 surface brief 和 PRODUCT.md。

## Colors

默认配色为中性白灰底、深色文字和清楚的 SJTU 蓝动作；前置 token 是规范值。

### Primary

- **SJTU 操作蓝**（`v2-accent`）：链接、当前操作、未读提示、日历标记和主要按钮。
- **Aime 点缀绿**（`aime-accent`）：Aime 模式的链接、光标、复选框强调、焦点及由动作 token 驱动的按钮；部分主要按钮在当前代码中仍固定为 SJTU 蓝。
- **深色强调蓝**（`dark-accent`）与 **深色动作蓝**（`dark-action-primary`）：分别用于深色环境的强调文字和由动作 token 驱动的实心控件。

### Neutral

- **工作区白**（`v2-bg`）：正文、面板、输入与浮层。
- **导航浅灰**（`v2-sidebar`）：侧栏、目录和部分表头。
- **控件浅灰**（`v2-soft`）：悬停、代码块、标签和用户消息底。
- **选中灰蓝**（`v2-selected`）：当前导航、选中行和日期。
- **分隔灰**（`v2-line`）与 **控件强分隔**（`border-strong`）：面板 / 列表边界和输入框。
- **正文墨色**（`v2-ink`）与 **辅助灰**（`v2-muted`）：标题 / 正文与说明 / 占位文字。
- Aime 导航、控件、选中面和分隔使用前置 `aime-*` 值；深色模式对应 `dark-*` 中性色，保持同一空间和组件结构。

### States

成功、警告、失败分别使用 `status-success`、`status-warning`、`status-danger`，与明确文案配合。焦点使用 `focus-ring`，Aime 模式以点缀绿覆盖焦点值。播放器为视频内容与浮层另设深色局部背景，不将播放器配色提升为应用默认底色。

**The 主题同结构 Rule.** 主题改变颜色与状态表达，保留页面结构、品牌坐标和阅读顺序。

## Typography

**Body Font:** 平台系统无衬线，中文回退 PingFang SC / Microsoft YaHei；完整字体栈见前置 token。标题沿用同一字体，没有独立展示字体。代码沿用浏览器等宽回退，不在本轮新增字体。

页面标题、分区标题、正文与辅助文字通过字号、字重、行高区分。正文和列表优先保持连续阅读，导航与操作词语保持完整。

### Hierarchy

- **Headline**：页面标题使用 `headline`；窄窗口页面标题缩为（23px）。
- **Title / Subtitle**：分区标题使用 `title`，下级标题使用 `subtitle`；消息、作业详情标题在局部使用（20px）。
- **Body**：工作台正文使用 `body`；列表标题通常（14–15px）。
- **Label / Meta**：按钮使用 `label`；元信息使用 `meta`，密集消息时间与日历元信息可局部使用（10–11px）。这些局部尺寸不成为全局正文标准。
- **Summary**：连续摘要数值使用 `summary`；窄窗口缩为（24px）。
- **AI body**：AI 长文使用 `ai-body`，输入为（15px）；Markdown 标题分级为（22 / 19 / 17px），表格为（13px）。

数字时间与进度使用等宽数字。长文件名、路径和技术输出允许换行或局部滚动，不能挤走同行操作。

**The 完整词语 Rule.** 操作与外观选项保持完整词语；宽度不足时整项换行。

## Layout

应用铺满桌面视口（100dvh），主区域弹性占据剩余宽度。侧栏默认（248px），窗口宽度不超过（1199px）时为（224px）；左右内边距（16px）。品牌容器高（84px）、内横向留白（8px），Logo 为（36px），与文案间距（10px）。因此宽屏与窄桌面下，Logo 左边缘均距应用左边（24px），顶部（24px）；文案左边缘为（70px）。AI 与工作台复用相同品牌组件与留白。

顶栏高（72px），横向留白（24px）。工作区默认内边距（24px 28px），不超过（1199px）时为（20px）；不超过（699px）时侧栏转抽屉，顶栏高（62px），正文内边距（18px 14px）。常见内容面内边距为 `inset`，页面主要栅格间距为 `grid-gap`。

页面任务决定分栏：消息列表 / 正文、资料目录 / 文件 / 预览、日程月缩略 / 主日历、录像列表 / 16:9 视频播放器。具体比例保留在实现与 surface brief，不将一种页面栅格强加到全部工作页。日程保留左月缩略和主网格，移除最右今日 / 本周日程栏；日 / 周 / 月控制位于右上。日 / 周视图的日期表头、独立全天带与（00:00–24:00）时间网格列对齐，时间网格以半小时划分并局部纵向滚动，左月缩略不随时间轴滚动。日程在不超过（1050px）时缩小左列和间距，周视图冲突组使用整列时间范围与安排数量按钮，点击进入对应日视图；不超过（699px）时隐藏左月缩略。资料、知识库等在（1250px）调整辅助列，视频在（900px）改为上下排列。

AI 输出、输入与空态共用阅读宽度（最大780px）；宽窗口保留总计（64px）的横向空间，窄桌面为（40px），移动抽屉布局为（28px）。Activity 默认（320px），窄桌面转覆盖抽屉；配置在中央工作区展开。

工作台 / AI 切换使用 WorkspaceTransition：进入透明度由（0.78）到（1），时长（220ms），退出为（120ms），进入曲线为 `cubic-bezier(0.22,0.8,0.22,1)`。没有品牌平移或缩放；退出场景不可交互并从读屏隐藏。减少动态效果时切换时长为零。加载旋转器为（1s）线性循环。

**The 固定品牌 Rule.** 工作台与 AI 使用同一侧栏和品牌坐标，切换不移动品牌。

## Elevation & Depth

静态内容面主要通过浅灰与白色层次、细边界和分隔建立深度，普通列表与摘要不依赖投影。浮层、对话框和抽屉使用阴影表达覆盖关系；选中页签仅有轻微抬升。

### Shadow Vocabulary

- **菜单浮层**（`0 12px 36px -10px #15203630`）：浮动选择器、提示和菜单。
- **对话框**（`0 18px 50px -15px #14203466`）：居中的确认 / 操作对话框。
- **选中页签**（`0 1px 3px #17233314`）：白色选中片与浅灰页签底的轻微区分。
- **右侧 Activity 抽屉**（`-10px 0 30px #15203622`）：窄桌面的覆盖轨迹面。

**The 覆盖层抬升 Rule.** 内容面以分隔与色面分组，阴影用于覆盖层和轻微页签抬升。

## Shapes

圆角随容器尺度递增：小标签使用 `tag`，页签使用 `tab`，输入与按钮使用 `control`，导航使用 `navigation`，浮动选择器使用 `floating-menu`，内容分区使用 `section`，对话框与 AI 输入外框使用 `dialog`。常见边界为（1px）实线；文件放置与修改提示可使用虚线。状态点、日期标记与进度旋转器使用圆形。

## Components

### Buttons

按钮紧凑且文字完整。主要按钮以蓝底白字表达执行；次要按钮为白底细边框，悬停变控件浅灰；Ghost 去除可见边界，Link 使用强调文字。标准按钮最小高（32px）、内边距见前置 token，图标间距为 `icon-gap`。禁用控件透明度为（0.5），保持语义禁用。

交互焦点为（2px）主题焦点线、偏移（2px）。页面标题的程序焦点不显示控件框。当前主要按钮来源分别是固定 SJTU 蓝与 `action-primary`，主题说明需区分二者。

### Chips

标签为浅灰填充、辅助灰文字和小圆角；状态标签加明确状态色与文案。附件片同时显示名称和操作，长名称省略或换行，保留移除入口。

### Cards / Containers

分区以白色内容面、细边框与分区圆角包住连续列表。标题区和常见列表行使用 `inset` 内边距；相邻行使用细分隔，末行去除多余底线。卡片服务内容组与操作区域，不替换本轮建立的连续列表关系。

### Inputs / Fields

输入为白底细边框、控件圆角和主题光标。占位文字使用辅助灰并保持不透明，当前浅色白底约（4.95:1）。搜索图标为（16px），搜索框中的输入去除内部重复边框。AI 输入外框使用较大圆角，输入 / 附件 / 工具在同一阅读宽度内；当前外框焦点使用局部灰蓝边界（#9baccb），不另增全局色值。

AI 输入外框最小高（90px），内边距（9px 12px），内部间距（6px）。左侧工具组排列添加与当前助理，右侧排列模型、推理强度和单一发送 / 暂停；发送为主题动作色的（30px）方形按钮，停止用浅灰次要状态。框外工作区名向上展开目录、文件与模型配置，修改前确认就地展开实际权限说明，右侧显示当前字数 / 4000 字。窄桌面保留完整分组并允许工具行换行；附加菜单继续锚定入口，显示本地文件、工作区文件、技能配置及 MCP 与工具权限。

### Navigation

主导航为一层完整文字与线性 SVG 图标。默认辅助灰，悬停转正文墨色与控件浅灰，选中转正文墨色与选中灰蓝；标准行最小高（42px）。AI 历史会话、技能与工作区入口沿用同一侧栏空间，配置详情进入中央。

### Tabs and Floating Selectors

页签组使用浅灰底，白色选中片与完整文字。技能来源使用内置 / 外部页签。助理与模型选择器在锚点附近浮动，独立于页面布局；菜单使用浮层圆角、细边界、投影和可见 hover / selected 状态。

### 连续摘要与阅读列

连续摘要共享一个外框并以内分隔连接，摘要值使用 `summary`。内容列表行与正文共享工作区，局部内容滚动保留标题与当前操作。AI 输入与输出共用从滚动区域测量的实际宽度与左偏移，最大（780px），左右总留白宽屏（64px）、窄桌面（40px）。用户气泡随文字长度收缩并贴右；Markdown 表格、代码与公式可在内容内部滚动。

### 日历时间网格

日 / 周视图的全天安排位于独立顶部带；时间轴每小时（64px），完整网格高（1536px），每半小时提供带日期和时间标签的创建按钮。普通新建默认（30分钟），跨午夜结束日期随起止时间确定。跨日事件在各日期列内按当天边界分段；宽周视图与日视图将重叠事件并排。已结束安排使用控件浅灰与辅助灰，状态及当前时间每分钟更新。

不超过（1050px）的周视图将冲突组替换为占满对应日期列的按钮，清楚显示时间范围和“N 项安排”，点击进入该日视图查看独立事件。全天带与时间网格继续按日期对齐，主网格保持局部滚动。这些尺寸、聚合和导航规则只描述日程页，不提升为全局布局或字号 token。

### Activity 可展开内容块

Activity 保留浅灰侧面与（72px）标题栏，标题旁的助理图像为（28px），标题 / 助理说明分行显示。这是执行轨迹面内的身份图像，不替代侧栏（36px）的 SJTU 品牌标志。

一轮执行使用块级可展开容器：摘要行排列节点、任务名称、状态与箭头，展开内容从摘要下方独立起行；运行中步骤可沿用紧凑横向行。工具组在展开内容中逐项排列，结果先呈现可读摘要，技术输出按需展开。滚动区域禁止横向溢出，长标题、路径与结果换行，代码输出保留换行并限制在本列。以上是 Activity 的局部模式，不改变主页面分栏或全局字号。

### 播放器内字幕与工具

普通窗口播放器保持（16:9），视频内容完整包含在播放器内；全屏模式随视口展开。字幕、PPT 与工具区均在播放器内部叠放，录像列表仍在播放器外的独立内容列。

工具浮层位于原生播放控制区上方（距底84px），本轮捕获显示保留（85px）的控制空间。展开时工具面限制自身高度并在内部滚动，不覆盖播放、进度与全屏操作；字幕位置随工具展开调整。字幕时间采用等宽数字的小标签，与可选择的字幕正文分列，当前段使用局部深色选中底，键盘焦点明确可见。

这些比例、控制留白、字幕小字号与局部深色只描述播放器模式，不新增全局 spacing、typography 或颜色 token。参考捕获使用合成媒体，只支持布局与状态表达核对，不代表真实课程媒体或外部服务完成验收。

### 同步状态面板

同步使用锚点浮层中的单一纵向面板：学习数据范围、自动同步状态、上次同步、立即同步操作和内嵌 MD 转换进度按顺序排列。自动状态与频率来自实际返回结果；状态读取失败时显示不可用，不生成运行中状态。

面板内部使用（14px）间距、（16px）留白，最大高度取（740px）与视口减（120px）的较小值并滚动。内嵌进度作为面板中的普通块展示，取消独立定位、额外投影和嵌套浮窗；复用现有浅色面、细边界与按钮。具体尺寸属于同步浮层，不提升为全局布局 token。

## Do's and Don'ts

### Do:

- **Do** 复用新系统 token 与同一品牌组件，保持工作台和 AI 的空间连续。
- **Do** 以文字层级、低彩度选中态、真实内容和细分隔组织信息。
- **Do** 保留局部列表 / 阅读滚动，窄窗口按阅读顺序下移辅助区域。
- **Do** 保持键盘焦点可见、占位文字可读，并尊重减少动态效果。
- **Do** 让主题提供真实颜色变化，同时保持页面结构和品牌位置。

### Don't:

- **Don't** 重新引入旧 Aura 玻璃样式或跨版本全局覆盖。
- **Don't** 把当前学习工作台改回仅有聊天与 Activity 的强制三栏。
- **Don't** 用大面积高饱和背景、厚边框或普遍投影改写固定审阅版方向。
- **Don't** 把本页局部小字号、播放器深色、兼容别名或未验收状态提升为全局规则。
- **Don't** 让外观标签逐字断行，或让切页后的标题出现默认控件焦点框。

## 本轮日历与输入细节

日期表头、全天带和时间网格共享列数与时间轴宽度，并补偿实际滚动条占位。当天数字使用（30px）固定蓝圈，数字与星期居中。23:59/次日00:00截止作业显示于对应日全天带，并标注23:59/24:00截止。首次日/周定位取当前时间减150分钟并向下取整到半小时，03:00前和23:00后不强制定位，时间刷新不重置手动滚动。

Mac日历只在左栏显示名称与连接状态，状态按钮展开锚定配置浮层。日程编辑宽（460px），开始/结束日期并排，日期使用可输入的应用月历；备注两行，保留草稿的全天、跨日和提醒状态。旧本机全天日程迁移保留全天属性。

2.0输入工具行只显示一个执行按钮：运行无草稿为暂停，输入补充后恢复发送，经现有steer链路交给当前任务。工作区和权限浮层在入口上方，保持输入区与正文位置不动。以上属于本轮局部交互，不改变全局token。本轮已完成 Mac 构建和部署，1000/1440 原生窗口测量确认日历三带列线与 AI 输入/正文边界差为0px；长配置浮层在外框内滚动，外框底部位于入口上方6px。

本批视频控制、字幕时间标签、学习工具和可拖动 PPT 浮窗均放在播放器内部；双击浮窗切换主视角。Skill 配置位于中央，内置/外部页签分开，导入入口仅在外部；只有启用开关，对话多选以输入框上方附件呈现。MCP 使用中央配置、服务开关和逐工具授权。作业禁用按钮浅灰底深色文字，悬浮保持对比度。新 CSS 使用普通规则，避免 scope 插件重复处理嵌套规则。

## 2026-11-28：录像点击与长字幕

录像标题和可见播放标识共用覆盖卡片主体的播放按钮，更多菜单保持独立热区。16:9框内使用自定义控制栏；底部自动字幕最多54px（两行），保留全文并局部滚动。字幕列表展开时隐藏自动字幕，收起时恢复。字幕详情锚定播放器，原始VTT时间标签可点击定位并跟随当前段；Markdown原始资料仍按原文阅读。MCP配置新增Bing模板，启动路径由桌面端生成，搜索失败明确显示原因。以上仅为对应页面局部规则。

## 当前字幕列表布局（2026-11-28）

整块字幕列表从画面内浮层迁到播放器下方，与播放器共享左右边界，间距12px。资料区采用白底、细灰边界、黑色13px正文与浅灰时间标签；选中段加粗。列表默认展开，自身高度最多240px/30vh，内部滚动；收起仅保留页签和操作行。视频维持16:9，单句播放字幕、播放控制和PPT仍是画面内控件。此条替代此前关于整块学习面板在播放器内展开的布局要求。
