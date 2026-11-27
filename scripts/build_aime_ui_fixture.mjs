import { fileURLToPath } from "node:url";
import { build } from "../dashboard-web/node_modules/vite/dist/node/index.js";
const root = fileURLToPath(new URL("../dashboard-web/", import.meta.url));
process.chdir(root);
await build({ root, configFile: `${root}vite.config.ts`, build: {
  outDir: "aime-preview-dist", emptyOutDir: true,
  rollupOptions: { input: `${root}tests/fixtures/aime-execution.html` },
} });
