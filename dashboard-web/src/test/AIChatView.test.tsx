// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { AIChatView } from "@/components/AIChatView";
import { cleanup, fireEvent, render, screen, waitFor } from "@/test/render";

vi.mock("@/lib/api", () => ({
  sendAiChat: vi.fn(async () => ({
    reply: "先完成明天截止的作业。",
    model: "qwen",
  })),
}));

afterEach(() => cleanup());

describe("AIChatView", () => {
  it("shows guided starters and sends a conversation", async () => {
    render(<AIChatView />);
    expect(
      screen.getByRole("heading", { name: "今天想先处理什么？" }),
    ).toBeTruthy();
    fireEvent.click(
      screen.getByRole("button", { name: "帮我整理最近一周的待办和优先级" }),
    );
    expect(screen.getByText("AI 正在整理学习信息…")).toBeTruthy();
    expect(await screen.findByText("先完成明天截止的作业。")).toBeTruthy();
  });

  it("supports keyboard send and clearing the in-memory conversation", async () => {
    render(<AIChatView />);
    const input = screen.getByRole("textbox", { name: "输入问题" });
    fireEvent.change(input, { target: { value: "最近有什么消息？" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await screen.findByText("先完成明天截止的作业。");
    fireEvent.click(screen.getByRole("button", { name: "新对话" }));
    await waitFor(() =>
      expect(screen.queryByText("最近有什么消息？")).toBeNull(),
    );
  });
});
