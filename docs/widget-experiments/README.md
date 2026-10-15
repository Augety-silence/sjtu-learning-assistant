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
