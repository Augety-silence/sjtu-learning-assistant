// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MaterialsView } from "@/components/MaterialsView";
import {
  invoke,
  moveMaterial,
  previewMaterial,
  restoreMaterialAuto,
} from "@/lib/api";
import type { MaterialTree } from "@/lib/types";
import { cleanup, fireEvent, render, screen, waitFor } from "@/test/render";

vi.mock("@/lib/api", () => ({
  invoke: vi.fn(),
  moveMaterial: vi.fn(),
  previewMaterial: vi.fn(),
  restoreMaterialAuto: vi.fn(),
}));

const categories = [
  { id: "assignments", label: "课程作业" },
  { id: "courseware", label: "课件" },
  { id: "other", label: "其他" },
];

const tree: MaterialTree = {
  categories,
  download_statuses: ["all", "downloaded", "cloud_only", "pending", "failed"],
  root: {
    id: "root",
    kind: "root",
    name: "全部资料",
    children: [
      {
        id: "term:0",
        kind: "term",
        name: "2026 Fall",
        children: [
          {
            id: "course:course-1",
            kind: "course",
            name: "自然语言处理",
            course_id: "course-1",
            children: [
              {
                id: "category:course-1:assignments",
                kind: "category",
                name: "课程作业",
                course_id: "course-1",
                category: "assignments",
                children: [],
              },
              {
                id: "category:course-1:courseware",
                kind: "category",
                name: "课件",
                course_id: "course-1",
                category: "courseware",
                children: [
                  {
                    id: "folder:1:courseware:10",
                    kind: "folder",
                    name: "Week 1",
                    course_id: "course-1",
                    category: "courseware",
                    children: [
                      {
                        id: "file:file-1",
                        kind: "file",
                        name: "讲义.pdf",
                        source_id: "file-1",
                        course_id: "course-1",
                        category: "courseware",
                        manual_override: true,
                        download_status: "pending",
                      },
                    ],
                  },
                  {
                    id: "file:file-3",
                    kind: "file",
                    name: "概览.pdf",
                    source_id: "file-3",
                    course_id: "course-1",
                    category: "courseware",
                    download_status: "pending",
                  },
                ],
              },
              {
                id: "category:course-1:other",
                kind: "category",
                name: "其他",
                course_id: "course-1",
                category: "other",
                children: [],
              },
            ],
          },
          {
            id: "course:course-2",
            kind: "course",
            name: "数据库",
            course_id: "course-2",
            children: [
              {
                id: "category:course-2:assignments",
                kind: "category",
                name: "课程作业",
                course_id: "course-2",
                category: "assignments",
                children: [],
              },
            ],
          },
        ],
      },
    ],
  },
};

function dataTransfer() {
  return {
    effectAllowed: "none",
    dropEffect: "none",
    setData: vi.fn(),
    getData: vi.fn(),
  };
}

function findMaterial(
  node: import("@/lib/types").MaterialNode,
  sourceId: string,
): import("@/lib/types").MaterialNode | undefined {
  if (node.source_id === sourceId) return node;
  for (const child of node.children ?? []) {
    const found = findMaterial(child, sourceId);
    if (found) return found;
  }
  return undefined;
}

async function renderFile() {
  render(<MaterialsView />);
  const course = await screen.findByRole("treeitem", {
    name: "自然语言处理",
  });
  fireEvent.click(course);
  const coursewareTarget = document.querySelector<HTMLElement>(
    '[data-material-target-id="category:course-1:courseware"]',
  );
  expect(coursewareTarget).toBeTruthy();
  fireEvent.click(coursewareTarget as HTMLElement);
  const folderTarget = document.querySelector<HTMLElement>(
    '[data-material-target-id="folder:1:courseware:10"]',
  );
  expect(folderTarget).toBeTruthy();
  fireEvent.click(folderTarget as HTMLElement);
  return await screen.findByRole("listitem", { name: "拖动资料：讲义.pdf" });
}

beforeEach(() => {
  vi.clearAllMocks();
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: undefined,
  });
  Object.defineProperty(window, "innerWidth", {
    configurable: true,
    value: 1024,
  });
  vi.mocked(invoke).mockResolvedValue(tree);
  vi.mocked(moveMaterial).mockResolvedValue({
    source_id: "file-1",
    status: "saved",
    local_path: null,
  });
  vi.mocked(previewMaterial).mockResolvedValue({
    kind: "pdf",
    name: "讲义.pdf",
    data_url: "data:application/pdf;base64,cGRm",
  });
  vi.mocked(restoreMaterialAuto).mockResolvedValue({
    source_id: "file-1",
    status: "saved",
    local_path: null,
  });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
  Object.defineProperty(window, "innerWidth", {
    configurable: true,
    value: 1024,
  });
});

