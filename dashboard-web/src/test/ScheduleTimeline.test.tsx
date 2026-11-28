// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { dateKey } from "@/v2/calendarData";
import { V2Schedule } from "@/v2/Schedule";

vi.mock("@/lib/api", () => ({
  invoke: vi.fn(async () => ({
    supported: false,
    enabled: false,
    authorization: "unsupported",
  })),
}));
let day: Date;
beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(2026, 10, 28, 12));
  day = new Date();
});
afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.useRealTimers();
});
function view(events: React.ComponentProps<typeof V2Schedule>["events"] = []) {
  return render(
    <V2Schedule
      events={events}
      month={day}
      onMonthChange={vi.fn()}
      onImport={vi.fn()}
      importing={false}
      status=""
      onRetry={vi.fn()}
    />,
  );
}
it("点击时间格使用相应时间与半小时长度，日周月可切换", () => {
  view();
  fireEvent.click(
    screen.getByRole("button", { name: `${dateKey(day)} 14:30 创建日程` }),
  );
  expect((screen.getByLabelText("开始") as HTMLInputElement).value).toBe(
    "14:30",
  );
  expect((screen.getByLabelText("结束") as HTMLInputElement).value).toBe(
    "15:00",
  );
  fireEvent.click(screen.getByRole("button", { name: "关闭" }));
  fireEvent.click(screen.getByRole("tab", { name: /^日$/ }));
  expect(document.querySelectorAll(".v2-time-day")).toHaveLength(1);
  fireEvent.click(screen.getByRole("tab", { name: /^月$/ }));
  expect(document.querySelectorAll(".v2-full-month-day")).toHaveLength(42);
  expect(screen.queryByText("今日日程")).toBeNull();
});
it("全天事项位于独立顶部，过去日程标灰，冲突日程并排", () => {
  const start = new Date(
    day.getFullYear(),
    day.getMonth(),
    day.getDate(),
    0,
  ).toISOString();
  const tomorrow = new Date(
    day.getFullYear(),
    day.getMonth(),
    day.getDate() + 1,
    0,
  ).toISOString();
  view([
    {
      id: "all",
      title: "全天复习",
      startAt: start,
      endAt: tomorrow,
      allDay: true,
      courseName: "个人",
    },
    {
      id: "past",
      title: "已结束",
      startAt: new Date(Date.now() - 7200000).toISOString(),
      endAt: new Date(Date.now() - 3600000).toISOString(),
      courseName: "个人",
    },
    ...[1, 2].map((id) => ({
      id: String(id),
      title: `冲突${id}`,
      startAt: new Date(
        day.getFullYear(),
        day.getMonth(),
        day.getDate(),
        14,
      ).toISOString(),
      endAt: new Date(
        day.getFullYear(),
        day.getMonth(),
        day.getDate(),
        15,
      ).toISOString(),
      courseName: "课程",
    })),
  ]);
  expect(screen.getByText("全天复习").closest(".v2-all-day-band")).toBeTruthy();
  expect(screen.getByText("已结束").closest(".is-past")).toBeTruthy();
  expect(
    (screen.getByText("冲突1").closest("button") as HTMLElement).style.width,
  ).toContain("50%");
  fireEvent.click(screen.getByRole("button", { name: /2项安排，查看日视图/ }));
  expect(document.querySelector(".v2-calendar-day")).toBeTruthy();
  expect(screen.getByText("冲突1")).toBeTruthy();
  expect(screen.getByText("冲突2")).toBeTruthy();
});
