// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import {
  type CanvasAgentMessage,
  CanvasAgentView,
} from "@/components/CanvasAgentView";
import { cleanup, fireEvent, render, screen, waitFor } from "@/test/render";

afterEach(cleanup);

const messages: CanvasAgentMessage[] = [
  {
    id: "m1",
    role: "assistant",
    content: "已找到 **2 项作业**。",
    toolCalls: [
      {
        id: "tool-1",
        name: "list_assignments",
        label: "查询作业",
        status: "completed",
        resultSummary: "2 项",
      },
    ],
  },
];

describe("CanvasAgentView", () => {
  it("reuses chat semantics and exposes Canvas tool traces", () => {
    render(
      <CanvasAgentView
        messages={messages}
        tools={[{ name: "list_assignments", label: "作业" }]}
        onSend={vi.fn()}
      />,
    );
    expect(
      screen.getByRole("main", { name: "Canvas Agent 对话" }),
    ).toBeTruthy();
    expect(screen.getByText("2 项作业")).toBeTruthy();
    fireEvent.click(screen.getByText(/查询作业/));
    expect(screen.getByText("结果：2 项")).toBeTruthy();
  });

  it("sends on Enter and keeps Shift+Enter for line breaks", async () => {
    const send = vi.fn(async () => undefined);
    render(
      <CanvasAgentView
        messages={[]}
        options={{ maxTurns: 4, maxTokens: 2048 }}
        onSend={send}
      />,
    );
    const input = screen.getByRole("textbox", { name: "输入 Canvas 问题" });
    fireEvent.change(input, { target: { value: "最近有哪些作业？" } });
    fireEvent.keyDown(input, { key: "Enter", shiftKey: true });
    expect(send).not.toHaveBeenCalled();
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() =>
      expect(send).toHaveBeenCalledWith({
        message: "最近有哪些作业？",
        history: [],
        options: { maxTurns: 4, maxTokens: 2048 },
      }),
    );
  });
});
