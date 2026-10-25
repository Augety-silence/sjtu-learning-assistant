// @vitest-environment jsdom

import { within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ScheduleView } from "@/components/ScheduleView";
import {
  getTimetableSchedule,
  getTimetableStatus,
  importTimetableIcs,
} from "@/lib/api";
import { mergeCalendarEvents } from "@/lib/calendarEvents";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@/test/render";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    getTimetableSchedule: vi.fn(),
    getTimetableStatus: vi.fn(),
    importTimetableIcs: vi.fn(),
  };
});

vi.mock("@/lib/calendarEvents", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/calendarEvents")>();
  return {
    ...actual,
    mergeCalendarEvents: vi.fn(actual.mergeCalendarEvents),
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

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("ScheduleView", () => {
  it("对缺少 ID 或时间的运行时事件仍按旧的严格身份规则去重", () => {
    const malformed = [
      { id: undefined, eventType: "course" },
      { id: undefined, eventType: "assignment" },
      { id: undefined, eventType: "course" },
      { id: "same", eventType: "course", startAt: "" },
      { id: "same", eventType: "course", startAt: "2026-10-01T09:00:00" },
    ] as unknown as Parameters<typeof mergeCalendarEvents>[0];
    const baseline = [...malformed].filter(
      (event, index, events) =>
        events.findIndex(
          (candidate) =>
            candidate.id === event.id &&
            candidate.eventType === event.eventType,
        ) === index,
    );

    expect(mergeCalendarEvents(malformed, [])).toEqual(baseline);
  });

  it("合并时保留来源顺序、同 ID 的不同类型，并在稳定输入重渲染时复用结果", async () => {
    const canvasEvents = [
      {
        id: "shared",
        title: "Canvas 作业",
        courseName: "文本分析",
        startAt: "2026-10-06T12:00:00+08:00",
        eventType: "assignment" as const,
      },
      {
        id: "shared",
        title: "Canvas 课程",
        courseName: "文本分析",
        startAt: "2026-10-06T08:00:00+08:00",
        eventType: "course" as const,
      },
    ];
    const localCourse = {
      id: "shared",
      title: "本地重复课程",
      courseName: "文本分析",
      startAt: "2026-10-06T08:00:00+08:00",
      eventType: "course" as const,
    };
    const month = new Date(2026, 9, 1);
    const onMonthChange = vi.fn();
    vi.mocked(getTimetableSchedule).mockResolvedValueOnce({
      events: [
        {
          ...localCourse,
          endAt: null,
          location: null,
          periodLabel: null,
          source: "local",
          canonicalCourseId: null,
        },
      ],
    });

    const view = render(
      <ScheduleView
        canvasEvents={canvasEvents}
        month={month}
        onMonthChange={onMonthChange}
      />,
    );
    expect(await screen.findByText("尚未完成 Canvas 数据同步")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "10月6日" }));
    expect(await screen.findByText("Canvas 课程")).toBeTruthy();
    const derivationsAfterLoad =
      vi.mocked(mergeCalendarEvents).mock.calls.length;
    view.rerender(
      <ScheduleView
        canvasEvents={canvasEvents}
        canvasSyncedAt="2026-10-20T12:00:00+08:00"
        month={month}
        onMonthChange={onMonthChange}
      />,
    );

    expect(vi.mocked(mergeCalendarEvents)).toHaveBeenCalledTimes(
      derivationsAfterLoad,
    );
    expect(getTimetableStatus).toHaveBeenCalledOnce();
    expect(getTimetableSchedule).toHaveBeenCalledOnce();
    view.rerender(
      <ScheduleView
        canvasEvents={canvasEvents}
        month={new Date(2026, 10, 1)}
        onMonthChange={onMonthChange}
      />,
    );
    await waitFor(() => {
      expect(getTimetableStatus).toHaveBeenCalledTimes(2);
      expect(getTimetableSchedule).toHaveBeenCalledTimes(2);
    });
    await waitFor(() => {
      expect(vi.mocked(mergeCalendarEvents).mock.calls.length).toBeGreaterThan(
        derivationsAfterLoad,
      );
    });
    expect(
      mergeCalendarEvents(canvasEvents, [localCourse, localCourse]).map(
        (event) => event.title,
      ),
    ).toEqual(["Canvas 作业", "Canvas 课程"]);
  });

  it("只保留轻量状态和 ICS 导入按钮，不展示课程配置卡", async () => {
    renderView();
    expect(await screen.findByText("等待开放平台配置")).toBeTruthy();
    expect(screen.getByRole("button", { name: "导入 ICS 日历" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /jAccount/ })).toBeNull();
    expect(screen.queryByText("连接上海交通大学")).toBeNull();
    expect(screen.queryByLabelText(/client_secret/i)).toBeNull();
    expect(screen.queryByLabelText(/密码/)).toBeNull();
  });

  it("在固定状态槽显示本机读取状态且不插入新行", async () => {
    render(
      <ScheduleView
        canvasEvents={[]}
        canvasLoading
        month={new Date(2026, 9, 1)}
        onMonthChange={vi.fn()}
      />,
    );
    const live = await screen.findByText("正在读取本机同步数据…");
    expect(live.className).toContain("calm-utility-live");
    expect(document.querySelectorAll(".calm-utility-row")).toHaveLength(1);
  });

  it("持续展示 Canvas 数据落地时间，避免把未加载误认为无作业", async () => {
    render(
      <ScheduleView
        canvasEvents={[]}
        canvasSyncedAt="2026-10-10T12:30:00+08:00"
        month={new Date(2026, 9, 1)}
        onMonthChange={vi.fn()}
      />,
    );
    expect(await screen.findByText(/Canvas 数据已落地/)).toBeTruthy();
    expect(screen.queryByText("正在同步 Canvas 日历…")).toBeNull();
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
    expect(startAt).toBe("2026-09-27T16:00:00.000Z");
    expect(endAt).toBe("2026-11-08T16:00:00.000Z");
    expect(new Date(startAt).getUTCDate()).toBe(27);
    expect(new Date(startAt).getUTCMonth()).toBe(8);
    expect(new Date(endAt).getUTCDate()).toBe(8);
    expect(new Date(endAt).getUTCMonth()).toBe(10);
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

  it("clears an ICS status notice so the same result can be announced again", async () => {
    vi.useFakeTimers();
    renderView();
    await act(async () => {
      await Promise.resolve();
    });
    const importButton = screen.getByRole("button", { name: "导入 ICS 日历" });
    fireEvent.click(importButton);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.getByText(/已导入 2 门课程/)).toBeTruthy();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(screen.queryByText(/已导入 2 门课程/)).toBeNull();
    fireEvent.click(importButton);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.getByText(/已导入 2 门课程/)).toBeTruthy();
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

  it("清理缺少月份范围的旧版离线缓存", async () => {
    localStorage.setItem(
      "sjtu-learning-timetable-cache-v1",
      JSON.stringify({ status: awaitingStatus, events: [] }),
    );
    vi.mocked(getTimetableSchedule).mockRejectedValueOnce(new Error("offline"));

    renderView();

    expect(await screen.findByText("offline")).toBeTruthy();
    expect(localStorage.getItem("sjtu-learning-timetable-cache-v1")).toBeNull();
  });

  it("does not display the previous month's local events when no matching cache exists", async () => {
    const visibleMonth = new Date(2026, 9, 1);
    const staleTitle = "上个月的课程";
    vi.mocked(getTimetableStatus).mockResolvedValue({
      ...awaitingStatus,
      state: "local",
      hasLocalData: true,
    });
    vi.mocked(getTimetableSchedule).mockResolvedValueOnce({
      events: [
        {
          id: "stale-course",
          title: staleTitle,
          courseName: staleTitle,
          startAt: "2026-10-25T10:00:00+08:00",
          endAt: null,
          location: null,
          periodLabel: null,
          eventType: "course",
          source: "local",
          canonicalCourseId: null,
        },
      ],
    });
    const view = render(
      <ScheduleView
        canvasEvents={[]}
        month={visibleMonth}
        onMonthChange={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /10月25日/ }));
    expect((await screen.findAllByText(staleTitle)).length).toBeGreaterThan(0);
    vi.mocked(getTimetableSchedule).mockRejectedValueOnce(
      new Error("当前月份离线"),
    );
    view.rerender(
      <ScheduleView
        canvasEvents={[]}
        month={
          new Date(visibleMonth.getFullYear(), visibleMonth.getMonth() + 1, 1)
        }
        onMonthChange={vi.fn()}
      />,
    );
    await screen.findByText("当前月份离线");
    expect(screen.queryByText(staleTitle)).toBeNull();
    expect(screen.queryByText("离线日历")).toBeNull();
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
