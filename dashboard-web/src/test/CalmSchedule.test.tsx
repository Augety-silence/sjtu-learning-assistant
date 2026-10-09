// @vitest-environment jsdom

import { within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { CalendarEventItem } from "@/components/CalendarView";
import { CalmSchedule } from "@/components/calm/CalmSchedule";
import { cleanup, fireEvent, render, screen } from "@/test/render";

process.env.TZ = "Asia/Shanghai";

afterEach(() => {
  cleanup();
  localStorage.clear();
});

const NOW = new Date(2026, 9, 7, 15, 30);
const MONTH = new Date(2026, 9, 1);

const events: CalendarEventItem[] = [
  {
    id: "due-today",
    title: "Stakeholder engagement plan",
    courseName: "PMGT5889",
    startAt: "2026-10-07T21:00:00+08:00",
    eventType: "assignment",
  },
  {
    id: "class-tomorrow",
    title: "Capstone seminar",
    courseName: "PMGT5850",
    startAt: "2026-10-08T18:00:00+08:00",
    endAt: "2026-10-08T19:40:00+08:00",
    location: "Zoom Online Meeting",
    eventType: "course",
  },
  {
    id: "overdue",
    title: "Weekly reflection 8",
    courseName: "PMGT5850",
    startAt: "2026-10-05T23:59:00+08:00",
    eventType: "assignment",
  },
  {
    id: "next-week",
    title: "Team progress presentation",
    courseName: "PMGT5850",
    startAt: "2026-10-14T17:00:00+08:00",
    eventType: "assignment",
  },
];

function renderCalm(props?: Partial<Parameters<typeof CalmSchedule>[0]>) {
  const onAskAI = vi.fn();
  const onOpenEvent = vi.fn();
  render(
    <CalmSchedule
      events={events}
      month={MONTH}
      now={NOW}
      onAskAI={onAskAI}
      onOpenEvent={onOpenEvent}
      {...props}
    />,
  );
  return { onAskAI, onOpenEvent };
}

describe("CalmSchedule", () => {
  it("renders a compact today summary without the oversized clock hero", () => {
    renderCalm();
    expect(screen.getByText(/下午 15:30/)).toBeTruthy();
    expect(screen.getByText("10月7日 · 周三")).toBeTruthy();
    expect(screen.getByLabelText("日程摘要").textContent).toContain(
      "7 天内 1 项待交",
    );
    expect(document.querySelector(".calm-hero-clock")).toBeNull();
  });

  it("renders 42 day cells and opens the agenda for the selected day", () => {
    renderCalm();
    expect(
      screen
        .getAllByRole("button")
        .filter((button) =>
          /^\d{1,2}月\d{1,2}日/.test(button.getAttribute("aria-label") ?? ""),
        ),
    ).toHaveLength(42);
    expect(
      screen.getAllByText("Stakeholder engagement plan").length,
    ).toBeGreaterThan(0);
    expect(screen.getByText("今天 21:00")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "10月5日" }));
    expect(screen.getAllByText("Weekly reflection 8").length).toBeGreaterThan(
      0,
    );
    const agenda = screen.getByLabelText("当日安排");
    expect(within(agenda).getByText("逾期 2 天")).toBeTruthy();
  });

  it("shows course sessions with time and location", () => {
    renderCalm();
    fireEvent.click(screen.getByRole("button", { name: "10月8日" }));
    expect(screen.getByText("Capstone seminar")).toBeTruthy();
    expect(screen.getByText(/Zoom Online Meeting/)).toBeTruthy();
    expect(screen.getByText(/18:00–19:40/)).toBeTruthy();
  });

  it("sends a planning prompt with the next seven days to AI", () => {
    const { onAskAI } = renderCalm();
    fireEvent.click(screen.getByRole("button", { name: /AI 本周规划/ }));
    expect(onAskAI).toHaveBeenCalledTimes(1);
    const prompt = String(onAskAI.mock.calls[0][0]);
    expect(prompt).toContain("Stakeholder engagement plan");
    expect(prompt).toContain("Capstone seminar");
    expect(prompt).not.toContain("Weekly reflection 8");
    expect(prompt).not.toContain("Team progress presentation");
    expect(prompt).toContain("时间冲突");
  });

  it("shows nearest deadlines, including a task later than seven days", () => {
    renderCalm();
    expect(screen.getByRole("heading", { name: "最近截止" })).toBeTruthy();
    expect(screen.getByText("7 天后截止")).toBeTruthy();
  });

  it("opens assignments through the open-event callback", () => {
    const { onOpenEvent } = renderCalm();
    fireEvent.click(
      screen.getByRole("button", {
        name: `在 App 内完成作业：${events[0].title}`,
      }),
    );
    expect(onOpenEvent).toHaveBeenCalledWith(events[0]);
  });

  it("marks submitted and graded assignments as done", () => {
    renderCalm({
      events: [
        { ...events[0], status: "submitted" },
        {
          ...events[0],
          id: "graded-1",
          status: "graded",
          startAt: "2026-10-08T21:00:00+08:00",
        },
      ],
    });
    expect(screen.getByText("已提交")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "10月8日" }));
    expect(screen.getByText("已出分")).toBeTruthy();
  });
});
