// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { AIChatView } from "@/components/AIChatView";
import {
  createAiChatSession,
  getAiChatSession,
  getAiChatSessions,
  sendAiChatMessage,
} from "@/lib/api";
import { cleanup, fireEvent, render, screen } from "@/test/render";

vi.mock("@/lib/api", () => ({
  getAiChatSessions: vi.fn(async () => ({ items: [] })),
  getAiChatSession: vi.fn(),
  createAiChatSession: vi.fn(async () => ({
    id: "session-1",
    title: "新对话",
    model: "auto",
    thinking_depth: "standard",
    messages: [],
  })),
  sendAiChatMessage: vi.fn(async () => ({
    session: {
      id: "session-1",
      title: "最近一周待办",
      model: "auto",
      thinking_depth: "deep",
    },
    user_message: { id: "1", role: "user", content: "最近有什么消息？" },
    assistant_message: {
      id: "2",
      role: "assistant",
      content: "## 优先事项\n\n- 完成明天截止的作业",
      reasoning_content: "先比较截止时间。",
      model: "deepseek-reasoner",
    },
  })),
  deleteAiChatSession: vi.fn(async () => ({ deleted: true })),
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("AIChatView", () => {
  it("opens as an independent workspace and creates a persisted conversation", async () => {
    render(<AIChatView onBack={vi.fn()} />);
    expect(
      await screen.findByRole("heading", { name: "今天想先处理什么？" }),
    ).toBeTruthy();
    expect(createAiChatSession).toHaveBeenCalledWith("auto", "standard");
    fireEvent.click(
      screen.getByRole("button", { name: "帮我整理最近一周的待办和优先级" }),
    );
    expect(
      await screen.findByRole("heading", { name: "优先事项" }),
    ).toBeTruthy();
  });

  it("sends selected model/depth and exposes reasoning and copy controls", async () => {
    render(<AIChatView onBack={vi.fn()} />);
    await screen.findByRole("textbox", { name: "输入问题" });
    fireEvent.change(screen.getByRole("combobox", { name: "思考深度" }), {
      target: { value: "deep" },
    });
    const input = screen.getByRole("textbox", { name: "输入问题" });
    fireEvent.change(input, { target: { value: "最近有什么消息？" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(await screen.findByText("查看思考过程")).toBeTruthy();
    expect(sendAiChatMessage).toHaveBeenCalledWith(
      "session-1",
      "最近有什么消息？",
      "auto",
      "deep",
    );
    expect(screen.getAllByRole("button", { name: "复制消息" })).toHaveLength(2);
  });

  it("loads an existing history session", async () => {
    vi.mocked(getAiChatSessions).mockResolvedValueOnce({
      items: [
        {
          id: "existing",
          title: "复习计划",
          model: "deepseek-chat",
          thinking_depth: "standard",
        },
      ],
    });
    vi.mocked(getAiChatSession).mockResolvedValueOnce({
      id: "existing",
      title: "复习计划",
      model: "deepseek-chat",
      thinking_depth: "standard",
      messages: [{ id: "3", role: "assistant", content: "历史回答" }],
    });
    render(<AIChatView onBack={vi.fn()} />);
    expect(await screen.findByText("历史回答")).toBeTruthy();
  });
});
