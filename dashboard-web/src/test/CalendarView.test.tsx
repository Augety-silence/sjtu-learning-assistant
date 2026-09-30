// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import {
  type CalendarEventItem,
  CalendarView,
} from "@/components/CalendarView";
import { cleanup, fireEvent, render, screen } from "@/test/render";

afterEach(cleanup);

const events: CalendarEventItem[] = [
  {
    id: "1",
    title: "项目报告",
    courseName: "人机交互",
    startAt: "2026-10-02T18:00:00+08:00",
  },
  {
    id: "2",
    title: "月末测验",
    courseName: "高等数学",
    startAt: "2026-10-31T09:00:00+08:00",
  },
];

describe("CalendarView", () => {
  it("renders a month grid and seven-day reminders", () => {
    const open = vi.fn();
    render(
      <CalendarView
        events={events}
        month="2026-10-01T00:00:00+08:00"
        now="2026-09-29T00:00:00+08:00"
        onOpenEvent={open}
      />,
    );
    expect(
      screen.getByRole("grid", { name: "2026 年 10 月日历" }),
    ).toBeTruthy();
    expect(screen.getAllByRole("gridcell")).toHaveLength(42);
    fireEvent.click(screen.getByRole("button", { name: /项目报告/ }));
    expect(open).toHaveBeenCalledWith(events[0]);
  });

  it("supports arrow-key date focus and permission state", () => {
    const { rerender } = render(
      <CalendarView events={events} month="2026-10-01" now="2026-10-01" />,
    );
    const day = screen.getByRole("gridcell", { name: /2026-10-01/ });
    fireEvent.keyDown(day, { key: "ArrowRight" });
    expect(
      screen
        .getByRole("gridcell", { name: /2026-10-02/ })
        .getAttribute("aria-selected"),
    ).toBe("true");
    rerender(<CalendarView events={[]} permissionDenied />);
    expect(screen.getByText("没有日历访问权限")).toBeTruthy();
  });
});
