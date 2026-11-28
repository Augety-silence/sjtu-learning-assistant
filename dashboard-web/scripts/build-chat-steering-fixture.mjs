// 独立原生验收产物，不进入生产应用。
import { build } from "vite";
await build({
  build: {
    outDir: "chat-steering-preview-dist",
    rollupOptions: { input: "tests/fixtures/chat-steering.html" },
  },
});
