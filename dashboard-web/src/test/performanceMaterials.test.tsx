// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { MaterialsView } from "@/components/MaterialsView";
import { filesBelow } from "@/lib/materialTree";
import type { MaterialTree } from "@/lib/types";
import { cleanup, fireEvent, render, screen } from "@/test/render";

vi.mock("@/lib/materialTree", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/lib/materialTree")>();
  return { ...original, filesBelow: vi.fn(original.filesBelow) };
});
const tree: MaterialTree = {
  categories: [
    { id: "courseware", label: "课件" },
    { id: "other", label: "其他" },
  ],
  download_statuses: ["all", "pending", "downloaded"],
  root: {
    id: "root",
    kind: "root",
    name: "全部资料",
    children: Array.from({ length: 40 }, (_, courseIndex) => ({
      id: `course:${courseIndex}`,
      kind: "course",
      name: `课程 ${courseIndex}`,
      course_id: String(courseIndex),
      children: Array.from({ length: 100 }, (_, fileIndex) => ({
        id: `file:${courseIndex}:${fileIndex}`,
        kind: "file",
        source_id: `${courseIndex}:${fileIndex}`,
        course_id: String(courseIndex),
        name:
          courseIndex === 39 && fileIndex === 99
            ? "Needle.pdf"
            : `讲义 ${courseIndex}-${fileIndex}.pdf`,
        category: "courseware",
        download_status: "pending",
      })),
    })),
  },
};
vi.mock("@/lib/api", () => ({ invoke: vi.fn(async () => tree) }));
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("material tree render workload", () => {
  it("keeps ordinary directory browsing local and flattens 4000 files only once per active search", async () => {
    render(<MaterialsView />);
    const course = await screen.findByRole("treeitem", { name: "课程 0" });
    expect(filesBelow).not.toHaveBeenCalled();
    fireEvent.click(course);
    expect(
      await screen.findByRole("listitem", { name: "拖动资料：讲义 0-99.pdf" }),
    ).toBeTruthy();
    expect(screen.queryByText("Needle.pdf")).toBeNull();
    expect(filesBelow).not.toHaveBeenCalled();
    const search = screen.getByRole("textbox", { name: "搜索文件名" });
    fireEvent.change(search, { target: { value: " needle " } });
    expect(
      await screen.findByRole("listitem", { name: "拖动资料：Needle.pdf" }),
    ).toBeTruthy();
    expect(filesBelow).toHaveBeenCalledTimes(1);
    fireEvent.change(search, { target: { value: "NEEDLE.PDF" } });
    fireEvent.change(screen.getByRole("combobox", { name: "分类" }), {
      target: { value: "other" },
    });
    expect(screen.queryByText("Needle.pdf")).toBeNull();
    fireEvent.change(screen.getByRole("combobox", { name: "分类" }), {
      target: { value: "courseware" },
    });
    expect(screen.getByText("Needle.pdf")).toBeTruthy();
    fireEvent.change(screen.getByRole("combobox", { name: "状态" }), {
      target: { value: "downloaded" },
    });
    expect(screen.queryByText("Needle.pdf")).toBeNull();
    fireEvent.change(screen.getByRole("combobox", { name: "状态" }), {
      target: { value: "pending" },
    });
    expect(screen.getByText("Needle.pdf")).toBeTruthy();
    expect(filesBelow).toHaveBeenCalledTimes(1);
    fireEvent.change(search, { target: { value: " " } });
    expect(
      screen.getByRole("listitem", { name: "拖动资料：讲义 0-99.pdf" }),
    ).toBeTruthy();
    expect(screen.queryByText("Needle.pdf")).toBeNull();
    expect(filesBelow).toHaveBeenCalledTimes(1);
  });
});
