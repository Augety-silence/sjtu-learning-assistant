// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DeadlinesView, groupDeadlines } from "@/components/DeadlinesView";
import { invoke, openExternal } from "@/lib/api";
import type { Deadline } from "@/lib/types";
import { cleanup, fireEvent, render, screen, waitFor } from "@/test/render";

vi.mock("@/lib/api", () => ({
  invoke: vi.fn(),
  openExternal: vi.fn(),
}));

const deadline = (
  sourceId: string,
  title: string,
  dueAt: string | null,
): Deadline => ({
  source_id: sourceId,
  title,
  course: "程序设计",
  due_at: dueAt,
  submission_state: "unsubmitted",
  url: `https://canvas.example/${sourceId}`,
});

const groupedItems = [
  deadline("later", "更晚事项", "2026-09-28T09:00:00+08:00"),
  deadline("today-2", "今天同刻第二项", "2026-09-23T12:00:00+08:00"),
  deadline("undated", "无日期事项", null),
  deadline("week", "本周事项", "2026-09-25T09:00:00+08:00"),
  deadline("today-1", "今天同刻第一项", "2026-09-23T12:00:00+08:00"),
];

beforeEach(() => {
  vi.clearAllMocks();
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: undefined,
  });
  Object.defineProperty(window, "innerWidth", {
    configurable: true,
    value: 1024,
  });
  vi.mocked(invoke).mockResolvedValue({ items: groupedItems });
  vi.mocked(openExternal).mockResolvedValue({ status: "opened" });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  Object.defineProperty(window, "innerWidth", {
    configurable: true,
    value: 1024,
  });
});

describe("groupDeadlines", () => {
  it("按上海日期稳定分为今天、本周、更晚和无日期", () => {
    const groups = groupDeadlines(
      groupedItems,
      new Date("2026-09-23T08:00:00+08:00"),
    );

    expect(groups.map(({ id }) => id)).toEqual([
      "today",
      "week",
      "later",
      "undated",
    ]);
    expect(groups[0].items.map(({ source_id }) => source_id)).toEqual([
      "today-2",
      "today-1",
    ]);
    expect(groups[3].items[0].source_id).toBe("undated");
  });

  it("将非法日期安全归入无日期", () => {
    const groups = groupDeadlines(
      [deadline("invalid", "异常日期", "not-a-date")],
      new Date("2026-09-23T08:00:00+08:00"),
    );
    expect(groups).toHaveLength(1);
    expect(groups[0].id).toBe("undated");
  });
});

describe("DeadlinesView responsive semantics", () => {
  it("600px 使用语义表格和分组 tbody", async () => {
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      value: 600,
    });
    render(<DeadlinesView />);

    const table = await screen.findByRole("table", {
      name: "按日期分组的截止事项",
    });
    expect(table).toBeTruthy();
    expect(document.querySelectorAll("tbody")).toHaveLength(4);
    fireEvent.click(screen.getByRole("button", { name: "今天同刻第一项" }));
    await waitFor(() =>
      expect(openExternal).toHaveBeenCalledWith(
        "https://canvas.example/today-1",
      ),
    );
  });

  it.each([320, 375, 599])("%ipx 使用无横向表格的移动列表", async (width) => {
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      value: width,
    });
    render(<DeadlinesView />);

    expect(await screen.findByLabelText("截止事项列表")).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.getAllByText("程序设计").length).toBeGreaterThan(0);
    expect(screen.getByText("无日期事项")).toBeTruthy();
    expect(screen.getAllByText("时间未知").length).toBeGreaterThan(0);
  });

  it("空范围可重新检查，错误可真实重试", async () => {
    vi.mocked(invoke)
      .mockResolvedValueOnce({ items: [] })
      .mockRejectedValueOnce(new Error("网络异常"))
      .mockResolvedValueOnce({ items: groupedItems });
    render(<DeadlinesView />);

    fireEvent.click(await screen.findByRole("button", { name: "重新检查" }));
    expect((await screen.findByRole("alert")).textContent).toContain(
      "网络异常",
    );
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByText("今天同刻第一项")).toBeTruthy();
    expect(invoke).toHaveBeenCalledTimes(3);
  });
});
