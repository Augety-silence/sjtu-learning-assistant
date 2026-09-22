// @vitest-environment jsdom

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MaterialsView } from "@/components/MaterialsView";
import { invoke, moveMaterial, restoreMaterialAuto } from "@/lib/api";
import type { MaterialTree } from "@/lib/types";

vi.mock("@/lib/api", () => ({
  invoke: vi.fn(),
  moveMaterial: vi.fn(),
  restoreMaterialAuto: vi.fn(),
}));

const categories = [
  { id: "assignments", label: "课程作业" },
  { id: "courseware", label: "课件" },
  { id: "supplementary", label: "补充资料" },
  { id: "other", label: "其他" },
];

const tree: MaterialTree = {
  categories,
  download_statuses: ["all", "downloaded", "pending", "failed"],
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
                id: "category:course-1:supplementary",
                kind: "category",
                name: "补充资料",
                course_id: "course-1",
                category: "supplementary",
                children: [],
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

async function renderFile() {
  render(<MaterialsView />);
  await screen.findByText("自然语言处理");
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
  vi.mocked(invoke).mockResolvedValue(tree);
  vi.mocked(moveMaterial).mockResolvedValue({
    source_id: "file-1",
    status: "saved",
    local_path: null,
  });
  vi.mocked(restoreMaterialAuto).mockResolvedValue({
    source_id: "file-1",
    status: "saved",
    local_path: null,
  });
});

afterEach(cleanup);

describe("MaterialsView manual filing", () => {
  it("supports same-course drag/drop and rejects cross-course drop with feedback", async () => {
    const row = await renderFile();
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
    const categoryTarget = await screen.findByRole("button", {
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
      { target: { value: "supplementary" } },
    );
    await waitFor(() =>
      expect(moveMaterial).toHaveBeenCalledWith(
        "file-1",
        "category:course-1:supplementary",
      ),
    );
  });
});
