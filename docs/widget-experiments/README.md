# Widget Experiments · 学习小组件实验

> 状态：**实验中，未接入正式页面**。分支 `experiments/learning-widgets`，基于开发快照 `d167f82`，Draft PR 的 base 为 `aime/1791949000-ai-reliability-streaming`，暂不合并。

这组组件是可以插入现有页面的小部件，供本地代理挑选后接入验证。约束如下：

- 组件代码在 `dashboard-web/src/components/experiments/`，每个组件单独导出、样式单独存放，只通过 props 和回调工作，不请求接口，也不调用模型。
- 演示代码在 `dashboard-web/src/experiments/`，入口为 `dashboard-web/widget-lab.html`。正式 `App` 路由、`App.tsx`、`index.css`、业务组件、后端、`package.json` 与 lockfile 均未改动。
- 只复用仓库已有的 React、TypeScript、`motion/react`、`lucide-react`、`Button`、`CompactControls`、`lib/motion.ts` 和主题变量，没有新增依赖。
- 样式统一使用 `slw-` 前缀（sjtu learning widget），颜色全部来自现有主题变量，浅色和深色主题会自动切换。
- 动效只使用 `motionDuration` / `motionEase` 定义的短淡入和小范围位移。每个组件都有 `motion?: "auto" | "static"` 属性：`auto` 会在系统开启"减少动态效果"或外层 `MotionConfig reducedMotion` 生效时自动降级为静态，`static` 则始终静态。

## 预览入口

```bash
cd dashboard-web
npm run dev
# 浏览器打开 http://localhost:5173/widget-lab.html
```

`widget-lab.html` 由现有 Vite 开发服务器直接提供，没有改 `vite.config.ts`，`npm run build` 仍只产出正式应用。预览页顶部可以切换：

- 主题：浅色 / 深色 / 跟随系统（复用 `applyThemeMode`）。
- 宽度：常规 / 窄屏 340px。
- 动态效果：跟随系统 / 模拟减少动态效果（`MotionConfig reducedMotion="always"`）。

每个用例都会把"静态"和"轻动效"两个独立实例并排展示，方便对比。演示数据全部为合成数据，并在页面上明确标记。

## 测试

```bash
cd dashboard-web
npx vitest run src/test/experiments
npm run lint
npm run build
```

## 组件

### 1. ReadyRangeBar · 学习材料可用时间带

文件：`dashboard-web/src/components/experiments/ReadyRangeBar.tsx` / `ready-range-bar.css`（前缀 `slw-range-`）

| Prop | 类型 | 说明 |
| --- | --- | --- |
| `durationMs` | `number \| null` | 总时长（毫秒）。缺失、`NaN`、`<= 0` 视为未知，此时不画比例时间带，改为列出区间 |
| `currentTimeMs` | `number \| null` | 播放位置（毫秒）。缺失或非法时显示"播放位置未知"；越界会被夹到 `[0, durationMs]` |
| `availableRangesMs` | `[startMs, endMs][] \| null` | 已有学习材料的区间。组件会丢弃 `NaN`、倒置和零长度区间，并完成裁剪、排序和重叠合并（`normalizeRangesMs` 同时单独导出） |
| `onSeekMs` | `(ms) => void` | 可选。不传时组件只读（`role="img"`）。传入后可以点击或用键盘选择候选位置，**只有点"确认跳转"或按 Enter 才会调用**；props 变化和动画都不会触发跳转 |
| `stepMs` | `number` | 方向键步长，默认 5000；按住 Shift 或用 PageUp/PageDown 时为 6 倍 |
| `motion` | `"auto" \| "static"` | 默认 `auto`：首帧已有的区间直接显示，后续新增的区间做短淡入和横向展开（`motionDuration.enter`），播放指示器用 `motionDuration.control` 平移 |

键盘操作：`←/→/↑/↓` 移动候选位置，`Home`/`End` 跳到首尾，`Enter` 确认，`Escape` 取消候选。

组件只显示"已有材料覆盖 N 段"和"当前位置有/暂无学习材料"，并固定附一句"覆盖只表示已有材料，不代表已校对或质量通过"。**不显示**整理百分比、预计完成时间或质量结论。

**推荐**：放在同步学习面板顶部，并把 `onSeekMs` 接到播放器的 seek 方法上。由于需要二次确认，它不会和视频自带进度条的直接拖动冲突。如果放在视频正下方，建议只读（不传 `onSeekMs`），避免页面上出现两条可拖动的进度条。

