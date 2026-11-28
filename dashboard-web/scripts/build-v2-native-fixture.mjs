// 完整 2.0 原生验收夹具；不会纳入生产包。
import { build } from "vite";
await build({ build: { outDir: "v2-native-preview-dist", rollupOptions: { input: "v2-visual-audit.html" } } });
