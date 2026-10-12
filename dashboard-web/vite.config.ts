import { fileURLToPath, URL } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  base: "./",
  plugins: [react()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes("/node_modules/")) return;
          if (/\/node_modules\/(react|react-dom|scheduler)\//.test(id))
            return "react-vendor";
          if (
            /\/node_modules\/(motion|framer-motion|motion-dom|motion-utils)\//.test(
              id,
            )
          )
            return "motion-vendor";
          if (
            /\/node_modules\/(.*(remark|rehype|micromark|mdast|hast|unist|markdown|unified|vfile).*|bail|trough|zwitch)\//.test(
              id,
            )
          )
            return "markdown-vendor";
        },
      },
    },
  },
});