最小插入示例。`VideoSynchronizedLearning` 里现有的 `availableRanges` 已经是毫秒；时长建议从 `<video>` 元素的 `duration`（单位为秒，加载前可能是 `NaN`）换算：

```tsx
import { ReadyRangeBar } from "@/components/experiments/ReadyRangeBar";

<ReadyRangeBar
  durationMs={Number.isFinite(media.duration) ? media.duration * 1000 : null}
  currentTimeMs={playbackTime * 1000}
  availableRangesMs={visible?.availableRanges}
  onSeekMs={(ms) => onSeek(ms / 1000)}
/>
```

### 2. EvidencePeek · 可展开的字幕证据

文件：`dashboard-web/src/components/experiments/EvidencePeek.tsx` / `evidence-peek.css`（前缀 `slw-evidence-`）

| Prop | 类型 | 说明 |
| --- | --- | --- |
| `quote` | `string \| null` | 字幕引文。缺失或只有空白时显示"未提供字幕引文。" |
| `sourceLabel` | `string \| null` | 来源标签。缺失时显示"来源未标注" |
| `startMs` / `endMs` | `number \| null` | 时间（毫秒）。开始时间非法时显示"时间未知"；结束时间缺失或不晚于开始时间时只显示开始时间，不补造结束时间（`resolveEvidenceTiming` 单独导出） |
| `onSeekMs` | `(ms) => void` | 可选。传入且时间有效时，展开后显示"跳到 mm:ss"按钮，**只有点击该按钮才会调用**；时间无效时改为说明"缺少有效时间，无法跳转视频。" |
| `variant` | `"inline" \| "popover"` | 默认 `inline`（推荐）。`popover` 为轻浮层 |
| `motion` | `"auto" \| "static"` | `inline` 用高度 + 淡入（`motionDuration.enter`），`popover` 用 4px 位移 + 淡入（`motionDuration.popoverEnter`） |
| `defaultOpen` | `boolean` | 非受控初始状态，主要用于预览 |

默认只显示紧凑的"来源 · 时间"触发按钮（带 `aria-expanded` / `aria-controls`）。键盘：Tab 聚焦，Enter/空格展开或收起；展开后焦点仍留在触发按钮上，不会自动移到跳转按钮；在组件内任意位置按 `Escape` 收起并把焦点还给触发按钮。`popover` 额外支持点击外部或焦点离开组件时收起，这种情况下不移动焦点。组件全部使用 `span`，可以直接嵌在 `<p>` 或列表项里。

**推荐 inline**：展开内容在文档流内，不会被滚动容器裁切，也不会遮住相邻知识点；窄面板（如同步学习侧栏）里表现稳定；焦点和关闭逻辑最简单。`popover` 只建议用在行高紧凑、不希望撑开布局的密集列表中，并且宿主容器不能设置 `overflow: hidden`。

最小插入示例（讲义知识点后附证据）：

```tsx
import { EvidencePeek } from "@/components/experiments/EvidencePeek";

<p>
  {point.text}
  <EvidencePeek
    quote={point.evidence?.quote}
    sourceLabel={point.evidence?.sourceLabel}
    startMs={point.evidence?.startMs}
    endMs={point.evidence?.endMs}
    onSeekMs={(ms) => onSeek(ms / 1000)}
  />
</p>
```

### 3. RecallCard · 先想再看的复习卡

文件：`dashboard-web/src/components/experiments/RecallCard.tsx` / `recall-card.css`（前缀 `slw-recall-`）

| Prop | 类型 | 说明 |
| --- | --- | --- |
| `question` | `string \| null` | 题目。缺失或只有空白时显示"未提供题目。" |
| `answer` | `string \| null` | 参考答案，完全由宿主提供。缺失时显示"未提供参考答案。可以回到视频或讲义自行核对。"，不生成任何内容 |
| `revealed` | `boolean` | **受控**。为 `false` 时答案节点根本不渲染 |
| `onRevealChange` | `(next: boolean) => void` | 用户点"显示答案 / 隐藏答案"时调用；是否真的切换由宿主决定 |
| `sourceLabel` | `string \| null` | 可选的出处标签，由宿主传入 |
| `variant` | `"expand" \| "crossfade"` | 默认 `expand`（推荐）：答案在题目下方局部展开。`crossfade`：占位提示与答案在同一位置交替淡入 |
| `motion` | `"auto" \| "static"` | `expand` 用高度 + 淡入（`motionDuration.enter`），`crossfade` 用短淡入（`motionDuration.fast`） |
| `label` | `string` | 题目上方的小标题，默认"复习题" |

