// @vitest-environment jsdom
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MarkdownSyncProgress } from "@/components/MarkdownSyncProgress";
import { getMarkdownSyncProgress } from "@/lib/api";

vi.mock("@/lib/api", () => ({ getMarkdownSyncProgress: vi.fn() }));
const group = {
  total: 99,
  completed: 99,
  pending: 0,
  failed: 0,
  ai_completed: 10,
  ai_failed: 81,
  issues: [{ name: "课堂课件.pdf", code: "timeout" }],
  truncated: false,
};
const state = {
  courses: group,
  transcripts: {
    ...group,
    total: 0,
    completed: 0,
    ai_completed: 0,
    ai_failed: 0,
    issues: [],
  },
  automatic: true,
  ai_ready: true,
  ai_model: "deepseek-chat",
  current: {
    status: "running",
    phase: "ai_summary",
    current_file: "第一讲.pdf",
  },
  updated_at: "2026-11-22",
};
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.useRealTimers();
});
describe("同步进度小窗口", () => {
  it("默认不轮询，按需展示基础完成和独立总结、空字幕及异常", async () => {
    vi.mocked(getMarkdownSyncProgress).mockResolvedValue(state);
    render(<MarkdownSyncProgress onNavigate={vi.fn()} />);
    expect(getMarkdownSyncProgress).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "同步进度" }));
    await screen.findByText("基础 MD 转换");
    await waitFor(() => expect(screen.getByText("99 / 99")).toBeTruthy());
    expect(screen.getByText("10 / 99")).toBeTruthy();
    expect(screen.getByText("暂无已保存字幕")).toBeTruthy();
    const progress = screen.getByLabelText("课件基础 MD 转换进度");
    expect(progress.getAttribute("value")).toBe("99");
    fireEvent.click(screen.getByText("查看异常 · 总结 81"));
    expect(screen.getByText("请求超时")).toBeTruthy();
    fireEvent.keyDown(screen.getByRole("button", { name: "关闭同步进度" }), {
      key: "Escape",
    });
    expect(screen.queryByText("基础 MD 转换")).toBeNull();
    expect(document.activeElement).toBe(
      screen.getByRole("button", { name: "同步进度" }),
    );
  });
  it("读取失败可重试，设置及知识库入口可导航", async () => {
    const navigate = vi.fn();
    vi.mocked(getMarkdownSyncProgress)
      .mockRejectedValueOnce(Error("offline"))
      .mockResolvedValue(state);
    render(<MarkdownSyncProgress onNavigate={navigate} />);
    fireEvent.click(screen.getByRole("button", { name: "同步进度" }));
    await screen.findByRole("alert");
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    await screen.findByText("99 / 99");
    fireEvent.click(screen.getByRole("button", { name: "查看知识库" }));
    expect(navigate).toHaveBeenCalledWith("knowledge");
    expect(screen.queryByText("基础 MD 转换")).toBeNull();
  });
  it("不重叠请求，关窗停止轮询，旧响应不改已关闭窗口", async () => {
    vi.useFakeTimers();
    let resolve: (value: typeof state) => void = () => {};
    vi.mocked(getMarkdownSyncProgress).mockImplementation(
      () =>
        new Promise((done) => {
          resolve = done;
        }),
    );
    render(<MarkdownSyncProgress onNavigate={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "同步进度" }));
    await act(() => vi.advanceTimersByTimeAsync(15_000));
    expect(getMarkdownSyncProgress).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "关闭同步进度" }));
    await act(async () => {
      resolve(state);
      await vi.advanceTimersByTimeAsync(15_000);
    });
    expect(getMarkdownSyncProgress).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("99 / 99")).toBeNull();
  });
  it("阻断任务不把旧文件名显示为正在整理", async () => {
    vi.mocked(getMarkdownSyncProgress).mockResolvedValue({
      ...state,
      current: { ...state.current, status: "blocked" },
    });
    render(<MarkdownSyncProgress onNavigate={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "同步进度" }));
    await screen.findByText("课件同步暂不可用，请检查设置");
    expect(screen.queryByText(/正在整理/)).toBeNull();
  });
  it("读取失败保留计数但停止动态文案并标记旧快照", async () => {
    vi.mocked(getMarkdownSyncProgress)
      .mockResolvedValueOnce(state)
      .mockRejectedValue(Error("offline"));
    render(<MarkdownSyncProgress onNavigate={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "同步进度" }));
    await screen.findByText(/正在整理/);
    fireEvent.click(screen.getByRole("button", { name: "关闭同步进度" }));
    fireEvent.click(screen.getByRole("button", { name: "同步进度" }));
    await screen.findByRole("alert");
    expect(screen.queryByText(/正在整理/)).toBeNull();
    expect(screen.getByText(/上次读取：/)).toBeTruthy();
    expect(screen.getByText("99 / 99")).toBeTruthy();
  });
});