describe("MaterialsView manual filing", () => {
  it("supports same-course drag/drop and rejects cross-course drop with feedback", async () => {
    await renderFile();
    const secondCourse = screen.getByRole("treeitem", { name: "数据库" });
    fireEvent.click(secondCourse);
    const firstFolder = document.querySelector<HTMLElement>(
      '[data-material-target-id="folder:1:courseware:10"]',
    );
    expect(firstFolder).toBeTruthy();
    fireEvent.click(firstFolder as HTMLElement);
    const row = await screen.findByRole("listitem", {
      name: "拖动资料：讲义.pdf",
    });
    const transfer = dataTransfer();
    const sameCourse = document.querySelector<HTMLElement>(
      '[data-material-target-id="category:course-1:assignments"]',
    );
    const otherCourse = document.querySelector<HTMLElement>(
      '[data-material-target-id="category:course-2:assignments"]',
    );
    expect(sameCourse).toBeTruthy();
    expect(otherCourse).toBeTruthy();

    fireEvent.dragStart(row, { dataTransfer: transfer });
    fireEvent.dragEnter(otherCourse as HTMLElement, { dataTransfer: transfer });
    expect(otherCourse?.className).toContain("material-drop-rejected");
    fireEvent.drop(otherCourse as HTMLElement, { dataTransfer: transfer });
    expect(moveMaterial).not.toHaveBeenCalled();
    expect(await screen.findByText(/不能.*移动到其他课程/)).toBeTruthy();

    fireEvent.dragStart(row, { dataTransfer: transfer });
    fireEvent.dragEnter(sameCourse as HTMLElement, { dataTransfer: transfer });
    expect(sameCourse?.className).toContain("material-drop-allowed");
    fireEvent.drop(sameCourse as HTMLElement, { dataTransfer: transfer });
    await waitFor(() =>
      expect(moveMaterial).toHaveBeenCalledWith(
        "file-1",
        "category:course-1:assignments",
      ),
    );
  });

  it("accepts a drop on a Canvas folder in the right-hand folder list", async () => {
    render(<MaterialsView />);
    const course = await screen.findByRole("treeitem", {
      name: "自然语言处理",
    });
    expect(course.getAttribute("aria-expanded")).toBe("false");
    expect(
      screen.queryByRole("treeitem", {
        name: "课件，可接收同课程资料拖放",
      }),
    ).toBeNull();
    fireEvent.click(course);
    const categoryTarget = await screen.findByRole("treeitem", {
      name: "课件，可接收同课程资料拖放",
    });
    fireEvent.click(categoryTarget);

    const row = await screen.findByRole("listitem", {
      name: "拖动资料：概览.pdf",
    });
    const folder = await screen.findByRole("listitem", {
      name: /Week 1.*可接收同课程资料拖放/,
    });
    const transfer = dataTransfer();
    fireEvent.dragStart(row, { dataTransfer: transfer });
    fireEvent.dragEnter(folder, { dataTransfer: transfer });
    expect(folder.className).toContain("material-drop-allowed");
    fireEvent.drop(folder, { dataTransfer: transfer });
    await waitFor(() =>
      expect(moveMaterial).toHaveBeenCalledWith(
        "file-3",
        "folder:1:courseware:10",
      ),
    );
  });

  it("provides keyboard-accessible category menu and restore-auto action", async () => {
    await renderFile();
    fireEvent.click(screen.getByRole("button", { name: "恢复自动分类" }));
    await waitFor(() =>
      expect(restoreMaterialAuto).toHaveBeenCalledWith("file-1"),
    );

    fireEvent.change(
      screen.getByRole("combobox", { name: "归档“讲义.pdf”到分类" }),
      { target: { value: "other" } },
    );
    await waitFor(() =>
      expect(moveMaterial).toHaveBeenCalledWith(
        "file-1",
        "category:course-1:other",
      ),
    );
  });
});

