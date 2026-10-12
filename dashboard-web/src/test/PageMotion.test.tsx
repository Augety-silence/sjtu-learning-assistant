// @vitest-environment jsdom
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AIChatView } from "@/components/AIChatView";
import { getAiPresets } from "@/lib/api";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@/test/render";

const { present } = vi.hoisted(() => ({ present: vi.fn(() => true) }));
vi.mock("motion/react", async (importOriginal) => ({
  ...(await importOriginal<typeof import("motion/react")>()),
  useIsPresent: present,
}));

const presetResult = {
  default_preset_id: "general",
  items: [
    {
      id: "general",
      name: "学习 Agent",
      description: "测试",
      allowed_tools: [],
      is_default: true,
    },
  ],
};
vi.mock("@/lib/api", () => ({
  getAiPresets: vi.fn(),
  getAiChatSessions: vi.fn(async () => ({ items: [] })),
  getSettings: vi.fn(async () => ({})),
  createAiChatSession: vi.fn(async () => ({
    id: "test",
    title: "新对话",
    model: "auto",
    thinking_depth: "standard",
    preset_id: "general",
    messages: [],
    traces: [],
  })),
}));

beforeEach(() => {
  present.mockReturnValue(true);
  vi.mocked(getAiPresets).mockResolvedValue(presetResult);
});
afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe("page entry lifecycle", () => {
  it("shows assistant chrome during loading and preserves typing when the thread becomes ready", async () => {
    let finishPresets: ((value: typeof presetResult) => void) | undefined;
    vi.mocked(getAiPresets).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finishPresets = resolve;
        }),
    );
    const { container } = render(<AIChatView onBack={vi.fn()} />);
    const shell = container.querySelector(".ai-workspace");
    const thread = container.querySelector(".ai-chat-thread");
    const input = screen.getByRole("textbox", { name: "输入问题" });
    expect(shell?.getAttribute("data-page-reveal")).toBe("ready");
    expect(shell?.getAttribute("data-page-reveal-sequence")).toBe("manual");
    expect(thread?.getAttribute("data-content-reveal")).toBe("loading");
    expect(document.activeElement).not.toBe(input);
    input.focus();
    fireEvent.change(input, { target: { value: "加载时也能继续准备问题" } });
    await act(async () => finishPresets?.(presetResult));
    await waitFor(() =>
      expect(thread?.getAttribute("data-content-reveal")).toBe("ready"),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "切换 Activity 检索轨迹" }),
    );
    expect(container.querySelector(".ai-workspace")).toBe(shell);
    expect(container.querySelector(".ai-chat-thread")).toBe(thread);
    expect(shell?.getAttribute("data-page-reveal")).toBe("ready");
    expect((input as HTMLTextAreaElement).value).toBe("加载时也能继续准备问题");
    expect(document.activeElement).toBe(input);
  });

  it("detaches workspace keyboard handling when the departing assistant loses presence", async () => {
    const { rerender } = render(<AIChatView onBack={vi.fn()} />);
    const toggle = screen.getByRole("button", {
      name: "切换 Activity 检索轨迹",
    });
    await screen.findByText("今天想从学习数据里查什么？");
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    present.mockReturnValue(false);
    rerender(<AIChatView onBack={vi.fn()} />);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
  });

  it("cancels pending prompt focus during a rapid exit", async () => {
    vi.useFakeTimers();
    window.localStorage.setItem("ai-chat-pending-prompt", "从日程带过来的问题");
    const { rerender } = render(<AIChatView onBack={vi.fn()} />);
    const input = screen.getByRole("textbox", { name: "输入问题" });
    const focus = vi.spyOn(input, "focus");
    present.mockReturnValue(false);
    rerender(<AIChatView onBack={vi.fn()} />);
    await act(async () => vi.advanceTimersByTime(80));
    expect(focus).not.toHaveBeenCalled();
  });

  it("retains normal pending prompt focus through Strict Mode effect cleanup", async () => {
    vi.useFakeTimers();
    window.localStorage.setItem("ai-chat-pending-prompt", "待查询的课程");
    render(
      <StrictMode>
        <AIChatView onBack={vi.fn()} />
      </StrictMode>,
    );
    const input = screen.getByRole("textbox", { name: "输入问题" });
    await act(async () => vi.advanceTimersByTime(80));
    expect((input as HTMLTextAreaElement).value).toBe("待查询的课程");
    expect(document.activeElement).toBe(input);
  });
});