可访问性：切换按钮是原生 `<button>`，带 `aria-expanded`，展开后才加 `aria-controls` 指向答案区域（`role="region"`，名称"参考答案"）。隐藏状态下答案不在 DOM 中；带动效收起时，退场中的节点会被设为 `inert` + `aria-hidden`，所以读屏和 Tab 在任何时刻都到不了隐藏的答案。切换时焦点留在按钮上。

组件不调用模型，不评判对错，也不显示正确率、掌握度或"已学会"一类的状态。

**推荐 expand**：题目位置不变，答案出现在下方，阅读顺序自然；答案长短不一时不会出现两段内容叠在同一格里的高度跳动。`crossfade` 适合答案很短、需要固定卡片高度的网格布局。

最小插入示例（讲义复习题区，宿主保证一次只展开一张）：

```tsx
import { RecallCard } from "@/components/experiments/RecallCard";

const [openId, setOpenId] = useState<string | null>(null);

{reviewQuestions.map((item) => (
  <RecallCard
    key={item.id}
    question={item.question}
    answer={item.answer}
    sourceLabel={item.sourceLabel}
    revealed={openId === item.id}
    onRevealChange={(next) => setOpenId(next ? item.id : null)}
  />
))}
```

### 4. MaterialUpdateNotice · 材料更新轻提示

文件：`dashboard-web/src/components/experiments/MaterialUpdateNotice.tsx` / `material-update-notice.css`（前缀 `slw-update-`）

| Prop | 类型 | 说明 |
| --- | --- | --- |
| `revision` | `string \| number \| null` | 材料的修订号（例如 `learningRevision`），只做相等比较。第一个有效值作为基线，不提示；之后出现**不同的**有效值才提示。`null`、`undefined`、`""`、`NaN` 视为未知，直接忽略，也不会覆盖基线 |
| `message` | `string \| null` | 可选的附加说明，由宿主提供，原样显示（窄屏时省略号截断） |
| `onView` | `() => void` | 可选。传入后显示"查看"按钮，**只有用户点击时才调用**，调用后提示收起 |
| `autoHideMs` | `number \| null` | 自动收起时间，默认 6000。鼠标悬停或焦点在提示内时暂停；`null` / `0` 表示一直保留到用户关闭 |
| `motion` | `"auto" \| "static"` | 轻动效为 4px 横移 + 淡入（`motionDuration.popoverEnter`），收起只淡出；减少动态效果时直接显示和移除 |

行为约定：

- 提示文字固定为"材料有更新"，不推断"已全部完成"或"可以开始复习"。
- 同一修订号的轮询、父组件普通重渲染、StrictMode 的双次 effect 都不会重播。提示可见期间修订号继续变化时，沿用同一个元素，不重新入场，只把自动收起计时重新开始，所以快速连续更新时不会闪烁。
- 外层 `role="status"` + `aria-live="polite"` 常驻，提示插入时由读屏礼貌播报；组件不调用 `focus()`、`scrollIntoView()` 或 `scrollTo()`。
- 计时器在收起、暂停和卸载时都会清理；hook `useRevisionChange(revision)` 单独导出，宿主也可以只用它判断"是否真的有新修订"。

**推荐**：放在同步讲义面板副标题右侧，`revision` 直接接面板已有的 `learningRevision`（或 `sourceVersion`），`onView` 接到"滚动到最新段落"这类宿主自己的动作上。滚动由宿主在用户点击后执行，组件本身不滚动。

最小插入示例：

```tsx
import { MaterialUpdateNotice } from "@/components/experiments/MaterialUpdateNotice";

<div className="panel-subtitle">
  <span>同步讲义</span>
  <MaterialUpdateNotice
    revision={learningRevision}
    onView={() => scrollToLatestSection()}
  />
</div>
```

## 推荐汇总

| 组件 | 推荐形态 | 建议接入点 | 注意 |
| --- | --- | --- | --- |
| ReadyRangeBar | 带 `onSeekMs` 的二次确认时间带 | 同步学习面板顶部 | 放在视频正下方时建议只读 |
| EvidencePeek | `inline` | 讲义知识点、纠错说明、待核对项 | `popover` 只用于密集列表，且容器不能裁切 |
| RecallCard | `expand` | 视频讲义复习题区 | 展开状态由宿主管理，可做"一次只展开一张" |
| MaterialUpdateNotice | 默认 6 秒自动收起 | 同步讲义面板副标题旁 | `revision` 必须是稳定的修订号，不要传时间戳或每次轮询都变的值 |

四个组件都只依赖 props 和回调，没有请求接口、没有全局状态，可以单独挑选接入。