describe("MaterialsView selection and responsive states", () => {
  it("文件单击只选择，Enter 与双击执行同一默认动作", async () => {
    const row = await renderFile();
    vi.mocked(invoke).mockClear();

    fireEvent.click(row);
    expect(row.getAttribute("aria-current")).toBe("true");
    expect(invoke).not.toHaveBeenCalled();

    fireEvent.keyDown(row, { key: "Enter" });
    await waitFor(() =>
      expect(invoke).toHaveBeenCalledWith("material_download", {
        source_id: "file-1",
      }),
    );
    vi.mocked(invoke).mockClear();
    fireEvent.doubleClick(row);
    await waitFor(() =>
      expect(invoke).toHaveBeenCalledWith("material_download", {
        source_id: "file-1",
      }),
    );
  });

  it("非选中行的次要操作不进入 Tab 顺序", async () => {
    render(<MaterialsView />);
    const search = await screen.findByRole("textbox", { name: "搜索文件名" });
    fireEvent.change(search, { target: { value: ".pdf" } });
    const first = screen.getByRole("listitem", { name: "拖动资料：讲义.pdf" });
    const second = screen.getByRole("listitem", { name: "拖动资料：概览.pdf" });

    fireEvent.click(first);
    expect(
      screen.getByRole("combobox", { name: "归档“讲义.pdf”到分类" }).tabIndex,
    ).toBe(0);
    expect(
      screen.getByRole("combobox", { name: "归档“概览.pdf”到分类" }).tabIndex,
    ).toBe(-1);
    expect(second.tabIndex).toBe(-1);
  });

  it("筛选无结果可清除并通过 live region 宣告", async () => {
    render(<MaterialsView />);
    const search = await screen.findByRole("textbox", { name: "搜索文件名" });
    fireEvent.change(search, { target: { value: "不存在的资料" } });

    expect(screen.getByText("0 个结果").getAttribute("aria-live")).toBe(
      "polite",
    );
    expect(screen.getByText("没有匹配资料")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "清除筛选" }));
    expect((search as HTMLInputElement).value).toBe("");
    expect(screen.queryByText("没有匹配资料")).toBeNull();
  });

  it.each([320, 375, 599])(
    "%ipx 使用目录/内容主从切换并恢复焦点",
    async (width) => {
      Object.defineProperty(window, "innerWidth", {
        configurable: true,
        value: width,
      });
      render(<MaterialsView />);
      const course = await screen.findByRole("treeitem", {
        name: "自然语言处理",
      });
      fireEvent.click(course);

      const back = await screen.findByRole("button", { name: "返回目录" });
      expect(screen.queryByRole("tree", { name: "资料目录" })).toBeNull();
      await waitFor(() =>
        expect(document.activeElement).toBe(
          screen.getByRole("heading", { level: 3, name: "自然语言处理" }),
        ),
      );
      fireEvent.click(back);
      const restoredCourse = await screen.findByRole("treeitem", {
        name: "自然语言处理",
      });
      await waitFor(() => expect(document.activeElement).toBe(restoredCourse));
    },
  );
  it("云端资料显示云盘路径，双击预览且不显示 Reveal", async () => {
    const cloudTree = structuredClone(tree);
    const cloudFile = findMaterial(cloudTree.root, "file-3");
    expect(cloudFile).toBeTruthy();
    Object.assign(cloudFile as import("@/lib/types").MaterialNode, {
      cloud_path:
        "SJTU Learning Assistant/Canvas/2026 Fall/自然语言处理/课件/概览--hash.pdf",
      cloud_ready: true,
      cloud_status: "cloud",
      can_open: true,
      can_preview: true,
      download_status: "cloud_only",
      local_path: null,
    });
    vi.mocked(invoke).mockResolvedValue(cloudTree);
    vi.mocked(previewMaterial).mockResolvedValue({
      kind: "pdf",
      name: "概览.pdf",
      data_url: "data:application/pdf;base64,cGRm",
    });

    render(<MaterialsView />);
    fireEvent.click(
      await screen.findByRole("treeitem", { name: "自然语言处理" }),
    );
    const category = await screen.findByRole("treeitem", {
      name: "课件，可接收同课程资料拖放",
    });
    fireEvent.click(category);
    const row = await screen.findByRole("listitem", {
      name: "拖动资料：概览.pdf",
    });

    expect(screen.getByText("云端")).toBeTruthy();
    expect(screen.getByText(/交大云盘 · .*概览--hash\.pdf/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Reveal" })).toBeNull();
    fireEvent.doubleClick(row);
    await waitFor(() => expect(previewMaterial).toHaveBeenCalledWith("file-3"));
    expect(
      await screen.findByRole("dialog", { name: "概览.pdf" }),
    ).toBeTruthy();
    expect(screen.getByTitle("概览.pdf PDF 预览")).toBeTruthy();
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("仅云端 Office 资料显示打开并在双击时调用 material_open", async () => {
    const cloudTree = structuredClone(tree);
    const cloudFile = findMaterial(cloudTree.root, "file-3");
    expect(cloudFile).toBeTruthy();
    Object.assign(cloudFile as import("@/lib/types").MaterialNode, {
      name: "课件.docx",
      cloud_path:
        "SJTU Learning Assistant/Canvas/2026 Fall/自然语言处理/课件/课件--hash.docx",
      cloud_ready: true,
      cloud_status: "cloud",
      can_open: true,
      can_preview: false,
      download_status: "cloud_only",
      local_path: null,
    });
    vi.mocked(invoke).mockResolvedValue(cloudTree);

    render(<MaterialsView />);
    fireEvent.click(
      await screen.findByRole("treeitem", { name: "自然语言处理" }),
    );
    fireEvent.click(
      await screen.findByRole("treeitem", {
        name: "课件，可接收同课程资料拖放",
      }),
    );
    const row = await screen.findByRole("listitem", {
      name: "拖动资料：课件.docx",
    });

    expect(screen.getByRole("button", { name: "打开" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "预览" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Reveal" })).toBeNull();
    fireEvent.doubleClick(row);
    await waitFor(() =>
      expect(invoke).toHaveBeenCalledWith("material_open", {
        source_id: "file-3",
      }),
    );
  });
});

describe("MaterialsView ARIA tree", () => {
  it("使用唯一 roving tabIndex 并按父子语义遍历至少三层", async () => {
    render(<MaterialsView />);
    const root = await screen.findByRole("treeitem", { name: "全部资料" });
    const tabbable = () =>
      screen.getAllByRole("treeitem").filter((item) => item.tabIndex === 0);
    expect(tabbable()).toEqual([root]);

    root.focus();
    fireEvent.keyDown(root, { key: "ArrowDown" });
    const term = screen.getByRole("treeitem", { name: "2026 Fall" });
    expect(document.activeElement).toBe(term);
    expect(term.getAttribute("aria-selected")).toBe("false");

    fireEvent.keyDown(term, { key: "ArrowRight" });
    const course = screen.getByRole("treeitem", { name: "自然语言处理" });
    expect(document.activeElement).toBe(course);
    expect(course.getAttribute("aria-expanded")).toBe("false");
    fireEvent.keyDown(course, { key: "ArrowRight" });
    expect(course.getAttribute("aria-expanded")).toBe("true");
    expect(document.activeElement).toBe(course);
    fireEvent.keyDown(course, { key: "ArrowRight" });
    const category = screen.getAllByRole("treeitem", { name: /课程作业/ })[0];
    expect(document.activeElement).toBe(category);
    expect(category.getAttribute("aria-level")).toBe("4");
    expect(category.getAttribute("aria-selected")).toBe("false");

    fireEvent.keyDown(category, { key: "Enter" });
    expect(category.getAttribute("aria-selected")).toBe("true");
    expect(tabbable()).toEqual([category]);

    fireEvent.keyDown(category, { key: "ArrowLeft" });
    expect(document.activeElement).toBe(course);
    fireEvent.keyDown(course, { key: " " });
    expect(course.getAttribute("aria-selected")).toBe("true");
    expect(course.getAttribute("aria-expanded")).toBe("false");
  });

  it("支持 Up/Down/Home/End，折叠后移除后代", async () => {
    render(<MaterialsView />);
    const root = await screen.findByRole("treeitem", { name: "全部资料" });
    const course = screen.getByRole("treeitem", { name: "自然语言处理" });
    fireEvent.click(course);
    const category = screen.getAllByRole("treeitem", { name: /课程作业/ })[0];

    category.focus();
    fireEvent.keyDown(category, { key: "Home" });
    expect(document.activeElement).toBe(root);
    fireEvent.keyDown(root, { key: "End" });
    const last = screen.getAllByRole("treeitem").at(-1);
    expect(document.activeElement).toBe(last);
    fireEvent.keyDown(last as HTMLElement, { key: "ArrowUp" });
    expect(document.activeElement).not.toBe(last);

    course.focus();
    fireEvent.keyDown(course, { key: "ArrowLeft" });
    expect(course.getAttribute("aria-expanded")).toBe("false");
    const treeView = screen.getByRole("tree", { name: "资料目录" });
    expect(
      treeView.querySelector(
        '[data-material-target-id="category:course-1:assignments"]',
      ),
    ).toBeNull();
    expect(
      screen.getAllByRole("treeitem").filter((item) => item.tabIndex === 0),
    ).toEqual([course]);
  });
});
