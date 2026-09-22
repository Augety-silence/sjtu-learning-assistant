import { describe, expect, it } from "vitest";
import {
  containerChildren,
  filesBelow,
  findNodePath,
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
  it("finds paths and descendants", () => {
    expect(findNodePath(tree, "course")?.map((node) => node.id)).toEqual([
      "root",
      "term",
      "course",
    ]);
    expect(filesBelow(tree).map(({ file }) => file.source_id)).toEqual(["1"]);
    expect(containerChildren(tree).map((node) => node.id)).toEqual(["term"]);
  });

  it("returns null for missing nodes", () => {
    expect(findNodePath(tree, "missing")).toBeNull();
  });
});
