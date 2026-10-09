// @vitest-environment jsdom

import { within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ScheduleView } from "@/components/ScheduleView";
import {
  getTimetableSchedule,
  getTimetableStatus,
  importTimetableIcs,
} from "@/lib/api";
import { cleanup, fireEvent, render, screen, waitFor } from "@/test/render";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    getTimetableSchedule: vi.fn(),
    getTimetableStatus: vi.fn(),
    importTimetableIcs: vi.fn(),
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
  vi.mocked(importTimetableIcs).mockResolvedValue({
    status: "ok",
    importedCourses: 2,
    importedSessions: 18,
    updatedSessions: 0,
    format: "ics",
    warnings: [],
  });
});

afterEach(cleanup);

describe("ScheduleView", () => {
  it("只保留轻量状态和 ICS 导入按钮，不展示课程配置卡", async () => {
    renderView();
    expect(await screen.findByText("等待开放平台配置")).toBeTruthy();
    expect(screen.getByRole("button", { name: "导入 ICS 日历" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /jAccount/ })).toBeNull();
    expect(screen.queryByText("连接上海交通大学")).toBeNull();
    expect(screen.queryByLabelText(/client_secret/i)).toBeNull();
    expect(screen.queryByLabelText(/密码/)).toBeNull();
  });

  it("在固定状态槽显示 Canvas 同步状态且不插入新行", async () => {
    render(
      <ScheduleView
        canvasEvents={[]}
        canvasLoading
        month={new Date(2026, 9, 1)}
        onMonthChange={vi.fn()}
      />,
    );
    const live = await screen.findByText("正在同步 Canvas 日历…");
    expect(live.className).toContain("calm-utility-live");
    expect(document.querySelectorAll(".calm-utility-row")).toHaveLength(1);
  });

  it("loads the full visible calendar grid and merges Canvas with local events once", async () => {
    const canvasEvent = {
      id: "canvas-1",
      title: "Canvas 作业",
      courseName: "文本分析",
      startAt: "2026-10-06T12:00:00+08:00",
      eventType: "assignment" as const,
    };
    vi.mocked(getTimetableSchedule).mockResolvedValue({
      events: [
        {
          id: "timetable-1",
          title: "本地课程",
          courseName: "本地课程",
          startAt: "2026-09-28T08:00:00+08:00",
          endAt: "2026-09-28T09:40:00+08:00",
          location: null,
          periodLabel: null,
          eventType: "course",
          source: "local",
          canonicalCourseId: null,
        },
      ],
    });
    render(
      <ScheduleView
        canvasEvents={[canvasEvent, canvasEvent]}
        month={new Date(2026, 9, 1)}
        onMonthChange={vi.fn()}
      />,
    );
    await waitFor(() => expect(getTimetableSchedule).toHaveBeenCalled());
    const [startAt, endAt] = vi.mocked(getTimetableSchedule).mock.calls[0];
    expect(new Date(startAt).getDate()).toBe(28);
    expect(new Date(startAt).getMonth()).toBe(8);
    expect(new Date(endAt).getDate()).toBe(9);
    expect(new Date(endAt).getMonth()).toBe(10);
    fireEvent.click(screen.getByRole("button", { name: "9月28日" }));
    const agenda = await screen.findByLabelText("当日安排");
    expect(within(agenda).findByText("本地课程")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "10月6日" }));
    expect(within(agenda).findByText("Canvas 作业")).toBeTruthy();
  });

  it("imports an ICS calendar in one action", async () => {
    renderView();
    fireEvent.click(
      await screen.findByRole("button", { name: "导入 ICS 日历" }),
    );
    await waitFor(() => expect(importTimetableIcs).toHaveBeenCalledOnce());
    expect(await screen.findByText("已导入 2 门课程、18 节课。")).toBeTruthy();
  });

  it("shows ICS import errors without opening the legacy dialog", async () => {
    vi.mocked(importTimetableIcs).mockRejectedValueOnce(
      new Error("ICS 内容无效"),
    );
    renderView();
    fireEvent.click(
      await screen.findByRole("button", { name: "导入 ICS 日历" }),
    );
    expect(await screen.findByText("ICS 内容无效")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("does nothing when the ICS picker is cancelled", async () => {
    vi.mocked(importTimetableIcs).mockResolvedValueOnce({ cancelled: true });
    renderView();
    fireEvent.click(
      await screen.findByRole("button", { name: "导入 ICS 日历" }),
    );
    await waitFor(() => expect(importTimetableIcs).toHaveBeenCalledOnce());
    expect(screen.queryByText(/^已导入/)).toBeNull();
  });
});
