// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MessagesView } from "@/components/MessagesView";
import {
  getMessageDetail,
  getMessages,
  markMessagesRead,
  openExternal,
} from "@/lib/api";
import type { MessageItem } from "@/lib/types";
import { cleanup, fireEvent, render, screen, waitFor } from "@/test/render";

vi.mock("@/lib/api", () => ({
  getMessageDetail: vi.fn(),
  getMessages: vi.fn(),
  markMessagesRead: vi.fn(),
  openExternal: vi.fn(),
}));

const messages: MessageItem[] = [
  {
    source_id: "mail-1",
    kind: "email",
    title: "邮件一",
    source_label: "老师",
    occurred_at: "2026-09-22T08:00:00+08:00",
    is_unread: true,
    url: null,
  },
  {
    source_id: "assignment-1",
    kind: "assignment",
    title: "作业一",
    source_label: "程序设计",
    occurred_at: "2026-09-21T08:00:00+08:00",
    is_unread: true,
    url: "https://canvas.example/assignments/1",
  },
];

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getMessages).mockResolvedValue({ items: messages });
  vi.mocked(getMessageDetail).mockImplementation(async (kind) => ({
    title: kind === "email" ? "邮件一" : "作业一",
    source_label: kind === "email" ? "老师" : "程序设计",
    occurred_at: "2026-09-22T08:00:00+08:00",
    body: "纯文本 <script>alert('xss')</script>",
    body_html: null,
    format: "text" as const,
    pending_body_sync: false,
    attachments: [],
    resources: [],
    url: kind === "assignment" ? "https://canvas.example/assignments/1" : null,
    is_unread: true,
  }));
  vi.mocked(markMessagesRead).mockResolvedValue({ updated: 1 });
  vi.mocked(openExternal).mockResolvedValue({ status: "opened" });
});

afterEach(cleanup);

describe("MessagesView", () => {
  it("显示纯文本 fallback，仅在有 URL 时提供 Canvas 操作", async () => {
    render(<MessagesView />);

    expect(await screen.findByText("作业一")).toBeTruthy();
    expect(screen.getByRole("tab", { name: "作业" })).toBeTruthy();
    fireEvent.click(
      screen.getByRole("button", { name: "打开消息详情：作业一" }),
    );

    expect(await screen.findByRole("dialog")).toBeTruthy();
    expect(screen.getByRole("document").textContent).toContain(
      "<script>alert('xss')</script>",
    );
    expect(document.querySelector("script")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /在 Canvas 打开/ }));
    await waitFor(() =>
      expect(openExternal).toHaveBeenCalledWith(
        "https://canvas.example/assignments/1",
      ),
    );

    fireEvent.click(screen.getByRole("button", { name: "关闭消息详情" }));
    fireEvent.click(
      screen.getByRole("button", { name: "打开消息详情：邮件一" }),
    );
    await screen.findByRole("dialog");
    expect(screen.queryByRole("button", { name: /在 Canvas 打开/ })).toBeNull();
  });

  it("支持单条与当前筛选全部标记已读并反馈状态", async () => {
    render(<MessagesView />);
    await screen.findByText("邮件一");

    fireEvent.click(
      screen.getByRole("button", { name: "将“邮件一”标记为已读" }),
    );
    await waitFor(() =>
      expect(markMessagesRead).toHaveBeenCalledWith({
        kind: "email",
        ids: ["mail-1"],
      }),
    );
    expect(await screen.findByText(/已将“邮件一”标记为已读/)).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: "将“邮件一”标记为已读" }),
    ).toBeNull();

    fireEvent.click(
      screen.getByRole("button", {
        name: "将当前筛选中的消息全部标记为已读",
      }),
    );
    await waitFor(() =>
      expect(markMessagesRead).toHaveBeenCalledWith({ kind: "all", all: true }),
    );
    expect(await screen.findByText(/当前筛选已全部标为已读/)).toBeTruthy();
  });

  it("支持 j/k、Enter、r、Shift+R、Escape，且编辑元素聚焦时不触发", async () => {
    render(
      <>
        <input aria-label="搜索输入" />
        <MessagesView />
      </>,
    );
    await screen.findByText("邮件一");

    fireEvent.keyDown(document, { key: "j" });
    expect(
      screen
        .getByRole("button", { name: "打开消息详情：作业一" })
        .getAttribute("aria-current"),
    ).toBe("true");
    fireEvent.keyDown(document, { key: "Enter" });
    await waitFor(() =>
      expect(getMessageDetail).toHaveBeenCalledWith(
        "assignment",
        "assignment-1",
      ),
    );
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();

    fireEvent.keyDown(document, { key: "k" });
    fireEvent.keyDown(document, { key: "r" });
    await waitFor(() =>
      expect(markMessagesRead).toHaveBeenCalledWith({
        kind: "email",
        ids: ["mail-1"],
      }),
    );
    fireEvent.keyDown(document, { key: "R", shiftKey: true });
    await waitFor(() =>
      expect(markMessagesRead).toHaveBeenCalledWith({ kind: "all", all: true }),
    );

    vi.mocked(markMessagesRead).mockClear();
    vi.mocked(getMessageDetail).mockClear();
    const input = screen.getByRole("textbox", { name: "搜索输入" });
    input.focus();
    fireEvent.keyDown(input, { key: "r" });
    fireEvent.keyDown(input, { key: "R", shiftKey: true });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(markMessagesRead).not.toHaveBeenCalled();
    expect(getMessageDetail).not.toHaveBeenCalled();
  });
});
