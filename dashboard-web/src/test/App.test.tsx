// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "@/App";
import { getMessageDetail, getMessages, invoke } from "@/lib/api";
import type { MessageDetail, MessageItem, OverviewData } from "@/lib/types";
import { cleanup, fireEvent, render, screen, waitFor } from "@/test/render";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    getMessageDetail: vi.fn(),
    getMessages: vi.fn(),
    invoke: vi.fn(),
  };
});

const targetMessage: MessageItem = {
  source_id: "mail-target",
  kind: "email",
  title: "无链接邮件",
  source_label: "老师",
  occurred_at: "2026-09-22T08:00:00+08:00",
  is_unread: true,
  url: null,
};

let syncTriggered = false;

const overview: OverviewData = {
  courses: 1,
  upcoming_deadlines: 0,
  unread_emails: 1,
  deadlines: [],
  messages: [targetMessage],
};

const detail: MessageDetail = {
  title: targetMessage.title,
  source_label: targetMessage.source_label,
  occurred_at: targetMessage.occurred_at,
  body: "邮件正文",
  body_html: null,
  format: "text",
  pending_body_sync: false,
  attachments: [],
  resources: [],
  url: null,
  is_unread: true,
};

beforeEach(() => {
  window.location.hash = "#/overview";
  vi.clearAllMocks();
  syncTriggered = false;
  vi.mocked(invoke).mockImplementation(async (action) => {
    if (action === "overview") return overview as never;
    if (action === "sync_status") {
      return {
        status: syncTriggered ? "syncing" : "idle",
        last_success_at: null,
        last_run_status: null,
        last_run_at: null,
      } as never;
    }
    if (action === "sync_trigger") {
      syncTriggered = true;
      return { status: "accepted" } as never;
    }
    throw new Error(`Unexpected action: ${action}`);
  });
  vi.mocked(getMessages).mockResolvedValue({ items: [] });
  vi.mocked(getMessageDetail).mockResolvedValue(detail);
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
  window.location.hash = "";
});

describe("overview message detail", () => {
  it("在概览页原地打开同款详情弹窗，不切换消息视图", async () => {
    render(<App />);

    fireEvent.click(
      await screen.findByRole("button", { name: "打开消息详情：无链接邮件" }),
    );

    await waitFor(() => {
      expect(window.location.hash).toBe("#/overview");
      expect(
        screen
          .getByRole("button", { name: "概览", hidden: true })
          .getAttribute("aria-current"),
      ).toBe("page");
      expect(screen.queryByText("消息收件箱")).toBeNull();
      expect(getMessageDetail).toHaveBeenCalledWith("email", "mail-target");
    });
    expect((await screen.findByRole("dialog")).textContent).toContain(
      "邮件正文",
    );
  });

  it("概览 DTO 不依赖消息列表或 URL，关闭后仍停留在概览", async () => {
    render(<App />);
    const trigger = await screen.findByRole("button", {
      name: "打开消息详情：无链接邮件",
    });
    fireEvent.click(trigger);
    await screen.findByRole("dialog");
    fireEvent.click(screen.getByRole("button", { name: "关闭消息详情" }));

    expect(screen.queryByRole("dialog")).toBeNull();
    await waitFor(() => expect(document.activeElement).toBe(trigger));
    expect(window.location.hash).toBe("#/overview");
    expect(getMessages).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: /在 Canvas 打开/ })).toBeNull();
  });
});

describe("页面标题焦点", () => {
  it("hash 导航后聚焦新页面标题，但首次加载不抢焦点", async () => {
    render(<App />);
    const initialHeading = screen.getByRole("heading", {
      level: 1,
      name: "概览",
    });
    expect(document.activeElement).not.toBe(initialHeading);

    window.location.hash = "#/messages";
    window.dispatchEvent(new HashChangeEvent("hashchange"));
    const heading = await screen.findByRole("heading", {
      level: 1,
      name: "消息",
    });
    await waitFor(() => expect(document.activeElement).toBe(heading));
  });
});

describe("global operation toast", () => {
  it("同步操作显示在全局右上角 Toast，页面内静态通知不再出现", async () => {
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "立即同步" }));

    const viewport = document.querySelector(".toast-viewport");
    expect(viewport?.getAttribute("aria-label")).toBe("操作通知");
    expect(
      await screen.findByText("同步请求已接受，正在后台执行。"),
    ).toBeTruthy();
    expect(document.querySelector(".global-notice")).toBeNull();
    expect(document.querySelector(".content > .notice")).toBeNull();
  });
});
