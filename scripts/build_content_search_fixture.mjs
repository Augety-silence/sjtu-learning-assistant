import { build } from "../dashboard-web/node_modules/vite/dist/node/index.js";
process.chdir("dashboard-web");
await build({ configFile: "vite.config.ts", build: {
  outDir: "content-search-preview-dist",
  rollupOptions: { input: "tests/fixtures/content-search.html" },
} });
