// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { KnowledgeFolders } from "@/components/KnowledgeFolders";
import { cleanup, fireEvent, render, screen } from "@/test/render";

const folders = [
  { source_id: "a", path: "数据库", name: "数据库", count: 2 },
  { source_id: "a", path: "数据库/第一讲", name: "第一讲", count: 2 },
  { source_id: "b", path: "数据库", name: "数据库", count: 1 },
  { source_id: "b", path: "数据库/第一讲", name: "第一讲", count: 1 },
];

afterEach(cleanup);

describe("KnowledgeFolders", () => {
  it("同名目录只高亮所选来源，使用原生 summary 选中与展开", () => {
    const onSelect = vi.fn();
    render(
      <KnowledgeFolders
        folders={folders}
        selected="数据库"
        selectedSource="a"
        onSelect={onSelect}
      />,
    );
    const summaries = screen
      .getAllByText("数据库")
      .map((node) => node.closest("summary")!);
    expect(summaries[0].getAttribute("aria-current")).toBe("location");
    expect(summaries[1].hasAttribute("aria-current")).toBe(false);
    fireEvent.click(summaries[1]);
    expect(onSelect).toHaveBeenCalledExactlyOnceWith("b", "数据库");
    expect(screen.queryByText("查看此文件夹全部课件")).toBeNull();
  });

  it("已选中文件夹可收起，不重复查询或重置正在阅读的正文", () => {
    const onSelect = vi.fn();
    render(
      <KnowledgeFolders
        folders={folders.slice(0, 2)}
        selected="数据库"
        selectedSource="a"
        onSelect={onSelect}
      />,
    );
    const summary = screen.getByText("数据库").closest("summary")!;
    expect(summary.closest("details")?.open).toBe(true);
    fireEvent.click(summary);
    expect(summary.closest("details")?.open).toBe(false);
    expect(onSelect).not.toHaveBeenCalled();
  });
});
