// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { OverviewView } from "@/components/OverviewView";
import { invoke } from "@/lib/api";
import type { OverviewData } from "@/lib/types";
import { cleanup, render, screen, within } from "@/test/render";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, invoke: vi.fn() };
});

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("OverviewView", () => {
  it("将 24 小时截止和未读置前，并把课程数降为辅助指标", async () => {
    const now = Date.now();
    const data: OverviewData = {
      courses: 8,
      upcoming_deadlines: 3,
      unread_emails: 0,
      deadlines: [
        {
          source_id: "later",
          title: "两天后",
          course: "课程 B",
          due_at: new Date(now + 48 * 60 * 60 * 1000).toISOString(),
          submission_state: "unsubmitted",
          url: null,
        },
        {
          source_id: "soon",
          title: "即将截止",
          course: "课程 A",
          due_at: new Date(now + 2 * 60 * 60 * 1000).toISOString(),
          submission_state: "unsubmitted",
          url: null,
        },
      ],
      messages: [
        {
          source_id: "read",
          kind: "email",
          title: "已读消息",
          source_label: "老师",
          occurred_at: null,
          is_unread: false,
          url: null,
        },
        {
          source_id: "unread",
          kind: "announcement",
          title: "未读消息",
          source_label: "课程 A",
          occurred_at: null,
          is_unread: true,
          url: null,
        },
      ],
    };
    vi.mocked(invoke).mockResolvedValue(data);

    render(<OverviewView navigate={vi.fn()} />);
    const summary = await screen.findByLabelText("学习概览");
    expect(summary.tagName).toBe("DL");
    expect(summary.textContent?.indexOf("24 小时内截止")).toBeLessThan(
      summary.textContent?.indexOf("未读邮件") ?? Infinity,
    );
    expect(summary.textContent?.indexOf("未读邮件")).toBeLessThan(
      summary.textContent?.indexOf("已同步课程") ?? Infinity,
    );
    expect(within(summary).getByText("1")).toBeTruthy();
    expect(within(summary).getByText("0")).toBeTruthy();
    expect(summary.querySelector(".kpi-secondary")?.textContent).toContain("8");

    const messageButtons = screen.getAllByRole("button", {
      name: /打开消息详情/,
    });
    expect(messageButtons[0].getAttribute("aria-label")).toBe(
      "打开消息详情：未读消息",
    );
    const deadlineRows = document.querySelectorAll(".list-row-button");
    expect(deadlineRows[0].textContent).toContain("即将截止");
  });

  it("加载失败保留真实重试路径", async () => {
    const empty: OverviewData = {
      courses: 0,
      upcoming_deadlines: 0,
      unread_emails: 0,
      deadlines: [],
      messages: [],
    };
    vi.mocked(invoke)
      .mockRejectedValueOnce(new Error("汇总失败"))
      .mockResolvedValueOnce(empty);

    render(<OverviewView navigate={vi.fn()} />);
    const retry = await screen.findByRole("button", { name: "重试" });
    retry.click();
    expect(await screen.findByLabelText("学习概览")).toBeTruthy();
    expect(invoke).toHaveBeenCalledTimes(2);
  });
});
