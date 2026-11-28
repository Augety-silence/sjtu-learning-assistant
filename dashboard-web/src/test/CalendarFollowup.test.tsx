// @vitest-environment jsdom
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import type { CalendarEventItem } from "@/components/CalendarView";
import { dateKey } from "@/v2/calendarData";
import { V2DatePicker } from "@/v2/DatePicker";
import { V2Dialog } from "@/v2/primitives";
import {
  initialScrollMinutes,
  isAllDay,
  onDay,
  ScheduleGrid,
} from "@/v2/ScheduleGrid";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});
it("23:59和次日00:00截止属于同一天的全天区，普通时间仍在网格", () => {
  const day = new Date(2026, 10, 28, 12);
  const events: CalendarEventItem[] = [
    {
      id: "1",
      eventType: "assignment",
      title: "午夜作业",
      courseName: "课程",
      startAt: new Date(2026, 10, 29, 0).toISOString(),
    },
    {
      id: "2",
      eventType: "assignment",
      title: "晚间作业",
      courseName: "课程",
      startAt: new Date(2026, 10, 28, 23, 59).toISOString(),
    },
    {
      id: "3",
      eventType: "assignment",
      title: "下午作业",
      courseName: "课程",
      startAt: new Date(2026, 10, 28, 15).toISOString(),
    },
  ];
  render(
    <ScheduleGrid
      mode="day"
      days={[day]}
      monthDays={[]}
      events={events}
      selected={dateKey(day)}
      onChoose={vi.fn()}
      onCreate={vi.fn()}
      onOpen={vi.fn()}
      onOpenDay={vi.fn()}
    />,
  );
  const band = screen.getByLabelText("全天日程");
  expect(within(band).getByText("24:00 截止")).toBeTruthy();
  expect(within(band).getByText("23:59 截止")).toBeTruthy();
  expect(screen.getByText("下午作业").closest(".v2-time-grid")).toBeTruthy();
  expect(onDay(events[0], new Date(2026, 10, 29, 12))).toBe(false);
  expect(isAllDay(events[2])).toBe(false);
});
it("默认定位当前时间前150分钟并按半小时取整，凌晨深夜不干预", () => {
  const at = (h: number, m: number) => new Date(2026, 10, 28, h, m);
  expect(initialScrollMinutes(at(9, 30))).toBe(420);
  expect(initialScrollMinutes(at(10, 0))).toBe(450);
  expect(initialScrollMinutes(at(10, 17))).toBe(450);
  expect(initialScrollMinutes(at(0, 0))).toBeNull();
  expect(initialScrollMinutes(at(23, 59))).toBeNull();
});
it("刷新时间指针不拉回用户手动滚动位置", () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date(2026, 10, 28, 10));
  const day = new Date();
  render(
    <ScheduleGrid
      mode="day"
      days={[day]}
      monthDays={[]}
      events={[]}
      selected={dateKey(day)}
      onChoose={vi.fn()}
      onCreate={vi.fn()}
      onOpen={vi.fn()}
      onOpenDay={vi.fn()}
    />,
  );
  const scroll = screen.getByLabelText("00:00至24:00时间网格，可上下滚动");
  expect(scroll.scrollTop).toBe(480);
  scroll.scrollTop = 800;
  fireEvent.scroll(scroll);
  vi.advanceTimersByTime(60000);
  expect(scroll.scrollTop).toBe(800);
});
it("自建月历支持日期选择和最小日期，Escape只关闭日期层", () => {
  function Form() {
    const [value, setValue] = useState("2026-11-28");
    return (
      <V2Dialog title="创建日程" onClose={vi.fn()}>
        <V2DatePicker
          label="结束日期"
          min="2026-11-28"
          value={value}
          onChange={setValue}
        />
      </V2Dialog>
    );
  }
  render(<Form />);
  expect(screen.getByLabelText("结束日期").getAttribute("type")).toBe("text");
  fireEvent.click(screen.getByRole("button", { name: "选择结束日期" }));
  const picker = screen.getByRole("dialog", { name: "结束日期选择器" });
  expect(
    within(picker)
      .getByRole("button", { name: "2026-11-27" })
      .hasAttribute("disabled"),
  ).toBe(true);
  fireEvent.click(within(picker).getByRole("button", { name: "2026-11-29" }));
  expect((screen.getByLabelText("结束日期") as HTMLInputElement).value).toBe(
    "2026-11-29",
  );
  fireEvent.click(screen.getByRole("button", { name: "选择结束日期" }));
  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByRole("dialog", { name: "结束日期选择器" })).toBeNull();
  expect(screen.getByRole("dialog", { name: "创建日程" })).toBeTruthy();
});
