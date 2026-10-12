// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { AIChatMessageBubble } from "@/components/AIChatMessageBubble";
import { AIChatView } from "@/components/AIChatView";
import type { AIChatSession } from "@/lib/types";
import { cleanup, fireEvent, render, screen } from "@/test/render";

const { markdownRender } = vi.hoisted(() => ({ markdownRender: vi.fn() }));
vi.mock("react-markdown", async (importOriginal) => {
  const original = await importOriginal<typeof import("react-markdown")>();
  return {
    ...original,
    default: (props: Parameters<typeof original.default>[0]) => {
      markdownRender(props.children);
      return original.default(props);
    },
  };
});
const history: AIChatSession = {
  id: "history",
  title: "长对话",
  model: "auto",
  thinking_depth: "standard",
  preset_id: "general",
  messages: Array.from({ length: 60 }, (_, index) => ({
    id: `message-${index}`,
    role: "assistant",
    content: `## 历史消息 ${index}\n\n| 课程 | 内容 |\n| --- | --- |\n| 文本分析 | **重点** |`,
  })),
  traces: [],
};
vi.mock("@/lib/api", () => ({
  AI_CHAT_STREAM_EVENT: "sjtu-ai-chat-stream",
  getAiPresets: vi.fn(async () => ({
    default_preset_id: "general",
    items: [{ id: "general", name: "学习 Agent", allowed_tools: [] }],
  })),
  getAiChatSessions: vi.fn(async () => ({ items: [history] })),
  getAiChatSession: vi.fn(async () => history),
  getSettings: vi.fn(async () => ({
    ai_chat_send_shortcut: "enter",
    ai_reply_language: "auto",
    ai_attachment_context_budget: "balanced",
    ai_auto_open_activity: false,
    ai_code_line_numbers: false,
  })),
}));
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  window.localStorage.clear();
});
describe("chat render workload", () => {
  it("does not parse history again while typing or opening Activity", async () => {
    render(<AIChatView onBack={vi.fn()} />);
    await screen.findByRole("heading", { name: "历史消息 59" });
    expect(markdownRender).toHaveBeenCalledTimes(60);
    markdownRender.mockClear();
    const input = screen.getByRole("textbox", { name: "输入问题" });
    for (const value of ["请", "请查", "请查课程"])
      fireEvent.change(input, { target: { value } });
    fireEvent.click(
      screen.getByRole("button", { name: "切换 Activity 检索轨迹" }),
    );
    expect(markdownRender).not.toHaveBeenCalled();
    expect((input as HTMLTextAreaElement).value).toBe("请查课程");
    expect(screen.getAllByRole("table")).toHaveLength(60);
  });
  it("updates changed text and code preferences without reparsing unrelated message updates", () => {
    const props = {
      message: {
        id: "answer",
        role: "assistant" as const,
        content: "**最初回答**",
      },
      agentName: "学习 Agent",
      onOpenActivity: vi.fn(),
      onRevealAttachment: vi.fn(),
      showCodeLineNumbers: false,
    };
    const { rerender, container } = render(<AIChatMessageBubble {...props} />);
    markdownRender.mockClear();
    rerender(<AIChatMessageBubble {...props} queued />);
    expect(markdownRender).not.toHaveBeenCalled();
    const changed = {
      ...props,
      message: { ...props.message, content: "```js\nlet x = 1;\n```" },
    };
    rerender(<AIChatMessageBubble {...changed} />);
    expect(markdownRender).toHaveBeenCalledTimes(1);
    expect(screen.getByText("let x = 1;")).toBeTruthy();
    rerender(<AIChatMessageBubble {...changed} showCodeLineNumbers />);
    expect(markdownRender).toHaveBeenCalledTimes(2);
    expect(container.querySelector(".ai-code-line-number")?.textContent).toBe(
      "1",
    );
  });
});
