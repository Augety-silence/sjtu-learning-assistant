// 隔离 UI 验收入口，不覆盖应用 dist，不调用网络或真实学习数据。
import { build } from "../dashboard-web/node_modules/vite/dist/node/index.js";

process.chdir("dashboard-web");
await build({
  root: ".",
  configFile: "vite.config.ts",
  build: {
    outDir: "progress-preview-dist",
    rollupOptions: { input: "tests/fixtures/markdown-progress.html" },
  },
});
