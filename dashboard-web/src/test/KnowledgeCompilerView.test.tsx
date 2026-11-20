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

vi.mock("@/components/CompiledKnowledgeBrowser", () => ({
  CompiledKnowledgeBrowser: () => (
    <section aria-label="编译结果与知识点">直接查看已有结果</section>
  ),
}));

vi.mock("@/lib/api", () => ({
  getCourseMarkdownStatus: vi.fn().mockResolvedValue({
    status: "disabled",
    enabled: false,
    output_root: "/archive/Knowledge Markdown",
    daily_limit: 100,
    calls_today: 0,
    total: 0,
    completed: 0,
    pending: 0,
    failed: 0,
    issues: [],
  }),
  updateSettings: vi.fn(),
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

async function renderCompiler() {
  render(<KnowledgeCompilerView />);
  const summary = await screen.findByText("编译设置与任务");
  if (!summary.closest("details")?.open) fireEvent.click(summary);
}

describe("KnowledgeCompilerView", () => {
  it("默认只展示结果浏览器，编译控件收纳且仍可主动展开", async () => {
    render(<KnowledgeCompilerView />);
    const summary = await screen.findByText("编译设置与任务");
    expect(summary.closest("details")?.open).toBe(false);
    expect(
      screen.getByRole("button", { name: "开始编译" }).closest("details"),
    ).toBe(summary.closest("details"));
    expect(screen.queryByRole("heading", { name: "课程知识库" })).toBeNull();
    fireEvent.click(summary);
    await waitFor(() => expect(summary.closest("details")?.open).toBe(true));
    expect(
      await screen.findByRole("button", { name: "开始编译" }),
    ).toBeTruthy();
  });
  it("已有任务运行时自动展开任务区，仍可安全停止", async () => {
    vi.mocked(getKnowledgeCompilerStatus).mockResolvedValue({
      ...idleTask,
      status: "running",
      task_id: "running-task",
    });
    render(<KnowledgeCompilerView />);
    const summary = await screen.findByText("编译设置与任务");
    await waitFor(() => expect(summary.closest("details")?.open).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: /停止/ }));
    await waitFor(() => expect(cancelKnowledgeCompiler).toHaveBeenCalled());
  });
  it("在编译页面直接展示已有结果浏览器", async () => {
    await renderCompiler();
    expect(await screen.findByLabelText("编译结果与知识点")).toBeTruthy();
  });
  it("默认选择逐页多模态，显示转换边界", async () => {
    await renderCompiler();
    const radio = await screen.findByRole("radio", {
      name: /逐页多模态转 Markdown/,
    });
    expect((radio as HTMLInputElement).checked).toBe(true);
    expect(screen.getByText(/每次最多 100 次 API 调用/)).toBeTruthy();
  });
  it("展示 12 轮编译路线和安全目录说明", async () => {
    await renderCompiler();
    expect(screen.queryByRole("heading", { name: "课程知识库" })).toBeNull();
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

    await renderCompiler();
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
    fireEvent.click(screen.getByRole("radio", { name: /完整编译 · 12 轮/ }));
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
    await renderCompiler();
    expect(await screen.findByText("需要先配置 AI 连接")).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "开始编译" }).hasAttribute("disabled"),
    ).toBe(true);
  });

  it("终态进度没有当前文件时显示任务已结束", async () => {
    vi.mocked(getKnowledgeCompilerStatus).mockResolvedValue({
      ...idleTask,
      status: "completed",
      progress: { done: 12, total: 12, current_file: null },
    });

    await renderCompiler();

    expect(await screen.findByText("编译已完成")).toBeTruthy();
    expect(screen.getByText("本次任务已结束")).toBeTruthy();
    expect(screen.queryByText("正在切换到下一轮…")).toBeNull();
  });
});
