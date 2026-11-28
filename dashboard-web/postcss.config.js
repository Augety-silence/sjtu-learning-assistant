import autoprefixer from "autoprefixer";
import tailwindcss from "tailwindcss";

// 旧界面规则只作用于经典模式；2.0拥有自己的完整布局与控件样式。
const classicScope = {
  postcssPlugin: "sjtu-classic-scope",
  OnceExit(root, { result }) {
    root.walkRules((rule) => {
      const file = (
        rule.source?.input.file ??
        result.opts.from ??
        ""
      ).replaceAll("\\", "/");
      if (!file.includes("/src/") || file.includes("node_modules")) return;
      let parent = rule.parent;
      while (parent) {
        if (parent.type === "atrule" && /keyframes$/i.test(parent.name)) return;
        parent = parent.parent;
      }
      if (file.includes("/src/v2/") || file.endsWith("/ui-2.css")) {
        rule.selectors = rule.selectors.map((selector) =>
          selector.includes(':root[data-ui-release="2"]')
            ? selector
            : selector.includes(":root")
              ? selector.replaceAll(":root", ':root[data-ui-release="2"]')
              : ':root[data-ui-release="2"] ' + selector,
        );
        return;
      }
      rule.selectors = rule.selectors.map((selector) =>
        selector.includes(':root[data-ui-release="classic"]')
          ? selector
          : selector.includes(":root")
            ? selector.replaceAll(":root", ':root[data-ui-release="classic"]')
            : ':root[data-ui-release="classic"] ' + selector,
      );
    });
  },
};
export default { plugins: [tailwindcss(), autoprefixer(), classicScope] };
