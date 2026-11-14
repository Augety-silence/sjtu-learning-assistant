// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { CourseMarkdownSyncPanel } from "@/components/CourseMarkdownSyncPanel";
import { getCourseMarkdownStatus, updateSettings } from "@/lib/api";
import { cleanup, fireEvent, render, screen, waitFor } from "@/test/render";

vi.mock("@/lib/api", () => ({
  getCourseMarkdownStatus: vi.fn(),
  updateSettings: vi.fn(),
}));
const state = {
  enabled: false,
  status: "disabled",
  output_root: "/archive/Knowledge Markdown",
  daily_limit: 100,
  calls_today: 0,
  total: 87,
  completed: 0,
  pending: 87,
  failed: 0,
  issues: [],
};
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getCourseMarkdownStatus).mockResolvedValue(state);
  vi.mocked(updateSettings).mockResolvedValue({} as never);
});
afterEach(cleanup);
it("使用已配置目录开启自动同步，不要求选目录", async () => {
  render(<CourseMarkdownSyncPanel />);
  const button = await screen.findByRole("button", {
    name: "开启课件自动同步",
  });
  await waitFor(() => expect(button.hasAttribute("disabled")).toBe(false));
  fireEvent.click(button);
  await waitFor(() =>
    expect(updateSettings).toHaveBeenCalledWith({
      course_markdown_enabled: true,
      course_markdown_daily_calls: 100,
    }),
  );
  expect(screen.getByText(/会将已下载 PPTX/)).toBeTruthy();
  expect(screen.queryByRole("button", { name: "选择目录" })).toBeNull();
});
it("已启用时可暂停，并显示额度与问题", async () => {
  vi.mocked(getCourseMarkdownStatus).mockResolvedValue({
    ...state,
    enabled: true,
    status: "budget_paused",
    calls_today: 100,
    issues: ["bad.ppt：需导出 PDF"],
  });
  render(<CourseMarkdownSyncPanel />);
  const button = await screen.findByRole("button", { name: "暂停自动同步" });
  await waitFor(() => expect(button.hasAttribute("disabled")).toBe(false));
  fireEvent.change(screen.getByRole("spinbutton"), { target: { value: "" } });
  fireEvent.click(button);
  await waitFor(() =>
    expect(updateSettings).toHaveBeenCalledWith({
      course_markdown_enabled: false,
    }),
  );
  expect(screen.getByText(/次日自动续编/)).toBeTruthy();
  expect(screen.getByText("bad.ppt：需导出 PDF")).toBeTruthy();
});
