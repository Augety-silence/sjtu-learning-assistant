export interface TextMatch {
  start: number;
  end: number;
}

/** Literal, case-insensitive matching preserves source offsets and never executes user regex. */
export function findTextMatches(text: string, query: string): TextMatch[] {
  const needle = query.trim();
  if (!needle) return [];
  const pattern = new RegExp(
    needle.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"),
    "gi",
  );
  return Array.from(text.matchAll(pattern), (match) => ({
    start: match.index!,
    end: match.index! + match[0].length,
  }));
}

type SearchNode = {
  type: string;
  value?: string;
  tagName?: string;
  properties?: Record<string, unknown>;
  children?: SearchNode[];
};
const blocks = new Set([
  "p",
  "div",
  "section",
  "li",
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
  "pre",
  "td",
  "th",
  "br",
]);

/** Highlight the rendered text, including matches split by emphasis or links. */
export function rehypeSearchHighlight({
  query,
  scope,
}: {
  query: string;
  scope: string;
}) {
  return (tree: SearchNode) => {
    if (!query.trim()) return;
    const leaves: {
      parent: SearchNode;
      node: SearchNode;
      start: number;
      end: number;
    }[] = [];
    let text = "";
    function collect(parent: SearchNode) {
      for (const node of parent.children ?? []) {
        if (node.type === "text" && node.value) {
          const start = text.length;
          text += node.value;
          leaves.push({ parent, node, start, end: text.length });
        } else if (
          node.children &&
          !["script", "style", "math"].includes(node.tagName ?? "") &&
          !String(node.properties?.className ?? "").includes("katex")
        ) {
          collect(node);
        }
        if (blocks.has(node.tagName ?? "")) text += "\n";
      }
    }
    collect(tree);
    const matches = findTextMatches(text, query);
    let cursor = 0;
    for (const leaf of leaves) {
      while (cursor < matches.length && matches[cursor].end <= leaf.start)
        cursor++;
      const parts: SearchNode[] = [];
      let offset = 0;
      for (
        let index = cursor;
        index < matches.length && matches[index].start < leaf.end;
        index++
      ) {
        const start = Math.max(matches[index].start, leaf.start) - leaf.start;
        const end = Math.min(matches[index].end, leaf.end) - leaf.start;
        if (start > offset)
          parts.push({
            type: "text",
            value: leaf.node.value!.slice(offset, start),
          });
        parts.push({
          type: "element",
          tagName: "mark",
          properties: {
            className: ["content-search-hit"],
            "data-search-hit": `${scope}:${index}`,
          },
          children: [
            { type: "text", value: leaf.node.value!.slice(start, end) },
          ],
        });
        offset = end;
      }
      if (!parts.length) continue;
      if (offset < leaf.node.value!.length)
        parts.push({ type: "text", value: leaf.node.value!.slice(offset) });
      const at = leaf.parent.children!.indexOf(leaf.node);
      leaf.parent.children!.splice(at, 1, ...parts);
    }
  };
}
