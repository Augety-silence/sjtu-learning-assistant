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
