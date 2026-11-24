import type { Root, RootContent, Text } from "mdast";

// 模型说明常在粗体闭合前多放一个空格。仅修正说明的呈现，不改原文、主答案或代码。
export function remarkExecutionStrong() {
  const split = (node: Text): RootContent[] => {
    const parts: RootContent[] = [];
    const pattern = /\*\*([^\s*](?:[^*\n]*?[^\s*])?)[ \t]+\*\*/g;
    let cursor = 0;
    for (const match of node.value.matchAll(pattern)) {
      if (match.index > cursor)
        parts.push({
          type: "text",
          value: node.value.slice(cursor, match.index),
        });
      parts.push({
        type: "strong",
        children: [{ type: "text", value: match[1] }],
      });
      cursor = match.index + match[0].length;
    }
    if (cursor === 0) return [node];
    if (cursor < node.value.length)
      parts.push({ type: "text", value: node.value.slice(cursor) });
    return parts;
  };
  const walk = (node: Root | RootContent) => {
    if (!("children" in node)) return;
    // 只替换 text；代码、链接地址等结构不经过正则处理。
    const children = node.children.flatMap((child) => {
      if (child.type === "text") return split(child);
      walk(child);
      return [child];
    });
    // 插入的节点只可能是合法的行内 text / strong，不改变父级结构。
    node.children = children as typeof node.children;
  };
  return (tree: Root) => walk(tree);
}
