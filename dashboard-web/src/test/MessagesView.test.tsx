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

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

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

  it.each(["按钮", "Escape", "遮罩"])(
    "%s 关闭详情后恢复实际触发按钮并恢复背景属性",
    async (method) => {
      render(<MessagesView />);
      const trigger = await screen.findByRole("button", {
        name: "打开消息详情：邮件一",
      });
      const background = trigger.closest<HTMLElement>(".list-surface");
      background?.setAttribute("aria-hidden", "false");
      fireEvent.click(trigger);

      const dialog = await screen.findByRole("dialog");
      expect(dialog.getAttribute("data-motion-surface")).toBe("modal");
      expect(dialog.parentElement?.getAttribute("data-motion-layer")).toBe(
        "modal",
      );
      const close = screen.getByRole("button", { name: "关闭消息详情" });
      await waitFor(() => expect(document.activeElement).toBe(close));
      expect(dialog.getAttribute("aria-modal")).toBe("true");
      expect(background?.getAttribute("aria-hidden")).toBe("true");

      fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
      expect(dialog.contains(document.activeElement)).toBe(true);
      trigger.focus();
      fireEvent.keyDown(document, { key: "Tab" });
      expect(dialog.contains(document.activeElement)).toBe(true);

      if (method === "按钮") fireEvent.click(close);
      else if (method === "Escape") {
        fireEvent.keyDown(document, { key: "Escape" });
      } else {
        fireEvent.click(
          document.querySelector(".message-dialog-backdrop") as HTMLElement,
        );
      }

      expect(screen.queryByRole("dialog")).toBeNull();
      await waitFor(() => expect(document.activeElement).toBe(trigger));
      expect(background?.getAttribute("aria-hidden")).toBe("false");
      expect(background?.hasAttribute("inert")).toBe(false);
    },
  );

  it("区分系统空态与筛选无结果，并提供真实恢复动作", async () => {
    vi.mocked(getMessages).mockResolvedValue({ items: [] });
    render(<MessagesView />);

    expect(await screen.findByText("暂无消息")).toBeTruthy();
    expect(screen.getByText("共 0 条消息").getAttribute("aria-live")).toBe(
      "polite",
    );
    fireEvent.click(screen.getByRole("button", { name: "重新检查" }));
    await waitFor(() => expect(getMessages).toHaveBeenCalledTimes(2));

    const emailTab = screen.getByRole("tab", { name: "邮件" });
    fireEvent.mouseDown(emailTab, { button: 0, ctrlKey: false });
    expect(await screen.findByText("当前筛选没有结果")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "清除筛选" }));
    await waitFor(() =>
      expect(
        screen.getByRole("tab", { name: "全部" }).getAttribute("data-state"),
      ).toBe("active"),
    );
  });

  it("卸载弹窗时清除初始焦点 timer", async () => {
    const { unmount } = render(<MessagesView />);
    const trigger = await screen.findByRole("button", {
      name: "打开消息详情：邮件一",
    });
    vi.useFakeTimers();
    fireEvent.click(trigger);
    const timersAfterOpen = vi.getTimerCount();
    expect(timersAfterOpen).toBeGreaterThan(0);
    unmount();
    expect(vi.getTimerCount()).toBeLessThan(timersAfterOpen);
  });
});
