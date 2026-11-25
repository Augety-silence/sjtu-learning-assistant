// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MessageMarkdown } from "@/components/AIMarkdown";
import { MessagesView } from "@/components/MessagesView";
import {
  getMessageDetail,
  getMessageReference,
  getMessages,
  markMessagesRead,
  openExternal,
} from "@/lib/api";
import type { MessageItem } from "@/lib/types";
import { cleanup, fireEvent, render, screen, waitFor } from "@/test/render";

vi.mock("@/lib/api", () => ({
  getMessageDetail: vi.fn(),
  getMessageReference: vi.fn(),
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
    occurred_at: null,
    is_unread: true,
    url: null,
    preview: "邮件摘要",
    attachment_count: 2,
  },
  {
    source_id: "work-1",
    kind: "assignment",
    title: "作业一",
    source_label: "程序设计",
    occurred_at: null,
    is_unread: true,
    url: "https://canvas.example/1",
  },
];
beforeEach(() => {
  vi.clearAllMocks();
  window.sessionStorage.clear();
  vi.mocked(getMessages).mockResolvedValue({
    items: messages,
    total: 151,
    unread: 120,
  });
  vi.mocked(getMessageDetail).mockImplementation(async (kind) => ({
    title: kind === "email" ? "邮件一" : "作业一",
    source_label: "老师",
    occurred_at: null,
    body: "纯文本 <script>test</script>",
    body_html: null,
    format: "text",
    pending_body_sync: false,
    attachments: [],
    resources: [],
    url: kind === "assignment" ? "https://canvas.example/1" : null,
    is_unread: true,
  }));
  vi.mocked(markMessagesRead).mockResolvedValue({ updated: 1 });
  vi.mocked(openExternal).mockResolvedValue({ status: "opened" });
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});
describe("消息中心", () => {
  it("双栏直接显示正文、摘要、总数及附件，不展示多余归档标签", async () => {
    render(<MessagesView />);
    const list = await screen.findByRole("list", { name: "消息列表" });
    expect(list.querySelectorAll('[role="listitem"]').length).toBe(2);
    expect(await screen.findByRole("document")).toBeTruthy();
    expect(screen.getByText("邮件摘要")).toBeTruthy();
    expect(screen.getByText("2 个附件")).toBeTruthy();
    expect(screen.getByText("151")).toBeTruthy();
    expect(screen.queryByText("本地归档")).toBeNull();
    expect(screen.queryByText("本地归档可搜索")).toBeNull();
    expect(document.querySelector("script")).toBeNull();
  });
  it("组合关键词和分类传给全库查询", async () => {
    render(<MessagesView />);
    await screen.findByRole("document");
    fireEvent.change(screen.getByRole("searchbox", { name: "搜索消息" }), {
      target: { value: "老师 考试" },
    });
    await waitFor(() =>
      expect(getMessages).toHaveBeenLastCalledWith("all", {
        query: "老师 考试",
        limit: 60,
      }),
    );
    fireEvent.mouseDown(screen.getByRole("tab", { name: "邮件" }), {
      button: 0,
      ctrlKey: false,
    });
    await waitFor(() =>
      expect(getMessages).toHaveBeenLastCalledWith("email", {
        query: "老师 考试",
        limit: 60,
      }),
    );
  });
  it("继续分页可打开第101条历史消息", async () => {
    vi.mocked(getMessages)
      .mockResolvedValueOnce({
        items: messages,
        total: 151,
        unread: 120,
        next_offset: 100,
      })
      .mockResolvedValueOnce({
        items: [{ ...messages[0], source_id: "old-101", title: "第101条" }],
        total: 151,
        next_offset: null,
      });
    render(<MessagesView />);
    fireEvent.click(await screen.findByRole("button", { name: "加载更多" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "打开消息详情：第101条" }),
    );
    await waitFor(() =>
      expect(getMessageDetail).toHaveBeenLastCalledWith("email", "old-101"),
    );
    expect(getMessages).toHaveBeenLastCalledWith("all", {
      query: "",
      offset: 100,
      limit: 60,
    });
  });
  it("已读更新与当前关键词筛选一致", async () => {
    render(<MessagesView />);
    fireEvent.click(
      await screen.findByRole("button", { name: "将“邮件一”标记为已读" }),
    );
    await waitFor(() =>
      expect(markMessagesRead).toHaveBeenCalledWith({
        kind: "email",
        ids: ["mail-1"],
      }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "将当前筛选中的消息全部标记为已读" }),
    );
    await waitFor(() =>
      expect(markMessagesRead).toHaveBeenCalledWith({
        kind: "all",
        all: true,
        query: "",
      }),
    );
  });
  it("切换消息后迟到的旧正文不能覆盖新消息", async () => {
    let resolveOld:
      | ((value: Awaited<ReturnType<typeof getMessageDetail>>) => void)
      | undefined;
    vi.mocked(getMessageDetail).mockReturnValueOnce(
      new Promise((resolve) => {
        resolveOld = resolve;
      }),
    );
    render(<MessagesView />);
    fireEvent.click(
      await screen.findByRole("button", { name: "打开消息详情：作业一" }),
    );
    await screen.findByRole("document");
    resolveOld?.({
      title: "旧的邮件",
      source_label: "旧",
      occurred_at: null,
      body: "迟到正文",
      body_html: null,
      format: "text",
      pending_body_sync: false,
      attachments: [],
      resources: [],
      url: null,
      is_unread: true,
    });
    await waitFor(() => expect(screen.queryByText("迟到正文")).toBeNull());
  });
  it("键盘选择不会拦截搜索输入", async () => {
    render(<MessagesView />);
    const first = await screen.findByRole("button", {
      name: "打开消息详情：邮件一",
    });
    first.focus();
    fireEvent.keyDown(document, { key: "j" });
    await waitFor(() =>
      expect(getMessageDetail).toHaveBeenLastCalledWith("assignment", "work-1"),
    );
    vi.mocked(markMessagesRead).mockClear();
    const input = screen.getByRole("searchbox");
    input.focus();
    fireEvent.keyDown(input, { key: "r" });
    expect(markMessagesRead).not.toHaveBeenCalled();
  });
  it("AI本地引用导航无需打开外部窗口", () => {
    const listener = vi.fn();
    window.addEventListener("sjtu-open-message", listener);
    render(
      <MessageMarkdown content="[历史通知](#/messages?kind=email&source_id=old-101)" />,
    );
    fireEvent.click(screen.getByRole("link", { name: "历史通知" }));
    expect(listener).toHaveBeenCalledOnce();
    expect(openExternal).not.toHaveBeenCalled();
    window.removeEventListener("sjtu-open-message", listener);
  });
  it("AI引用定位当前分页之外的本地消息", async () => {
    vi.mocked(getMessageReference).mockResolvedValue({
      item: { ...messages[0], title: "旧考试通知", source_id: "old-101" },
    });
    window.sessionStorage.setItem(
      "sjtu-message-citation",
      "#/messages?kind=email&ref=email:101",
    );
    render(<MessagesView />);
    await screen.findByRole("heading", { name: "旧考试通知" });
    expect(getMessageReference).toHaveBeenCalledWith("email", "email:101");
    expect(getMessageDetail).toHaveBeenLastCalledWith("email", "old-101");
  });
  it("空结果保留搜索和分类入口", async () => {
    vi.mocked(getMessages).mockResolvedValue({
      items: [],
      total: 0,
      unread: 0,
    });
    render(<MessagesView />);
    await screen.findByText("暂无消息");
    fireEvent.change(screen.getByRole("searchbox"), {
      target: { value: "不存在" },
    });
    await screen.findByText("没有找到相关消息");
    fireEvent.click(screen.getByRole("button", { name: "清除筛选" }));
    await waitFor(() =>
      expect((screen.getByRole("searchbox") as HTMLInputElement).value).toBe(
        "",
      ),
    );
  });
});
