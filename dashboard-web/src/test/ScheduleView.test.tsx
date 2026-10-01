// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ScheduleView } from "@/components/ScheduleView";
import {
  commitTimetableImport,
  getTimetableSchedule,
  getTimetableStatus,
  pickTimetableFile,
  previewTimetableFile,
  previewTimetableSample,
} from "@/lib/api";
import { cleanup, fireEvent, render, screen, waitFor } from "@/test/render";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    commitTimetableImport: vi.fn(),
    getTimetableSchedule: vi.fn(),
    getTimetableStatus: vi.fn(),
    pickTimetableFile: vi.fn(),
    previewTimetableFile: vi.fn(),
    previewTimetableSample: vi.fn(),
    syncTimetable: vi.fn(),
  };
});

const awaitingStatus = {
  state: "awaiting_configuration" as const,
  provider: "上海交通大学",
  lastSyncedAt: null,
  message: "等待开放平台配置",
  supportsOAuth: false,
  hasLocalData: false,
};

const preview = {
  previewId: "preview-1",
  format: "ics",
  courses: [{ name: "文本分析与大模型" }, { name: "学术英语" }],
  sessions: 18,
  warnings: ["一节课程缺少教室"],
};

function renderView() {
  return render(
    <ScheduleView
      canvasEvents={[]}
      month={new Date(2026, 9, 1)}
      onMonthChange={vi.fn()}
    />,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  vi.mocked(getTimetableStatus).mockResolvedValue(awaitingStatus);
  vi.mocked(getTimetableSchedule).mockResolvedValue({ events: [] });
  vi.mocked(pickTimetableFile).mockResolvedValue({
    cancelled: false,
    path: "/tmp/timetable.ics",
  });
  vi.mocked(previewTimetableFile).mockResolvedValue(preview);
  vi.mocked(previewTimetableSample).mockResolvedValue({
    ...preview,
    previewId: "sample-1",
    warnings: [],
  });
  vi.mocked(commitTimetableImport).mockResolvedValue({
    status: "ok",
    importedCourses: 2,
    importedSessions: 18,
    updatedSessions: 0,
  });
});

afterEach(cleanup);

describe("ScheduleView", () => {
  it("shows the awaiting-configuration connection entry without credential fields", async () => {
    renderView();
    expect(await screen.findByText("连接上海交通大学")).toBeTruthy();
    expect(
      screen
        .getByRole("button", { name: "使用 jAccount 连接" })
        .hasAttribute("disabled"),
    ).toBe(true);
    expect(screen.getAllByText("等待开放平台配置").length).toBeGreaterThan(0);
    expect(screen.getByText(/不保存 jAccount 密码/)).toBeTruthy();
    expect(screen.queryByLabelText(/client_secret/i)).toBeNull();
    expect(screen.queryByLabelText(/密码/)).toBeNull();
  });

  it("previews and commits a selected local timetable", async () => {
    renderView();
    fireEvent.click(
      await screen.findByRole("button", { name: "导入本地课表" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "选择文件" }));
    expect(await screen.findByText("2 门")).toBeTruthy();
    expect(screen.getByText("18 节")).toBeTruthy();
    expect(screen.getByText("一节课程缺少教室")).toBeTruthy();
    expect(screen.getByText("文本分析与大模型")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "确认导入" }));
    await waitFor(() =>
      expect(commitTimetableImport).toHaveBeenCalledWith("preview-1"),
    );
  });

  it("loads the anonymous sample and recovers from preview errors", async () => {
    vi.mocked(previewTimetableFile).mockRejectedValueOnce(
      new Error("ICS 内容无效"),
    );
    renderView();
    fireEvent.click(
      await screen.findByRole("button", { name: "导入本地课表" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "选择文件" }));
    expect(await screen.findByText("ICS 内容无效")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /加载匿名示例课表/ }));
    expect(await screen.findByText("18 节")).toBeTruthy();
    expect(previewTimetableSample).toHaveBeenCalled();
  });

  it("keeps the preview available when commit fails", async () => {
    vi.mocked(commitTimetableImport).mockRejectedValueOnce(
      new Error("写入失败"),
    );
    renderView();
    fireEvent.click(
      await screen.findByRole("button", { name: "导入本地课表" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "选择文件" }));
    fireEvent.click(await screen.findByRole("button", { name: "确认导入" }));
    expect(await screen.findByText("写入失败")).toBeTruthy();
    expect(screen.getByRole("button", { name: "确认导入" })).toBeTruthy();
  });
});
