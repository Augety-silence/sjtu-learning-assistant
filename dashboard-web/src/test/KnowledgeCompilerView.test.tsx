// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { KnowledgeCompilerView } from "@/components/KnowledgeCompilerView";
import {
  cancelKnowledgeCompiler,
  getKnowledgeCompilerStatus,
  getSettings,
  inspectKnowledgeSource,
  pickKnowledgeFolder,
  startKnowledgeCompiler,
} from "@/lib/api";
import type { KnowledgeCompilerTask, SettingsStatus } from "@/lib/types";
import { cleanup, fireEvent, render, screen, waitFor } from "@/test/render";

vi.mock("@/lib/api", () => ({
  cancelKnowledgeCompiler: vi.fn(),
  getKnowledgeCompilerStatus: vi.fn(),
  getSettings: vi.fn(),
  inspectKnowledgeSource: vi.fn(),
  pickKnowledgeFolder: vi.fn(),
  startKnowledgeCompiler: vi.fn(),
}));

const idleTask: KnowledgeCompilerTask = {
  status: "idle",
  task_id: null,
  source_name: null,
  target_name: null,
  mode: null,
  phase_index: 0,
  total_phases: 0,
  current_phase: null,
  progress: { done: 0, total: 0, current_file: null },
  counts: {
    markdown_files: 0,
    processed_files: 0,
    generated_notes: 0,
    warnings: 0,
  },
  warnings: [],
  events: [],
  error: null,
  started_at: null,
  updated_at: null,
  finished_at: null,
};

const settings = {
  ai_enabled: true,
  ai_key_saved: true,
} as SettingsStatus;

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  vi.mocked(getSettings).mockResolvedValue(settings);
  vi.mocked(getKnowledgeCompilerStatus).mockResolvedValue(idleTask);
  vi.mocked(cancelKnowledgeCompiler).mockResolvedValue({
    ...idleTask,
    status: "cancelled",
  });
});

afterEach(() => cleanup());

describe("KnowledgeCompilerView", () => {
  it("展示 12 轮编译路线和安全目录说明", async () => {
    render(<KnowledgeCompilerView />);
    expect(
      await screen.findByText("把课程文件编译成可生长的知识网络"),
    ).toBeTruthy();
    expect(screen.getByText("完整编译 · 12 轮")).toBeTruthy();
    expect(screen.getByText("反向校验")).toBeTruthy();
    expect(screen.getByText(/输出目录必须为空/)).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "开始编译" }).hasAttribute("disabled"),
    ).toBe(true);
  });

  it("选择目录、扫描并启动完整编译", async () => {
    vi.mocked(pickKnowledgeFolder)
      .mockResolvedValueOnce({
        cancelled: false,
        path: "/Users/student/Courses",
        name: "Courses",
      })
      .mockResolvedValueOnce({
        cancelled: false,
        path: "/Users/student/Vault",
        name: "Vault",
      });
    vi.mocked(inspectKnowledgeSource).mockResolvedValue({
      source_name: "Courses",
      markdown_files: 12,
      total_bytes: 8192,
      image_references: 3,
      courses: ["数据库", "机器学习"],
      truncated_courses: false,
    });
    const running: KnowledgeCompilerTask = {
      ...idleTask,
      status: "running",
      task_id: "task-1",
      mode: "full",
      phase_index: 1,
      total_phases: 12,
      current_phase: {
        id: "scan",
        label: "扫描与原始资料归档",
        model: "local",
      },
      progress: { done: 1, total: 12, current_file: "lecture-01.md" },
      counts: {
        markdown_files: 12,
        processed_files: 0,
        generated_notes: 1,
        warnings: 0,
      },
      events: [
        {
          time: "2026-10-02T10:00:00+08:00",
          level: "info",
          message: "开始扫描",
        },
      ],
    };
    vi.mocked(startKnowledgeCompiler).mockResolvedValue({
      status: "started",
      task: running,
    });

    render(<KnowledgeCompilerView />);
    const pickButtons = await screen.findAllByRole("button", {
      name: "选择目录",
    });
    fireEvent.click(pickButtons[0]);
    await waitFor(() =>
      expect(document.body.textContent).toContain("/Users/student/Courses"),
    );
    fireEvent.click(pickButtons[1]);
    await waitFor(() =>
      expect(document.body.textContent).toContain("/Users/student/Vault"),
    );

    fireEvent.click(screen.getByRole("button", { name: "扫描素材" }));
    expect(await screen.findByText("12")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "开始编译" }));

    await waitFor(() =>
      expect(startKnowledgeCompiler).toHaveBeenCalledWith(
        "/Users/student/Courses",
        "/Users/student/Vault",
        "full",
      ),
    );
    expect(await screen.findByText("编译运行中")).toBeTruthy();
    expect(
      screen.getByRole("progressbar", { name: "知识库编译进度" }),
    ).toBeTruthy();
    expect(screen.getByText("lecture-01.md")).toBeTruthy();
  });

  it("AI 未配置时给出明确引导", async () => {
    vi.mocked(getSettings).mockResolvedValue({
      ...settings,
      ai_enabled: false,
    });
    render(<KnowledgeCompilerView />);
    expect(await screen.findByText("需要先配置 AI 连接")).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "开始编译" }).hasAttribute("disabled"),
    ).toBe(true);
  });
});
