import { describe, expect, it } from "vitest";
import type { CalendarEventItem } from "@/components/CalendarView";
import { uniqueCalendarEventsById } from "@/lib/calendarEvents";

describe("uniqueCalendarEventsById", () => {
  it("keeps the first cross-range detail by strict ID, including when event types differ", () => {
    const monthEvents: CalendarEventItem[] = [
      {
        id: "canvas-42",
        title: "月份内作业详情",
        courseName: "人机交互",
        startAt: "2026-10-19T21:59:00+08:00",
        eventType: "assignment",
      },
      {
        id: "canvas-43",
        title: "保留的课程事件",
        courseName: "文本分析",
        startAt: "2026-10-20T09:00:00+08:00",
        eventType: "course",
      },
    ];
    const upcomingEvents: CalendarEventItem[] = [
      {
        id: "canvas-42",
        title: "即将到期的更新详情",
        courseName: "人机交互",
        startAt: "2026-10-19T21:59:00+08:00",
        eventType: "assignment",
      },
      {
        id: "canvas-43",
        title: "同 ID 的另一事件类型",
        courseName: "文本分析",
        startAt: "2026-10-20T09:00:00+08:00",
        eventType: "assignment",
      },
      {
        id: "canvas-44",
        title: "仅在 upcoming 中",
        courseName: "机器学习",
        startAt: "2026-10-21T09:00:00+08:00",
        eventType: "assignment",
      },
    ];
    const combined = [...monthEvents, ...upcomingEvents];
    const previousImplementation = combined.filter(
      (event, index, events) =>
        events.findIndex((candidate) => candidate.id === event.id) === index,
    );
    const actual = uniqueCalendarEventsById(combined);

    expect(actual).toEqual(previousImplementation);
    expect(actual).toHaveLength(3);
    expect(actual[0]).toBe(monthEvents[0]);
    expect(actual[1]).toBe(monthEvents[1]);
    expect(actual[2]).toBe(upcomingEvents[2]);
  });
});
