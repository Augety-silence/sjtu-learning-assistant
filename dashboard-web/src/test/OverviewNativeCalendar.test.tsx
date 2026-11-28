// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { getCalendar, getTimetableSchedule, invoke } from "@/lib/api";
import { cleanup, fireEvent, render, screen, waitFor } from "@/test/render";
import type { NativeEvent } from "@/v2/nativeCalendar";
import { V2Overview } from "@/v2/Overview";

vi.mock("@/lib/api", () => ({
  invoke: vi.fn(),
  getCalendar: vi.fn(),
  getTimetableSchedule: vi.fn(),
}));
const now = new Date();
const start = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 18);
const event: NativeEvent = {
  id: "apple-evening",
  calendar_id: "personal",
  calendar_title: "个人",
  title: "今晚的 Apple 日程",
  start_at: start.toISOString(),
  end_at: new Date(start.getTime() + 1800000).toISOString(),
  all_day: false,
  timezone: "Asia/Shanghai",
  location: "",
  notes: "",
  revision: "one",
  writable: true,
  recurring: false,
  alarms: [],
};
afterEach(() => {
  cleanup();
  localStorage.clear();
  sessionStorage.clear();
  vi.clearAllMocks();
});
function mock(events: NativeEvent[]) {
  vi.mocked(getCalendar).mockResolvedValue({
    month_events: [],
    upcoming_events: [],
    context_codes: [],
  } as never);
  vi.mocked(getTimetableSchedule).mockResolvedValue({ events: [] });
  vi.mocked(invoke).mockImplementation(async (action) => {
    if (action === "overview")
      return {
        courses: 0,
        upcoming_deadlines: 0,
        unread_emails: 0,
        messages: [],
        deadlines: [],
      };
    if (action === "native_calendar_status")
      return { supported: true, enabled: true, authorization: "full_access" };
    if (action === "native_calendar_calendars")
      return {
        items: [
          { id: "personal", title: "个人", writable: true, source: "iCloud" },
        ],
      };
    if (action === "native_calendar_events")
      return { items: events, truncated: false };
    throw new Error("未知操作");
  });
}
describe("概览与 Apple 日历使用同一数据源", () => {
  it("显示今晚安排与数量，点击保留相同原生日程标识用于跳转", async () => {
    mock([event]);
    const navigate = vi.fn();
    const { container } = render(<V2Overview navigate={navigate} />);
    fireEvent.click(await screen.findByText(event.title));
    expect(container.querySelector(".v2-agenda-item time")?.textContent).toBe(
      "18:00–18:30",
    );
    expect(
      container.querySelectorAll(".v2-summary button strong")[2].textContent,
    ).toBe("1");
    expect(sessionStorage.getItem("sjtu-v2-selected-event")).toBe(
      `native:${event.id}:${event.start_at}`,
    );
    expect(navigate).toHaveBeenCalledWith("calendar");
    expect(screen.queryByText("今天暂无日程")).toBeNull();
  });
  it("跨日与全天事件保留，昨天已结束的事件排除", async () => {
    const yesterday = new Date(
      now.getFullYear(),
      now.getMonth(),
      now.getDate() - 1,
    );
    const tomorrow = new Date(
      now.getFullYear(),
      now.getMonth(),
      now.getDate() + 1,
    );
    mock([
      {
        ...event,
        id: "span",
        title: "跨日安排",
        start_at: yesterday.toISOString(),
        end_at: tomorrow.toISOString(),
        all_day: true,
      },
      {
        ...event,
        id: "ended",
        title: "昨日结束",
        start_at: yesterday.toISOString(),
        end_at: new Date(
          now.getFullYear(),
          now.getMonth(),
          now.getDate(),
        ).toISOString(),
      },
    ]);
    render(<V2Overview navigate={vi.fn()} />);
    await screen.findByText("跨日安排");
    expect(screen.getByText("全天")).toBeTruthy();
    expect(screen.queryByText("昨日结束")).toBeNull();
  });
  it("系统日历修改后焦点刷新更新概览，不自动请求连接权限", async () => {
    const events = [event];
    mock(events);
    render(<V2Overview navigate={vi.fn()} />);
    await screen.findByText(event.title);
    events[0] = { ...event, title: "Apple 修改后的日程", revision: "two" };
    fireEvent(window, new Event("focus"));
    await screen.findByText("Apple 修改后的日程");
    expect(screen.queryByText(event.title)).toBeNull();
    expect(invoke).not.toHaveBeenCalledWith("native_calendar_connect");
  });
  it("原生读取失败展示错误与重试，不能宣称今天没有安排", async () => {
    mock([]);
    const original = vi.mocked(invoke).getMockImplementation()!;
    vi.mocked(invoke).mockImplementation(async (action, payload) => {
      if (action === "native_calendar_events") throw new Error("暂不可读取");
      return original(action, payload);
    });
    render(<V2Overview navigate={vi.fn()} />);
    await screen.findByText("Apple 日历未能读取");
    expect(screen.queryByText("今天暂无日程")).toBeNull();
    mock([event]);
    fireEvent.click(screen.getByRole("button", { name: "重新读取日程" }));
    await screen.findByText(event.title);
    await waitFor(() =>
      expect(screen.queryByText("Apple 日历未能读取")).toBeNull(),
    );
  });
});
