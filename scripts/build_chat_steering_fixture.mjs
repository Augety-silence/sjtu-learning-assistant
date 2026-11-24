// 只构建隔离验收页面；不替换生产 dashboard-web/dist。
import { fileURLToPath } from "node:url";
import { build } from "../dashboard-web/node_modules/vite/dist/node/index.js";

const root = fileURLToPath(new URL("../dashboard-web/", import.meta.url));
await build({
  root,
  configFile: `${root}vite.config.ts`,
  build: {
    outDir: "chat-steering-preview-dist",
    emptyOutDir: true,
    rollupOptions: { input: `${root}tests/fixtures/chat-steering.html` },
  },
});
