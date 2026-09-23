import type { MaterialNode } from "@/lib/types";

export interface VisibleMaterialTreeItem {
  id: string;
  node: MaterialNode;
  parentId?: string;
  level: number;
}

export function containerChildren(node: MaterialNode): MaterialNode[] {
  return (node.children ?? []).filter((child) => child.kind !== "file");
}

export function visibleTreeItems(
  root: MaterialNode,
  expandedIds: ReadonlySet<string>,
): VisibleMaterialTreeItem[] {
  const result: VisibleMaterialTreeItem[] = [
    { id: root.id, node: root, level: 1 },
  ];

  const visit = (parent: MaterialNode, parentId: string, level: number) => {
    if (!expandedIds.has(parentId)) return;
    for (const node of containerChildren(parent)) {
      result.push({ id: node.id, node, parentId, level });
      visit(node, node.id, level + 1);
    }
  };

  visit(root, root.id, 2);
  return result;
}

export function directFiles(
  node: MaterialNode,
  path: MaterialNode[] = [],
): Array<{ file: MaterialNode; path: MaterialNode[] }> {
  const filePath = node.kind === "root" ? path : [...path, node];
  return (node.children ?? [])
    .filter((child) => child.kind === "file")
    .map((file) => ({ file, path: filePath }));
}

export function filesBelow(
  node: MaterialNode,
  path: MaterialNode[] = [],
): Array<{ file: MaterialNode; path: MaterialNode[] }> {
  const nextPath = node.kind === "root" ? path : [...path, node];
  return (node.children ?? []).flatMap((child) =>
    child.kind === "file"
      ? [{ file: child, path: nextPath }]
      : filesBelow(child, nextPath),
  );
}

export function findNodePath(
  root: MaterialNode,
  id: string,
  trail: MaterialNode[] = [],
): MaterialNode[] | null {
  const next = [...trail, root];
  if (root.id === id) return next;
  for (const child of root.children ?? []) {
    const result = findNodePath(child, id, next);
    if (result) return result;
  }
  return null;
}
