// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TokenUsageDashboard } from "@/components/TokenUsageDashboard";
import { getProviderUsage, type ProviderUsageSummary } from "@/lib/api";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@/test/render";

vi.mock("@/lib/api", () => ({ getProviderUsage: vi.fn() }));
const period = {
  calls: 2,
  metrics: {
    input: { tokens: 1000, reported_calls: 2, missing_calls: 0 },
    output: { tokens: 20, reported_calls: 2, missing_calls: 0 },
    cache: { tokens: null, reported_calls: 0, missing_calls: 2 },
    total: { tokens: 1020, reported_calls: 2, missing_calls: 0 },
  },
};
const report: ProviderUsageSummary = {
  periods: {
    today: period,
    week: { ...period, calls: 3 },
    all: { ...period, calls: 4 },
  },
  first_recorded_at: null,
  timezone: "CST",
  scope: "local_app_since_upgrade",
};
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getProviderUsage).mockResolvedValue(report);
});
describe("TokenUsageDashboard", () => {
  it("显示接口实报、缓存未知和统计周期；不把缓存加到总量", async () => {
    render(<TokenUsageDashboard />);
    await screen.findByText("1,020");
    expect(screen.getByText("未报告")).toBeTruthy();
    expect(screen.getByText("2 次未报告")).toBeTruthy();
    fireEvent.change(screen.getByRole("combobox", { name: "统计范围" }), {
      target: { value: "week" },
    });
    expect(screen.getByText(/3 次请求/)).toBeTruthy();
  });
  it("失败可重试，不阻塞设置页", async () => {
    vi.mocked(getProviderUsage).mockRejectedValueOnce(new Error("private"));
    render(<TokenUsageDashboard />);
    await screen.findByText("用量读取失败");
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    await screen.findByText("1,020");
    expect(screen.queryByText("用量读取失败")).toBeNull();
  });
  it("空账本显示零，已有请求但无usage不显示零", async () => {
    const empty = {
      ...report,
      periods: { ...report.periods, today: { ...period, calls: 0 } },
    };
    vi.mocked(getProviderUsage).mockResolvedValue(empty);
    render(<TokenUsageDashboard />);
    await waitFor(() => expect(screen.getAllByText("0")).toHaveLength(4));
  });
  it("卸载释放刷新计时器且忽略迟到响应", async () => {
    vi.useFakeTimers();
    const view = render(<TokenUsageDashboard />);
    await act(async () => Promise.resolve());
    view.unmount();
    await act(async () => vi.advanceTimersByTimeAsync(60_000));
    expect(getProviderUsage).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });
});
