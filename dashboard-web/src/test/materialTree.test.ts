import { describe, expect, it } from "vitest";
import {
  containerChildren,
  directFiles,
  filesBelow,
  findNodePath,
  visibleTreeItems,
} from "@/lib/materialTree";
import type { MaterialNode } from "@/lib/types";

const tree: MaterialNode = {
  id: "root",
  kind: "root",
  name: "全部资料",
  children: [
    {
      id: "term",
      kind: "term",
      name: "2026 Fall",
      children: [
        {
          id: "course",
          kind: "course",
          name: "NLP",
          children: [
            { id: "file:1", kind: "file", name: "lecture.pdf", source_id: "1" },
          ],
        },
      ],
    },
  ],
};

describe("material tree helpers", () => {
  it("does not flatten nested files into a parent while browsing", () => {
    expect(directFiles(tree)).toEqual([]);
    expect(directFiles(tree.children?.[0] as MaterialNode)).toEqual([]);
  });

  it("recursively finds nested files for search", () => {
    expect(filesBelow(tree).map(({ file }) => file.name)).toEqual([
      "lecture.pdf",
    ]);
  });

  it("finds paths and descendants", () => {
    expect(findNodePath(tree, "course")?.map((node) => node.id)).toEqual([
      "root",
      "term",
      "course",
    ]);
    expect(filesBelow(tree).map(({ file }) => file.source_id)).toEqual(["1"]);
    expect(containerChildren(tree).map((node) => node.id)).toEqual(["term"]);
  });

  it("按展开状态生成深度优先的可见节点及父级信息", () => {
    expect(visibleTreeItems(tree, new Set(["root"]))).toEqual([
      { id: "root", node: tree, level: 1 },
      {
        id: "term",
        node: tree.children?.[0],
        parentId: "root",
        level: 2,
      },
    ]);

    const visible = visibleTreeItems(tree, new Set(["root", "term", "course"]));
    expect(visible.map(({ id }) => id)).toEqual(["root", "term", "course"]);
    expect(visible[2]).toMatchObject({ parentId: "term", level: 3 });
  });

  it("returns null for missing nodes", () => {
    expect(findNodePath(tree, "missing")).toBeNull();
  });
});
