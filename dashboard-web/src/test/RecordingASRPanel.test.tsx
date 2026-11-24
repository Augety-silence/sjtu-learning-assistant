// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RecordingASRPanel } from "@/components/RecordingASRPanel";
import {
  getRecordingASRJobs,
  getRecordingASRStatus,
  pauseRecordingASR,
  resumeRecordingASR,
  retryRecordingASR,
  scanRecordingASR,
  startRecordingASR,
} from "@/lib/api";
import type { SettingsStatus } from "@/lib/types";
import { cleanup, fireEvent, render, screen, waitFor } from "@/test/render";

vi.mock("@/lib/api", () => ({
  getRecordingASRStatus: vi.fn(),
  getRecordingASRJobs: vi.fn(),
  pauseRecordingASR: vi.fn(),
  resumeRecordingASR: vi.fn(),
  retryRecordingASR: vi.fn(),
  scanRecordingASR: vi.fn(),
  startRecordingASR: vi.fn(),
}));
const settings = {
  asr_key_saved: true,
  asr_endpoint: "https://api.siliconflow.cn/v1/audio/transcriptions",
  asr_model: "XingChenAGI/XingChenASR-V3.2-Ultra",
  ai_key_saved: false,
  ai_enabled: false,
} as SettingsStatus;
beforeEach(() => {
  vi.mocked(getRecordingASRStatus).mockResolvedValue({
    enabled: false,
    paused: true,
  });
  vi.mocked(getRecordingASRJobs).mockResolvedValue({ items: [] });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});
describe("录播批量ASR", () => {
  it("排空中及排空存储错误明确显示，不触发自动继续", async () => {
    vi.mocked(getRecordingASRStatus).mockResolvedValue({
      enabled: true,
      paused: true,
      draining: true,
      safe_to_exit: false,
    });
    const view = render(<RecordingASRPanel settings={settings} />);
    await screen.findByText(/正在保存已发送的语音响应，排空后再继续/);
    expect(resumeRecordingASR).not.toHaveBeenCalled();
    view.unmount();
    vi.mocked(getRecordingASRStatus).mockResolvedValue({
      enabled: true,
      paused: true,
      draining: false,
      safe_to_exit: false,
      drain_error: "存储排空失败",
      error: "存储排空失败",
    });
    render(<RecordingASRPanel settings={settings} />);
    await screen.findByText(/排空存储失败，已暂停；不会自动重发/);
    expect(await screen.findByRole("alert")).toHaveProperty(
      "textContent",
      "存储排空失败",
    );
    expect(resumeRecordingASR).not.toHaveBeenCalled();
  });
  it("默认仅查进度，双重同意前绝不外发，不依赖聊天密钥", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<RecordingASRPanel settings={settings} />);
    await waitFor(() => expect(getRecordingASRJobs).toHaveBeenCalledOnce());
    const start = screen.getByRole("button", { name: "授权并开始" });
    expect(start.hasAttribute("disabled")).toBe(true);
    expect(startRecordingASR).not.toHaveBeenCalled();
    expect(scanRecordingASR).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("checkbox"));
    expect(start.hasAttribute("disabled")).toBe(false);
    fireEvent.click(start);
    expect(startRecordingASR).not.toHaveBeenCalled();
    vi.mocked(window.confirm).mockReturnValue(true);
    vi.mocked(startRecordingASR).mockResolvedValue({
      enabled: true,
      paused: false,
    });
    fireEvent.click(start);
    await waitFor(() => expect(startRecordingASR).toHaveBeenCalledWith(true));
  });
  it("扫描不代替外发授权，缺少ASR密钥不能开始", async () => {
    vi.mocked(scanRecordingASR).mockResolvedValue({
      enabled: false,
      paused: true,
      scanning: true,
    });
    render(
      <RecordingASRPanel settings={{ ...settings, asr_key_saved: false }} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "扫描录播" }));
    await waitFor(() => expect(scanRecordingASR).toHaveBeenCalledOnce());
    fireEvent.click(screen.getByRole("checkbox"));
    expect(
      screen
        .getByRole("button", { name: "授权并开始" })
        .hasAttribute("disabled"),
    ).toBe(true);
    expect(startRecordingASR).not.toHaveBeenCalled();
  });
  it("显示真实进度与未覆盖范围，不把原文当审阅通过", async () => {
    vi.mocked(getRecordingASRStatus).mockResolvedValue({
      enabled: true,
      paused: true,
      busy: true,
      counts: { completed: 2, failed: 1 },
      coverage: {
        status: "partial",
        total_courses: 4,
        scanned_courses: 3,
        missing_sources: 1,
        inaccessible_courses: [
          {
            course_id: "4",
            course_name: "不可访问课程",
            reason: "目录不可访问",
          },
        ],
      },
    });
    vi.mocked(getRecordingASRJobs).mockResolvedValue({
      items: [
        { source_id: "sjtu-video:1:2", title: "完整原文", status: "completed" },
      ],
    });
    render(<RecordingASRPanel settings={settings} />);
    await screen.findByText(/MD完成\/人工未校对 2/);
    expect(screen.getByText(/课程扫描：3\/4/)).toBeTruthy();
    expect(screen.getByText(/正在保存已发送请求的响应/)).toBeTruthy();
    expect(screen.queryByText("AI审阅通过")).toBeNull();
  });
  it("暂停保留响应，继续须确认，有界重试未知响应须额外同意", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.mocked(getRecordingASRStatus).mockResolvedValue({
      enabled: true,
      paused: false,
    });
    vi.mocked(pauseRecordingASR).mockResolvedValue({
      enabled: true,
      paused: true,
    });
    render(<RecordingASRPanel settings={settings} />);
    fireEvent.click(await screen.findByRole("button", { name: "暂停转写" }));
    await waitFor(() => expect(pauseRecordingASR).toHaveBeenCalledOnce());
    cleanup();
    vi.mocked(getRecordingASRStatus).mockResolvedValue({
      enabled: true,
      paused: true,
    });
    vi.mocked(resumeRecordingASR).mockResolvedValue({
      enabled: true,
      paused: false,
    });
    vi.mocked(getRecordingASRJobs).mockResolvedValue({
      items: [{ source_id: "sjtu-video:1:2", status: "response_unknown" }],
    });
    vi.mocked(retryRecordingASR).mockResolvedValue({
      enabled: true,
      paused: true,
    });
    render(<RecordingASRPanel settings={settings} />);
    fireEvent.click(await screen.findByRole("button", { name: "继续转写" }));
    await waitFor(() => expect(resumeRecordingASR).toHaveBeenCalledOnce());
    fireEvent.click(
      await screen.findByRole("button", { name: "重试未完成部分" }),
    );
    await waitFor(() =>
      expect(retryRecordingASR).toHaveBeenCalledWith("sjtu-video:1:2", true),
    );
    expect(window.confirm).toHaveBeenLastCalledWith(
      expect.stringContaining("重复扣费"),
    );
  });
  it("操作失败显示错误且不自动重发", async () => {
    vi.mocked(scanRecordingASR).mockRejectedValue(new Error("目录不可访问"));
    render(<RecordingASRPanel settings={settings} />);
    fireEvent.click(screen.getByRole("button", { name: "扫描录播" }));
    expect(await screen.findByRole("alert")).toHaveProperty(
      "textContent",
      "目录不可访问",
    );
    expect(scanRecordingASR).toHaveBeenCalledOnce();
    expect(startRecordingASR).not.toHaveBeenCalled();
  });
});
