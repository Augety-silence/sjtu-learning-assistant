// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LocalProjectsView } from "@/components/LocalProjectsView";
import {
  addLocalProject,
  listLocalProjects,
  pickKnowledgeFolder,
  refreshLocalProject,
  removeLocalProject,
} from "@/lib/api";
import type { LocalProject } from "@/lib/types";
import { cleanup, fireEvent, render, screen, waitFor } from "@/test/render";

vi.mock("@/lib/api", () => ({
  addLocalProject: vi.fn(),
  listLocalProjects: vi.fn(),
  pickKnowledgeFolder: vi.fn(),
  refreshLocalProject: vi.fn(),
  removeLocalProject: vi.fn(),
}));

const project: LocalProject = {
  id: "0123456789abcdef01234567",
  name: "数据库系统",
  source_root: "/Users/student/Courses/Database",
  target_root: "/Users/student/Vaults/Database",
  markdown_files: 12,
  total_bytes: 8192,
  image_references: 3,
  courses: ["数据库系统"],
  truncated_courses: false,
  available: true,
  issue: null,
  compile_status: "completed",
  phase_index: 12,
  total_phases: 12,
  last_compiled_at: "2026-10-02T10:00:00+08:00",
  compile_error: null,
  created_at: "2026-10-02T09:00:00+08:00",
  updated_at: "2026-10-02T10:00:00+08:00",
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(listLocalProjects).mockResolvedValue({ items: [] });
  vi.mocked(addLocalProject).mockResolvedValue(project);
  vi.mocked(refreshLocalProject).mockResolvedValue(project);
  vi.mocked(removeLocalProject).mockResolvedValue({
    project_id: project.id,
    removed: true,
  });
});

afterEach(() => cleanup());

describe("LocalProjectsView", () => {
  it("从空状态登记素材与 Vault，并展示扫描结果", async () => {
    vi.mocked(pickKnowledgeFolder)
      .mockResolvedValueOnce({
        cancelled: false,
        path: project.source_root,
        name: "Database",
      })
      .mockResolvedValueOnce({
        cancelled: false,
        path: project.target_root,
        name: "Database Vault",
      });

    render(<LocalProjectsView onCompile={vi.fn()} />);
    fireEvent.click(
      await screen.findByRole("button", { name: "登记第一个项目" }),
    );

    const pickButtons = screen.getAllByRole("button", { name: "选择目录" });
    fireEvent.click(pickButtons[0]);
    await waitFor(() =>
      expect(document.body.textContent).toContain(project.source_root),
    );
    fireEvent.click(pickButtons[1]);
    await waitFor(() =>
      expect(document.body.textContent).toContain(project.target_root),
    );
    fireEvent.click(screen.getByRole("button", { name: "保存并扫描" }));

    await waitFor(() =>
      expect(addLocalProject).toHaveBeenCalledWith(
        project.source_root,
        project.target_root,
      ),
    );
    expect(
      await screen.findByRole("heading", {
        level: 3,
        name: "数据库系统",
      }),
    ).toBeTruthy();
    expect(screen.getByText("编译完成")).toBeTruthy();
    expect(screen.getAllByText("12")).toHaveLength(2);
  });

  it("从项目卡进入现有知识库编译流程", async () => {
    vi.mocked(listLocalProjects).mockResolvedValue({ items: [project] });
    const onCompile = vi.fn();

    render(<LocalProjectsView onCompile={onCompile} />);
    fireEvent.click(await screen.findByRole("button", { name: "进入编译" }));

    expect(onCompile).toHaveBeenCalledWith(project);
  });
});
