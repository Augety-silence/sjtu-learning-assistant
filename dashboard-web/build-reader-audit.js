// 单独构建合成原生验收页，不覆盖生产 dist，也不包含真实用户资料。
import { stat } from "node:fs/promises";
import { isAbsolute, resolve } from "node:path";
import { build } from "vite";

const output = process.argv[2];
const entry = process.argv[3] ?? "transcript-reader-audit.html";
if (
  !["transcript-reader-audit.html", "knowledge-reader-audit.html"].includes(
    entry,
  )
)
  throw new Error("仅支持项目内的合成验收入口");
if (!output || !isAbsolute(output) || !(await stat(output)).isDirectory())
  throw new Error("请提供已创建的独立绝对路径目录");
const root = import.meta.dirname;
if (resolve(output) === root || resolve(output) === resolve(root, "dist"))
  throw new Error("验收不能覆盖源码或生产构建目录");
process.chdir(root);
await build({
  root,
  configFile: resolve(root, "vite.config.ts"),
  build: {
    outDir: output,
    emptyOutDir: false,
    rollupOptions: { input: resolve(root, entry) },
  },
});
