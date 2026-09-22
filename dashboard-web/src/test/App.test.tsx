// @vitest-environment jsdom

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "@/App";
import { getMessageDetail, getMessages, invoke } from "@/lib/api";
import type { MessageDetail, MessageItem, OverviewData } from "@/lib/types";

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
  vi.mocked(invoke).mockImplementation(async (action) => {
    if (action === "overview") return overview as never;
    if (action === "sync_status") {
      return {
        status: "idle",
        last_success_at: null,
        last_run_status: null,
        last_run_at: null,
      } as never;
    }
    throw new Error(`Unexpected action: ${action}`);
  });
  vi.mocked(getMessages).mockResolvedValue({ items: [] });
  vi.mocked(getMessageDetail).mockResolvedValue(detail);
});

afterEach(cleanup);

describe("overview message navigation", () => {
  it("跳转到消息页、高亮导航并自动打开对应详情", async () => {
    render(<App />);

    fireEvent.click(
      await screen.findByRole("button", { name: "打开消息详情：无链接邮件" }),
    );

    await waitFor(() => {
      expect(window.location.hash).toBe("#/messages");
      expect(
        screen
          .getByRole("button", { name: "消息" })
          .getAttribute("aria-current"),
      ).toBe("page");
      expect(getMessageDetail).toHaveBeenCalledWith("email", "mail-target");
    });
    expect((await screen.findByRole("dialog")).textContent).toContain(
      "邮件正文",
    );
  });

  it("消费待打开消息后只自动打开一次", async () => {
    render(<App />);
    fireEvent.click(
      await screen.findByRole("button", { name: "打开消息详情：无链接邮件" }),
    );
    await screen.findByRole("dialog");
    fireEvent.click(screen.getByRole("button", { name: "关闭消息详情" }));
    fireEvent.click(screen.getByRole("button", { name: "概览" }));
    await screen.findByRole("button", { name: "打开消息详情：无链接邮件" });
    fireEvent.click(screen.getByRole("button", { name: "消息" }));

    await screen.findByText("消息收件箱");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(getMessageDetail).toHaveBeenCalledTimes(1);
  });

  it("目标不在消息首屏且邮件无 URL 时仍使用 DTO 标识加载详情", async () => {
    vi.mocked(getMessages).mockResolvedValue({
      items: [
        {
          ...targetMessage,
          source_id: "other-mail",
          title: targetMessage.title,
          url: "https://example.test/other",
        },
      ],
    });
    render(<App />);

    fireEvent.click(
      await screen.findByRole("button", { name: "打开消息详情：无链接邮件" }),
    );

    expect(await screen.findByRole("dialog")).toBeTruthy();
    expect(getMessageDetail).toHaveBeenCalledWith("email", "mail-target");
    expect(screen.queryByRole("button", { name: /在 Canvas 打开/ })).toBeNull();
  });
});
