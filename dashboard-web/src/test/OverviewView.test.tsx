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
    expect(summary.classList.contains("summary-strip")).toBe(true);
    expect(summary.children).toHaveLength(3);
    expect(summary.querySelectorAll(".summary-icon")).toHaveLength(3);
    expect(summary.querySelector(".summary-secondary")?.textContent).toContain(
      "8",
    );
    expect(within(summary).getByText("1 项即将截止")).toBeTruthy();
    expect(within(summary).getByText("没有等待查看的邮件")).toBeTruthy();
    expect(within(summary).getByText("课程数据已同步")).toBeTruthy();

    const messageButtons = screen.getAllByRole("button", {
      name: /打开消息详情/,
    });
    expect(messageButtons[0].getAttribute("aria-label")).toBe(
      "打开消息详情：未读消息",
    );
    const deadlineRows = document.querySelectorAll(".list-row-button");
    expect(deadlineRows[0].textContent).toContain("即将截止");
    expect(document.querySelectorAll(".overview-panel")).toHaveLength(2);
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

  it("让统计卡片的空状态描述与动态数字保持一致", async () => {
    const empty: OverviewData = {
      courses: 0,
      upcoming_deadlines: 0,
      unread_emails: 0,
      deadlines: [],
      messages: [],
    };
    vi.mocked(invoke).mockResolvedValue(empty);

    render(<OverviewView navigate={vi.fn()} />);
    const summary = await screen.findByLabelText("学习概览");
    expect(within(summary).getByText("24 小时内无任务")).toBeTruthy();
    expect(within(summary).getByText("没有等待查看的邮件")).toBeTruthy();
    expect(within(summary).getByText("尚未同步课程")).toBeTruthy();
  });

  it("仅展示截止日期最近的五项未完成任务", async () => {
    const now = Date.now();
    const dayOffsets = [9, 1, 7, 3, 5, 11];
    const data: OverviewData = {
      courses: 1,
      upcoming_deadlines: 3,
      unread_emails: 0,
      deadlines: dayOffsets.map((days) => ({
        source_id: `deadline-${days}`,
        title: `第 ${days} 天截止`,
        course: "测试课程",
        due_at: new Date(now + days * 24 * 60 * 60 * 1000).toISOString(),
        submission_state: "unsubmitted",
        url: null,
      })),
      messages: [],
    };
    vi.mocked(invoke).mockResolvedValue(data);

    render(<OverviewView navigate={vi.fn()} />);
    expect(await screen.findByText("最近截止")).toBeTruthy();
    const rows = document.querySelectorAll(
      ".overview-deadlines .list-row-button",
    );
    expect(rows).toHaveLength(5);
    expect(rows[0].textContent).toContain("第 1 天截止");
    expect(rows[4].textContent).toContain("第 9 天截止");
    expect(document.body.textContent).not.toContain("第 11 天截止");
  });
});
