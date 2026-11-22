// Isolated fixture build; never replace the production dashboard dist.
import { build } from "../dashboard-web/node_modules/vite/dist/node/index.js";
process.chdir("dashboard-web");
await build({ configFile: "vite.config.ts", build: {
  outDir: "ui-hardening-preview-dist",
  rollupOptions: { input: "tests/fixtures/ui-hardening.html" },
} });
