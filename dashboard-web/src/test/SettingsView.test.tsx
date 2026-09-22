// @vitest-environment jsdom

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SettingsView } from "@/components/SettingsView";
import {
  getSettings,
  importAiConnection,
  organizeArchive,
  pickArchiveRoot,
  testAiConnection,
  updateSettings,
} from "@/lib/api";
import type { SettingsStatus } from "@/lib/types";

vi.mock("@/lib/api", () => ({
  getSettings: vi.fn(),
  importAiConnection: vi.fn(),
  organizeArchive: vi.fn(),
  pickArchiveRoot: vi.fn(),
  testAiConnection: vi.fn(),
  updateSettings: vi.fn(),
}));

const status: SettingsStatus = {
  archive_root_ready: true,
  archive_root: "/tmp/SJTU Study",
  auto_download_current_term: true,
  organize_by_category: true,
  mail_account: "",
  ai_enabled: false,
  ai_base_url: "https://models.sjtu.edu.cn/api/v1",
  ai_model: "deepseek-chat",
  ai_key_saved: false,
};

describe("SettingsView", () => {
  afterEach(cleanup);
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getSettings).mockResolvedValue(status);
    vi.mocked(updateSettings).mockResolvedValue(status);
    vi.mocked(importAiConnection).mockResolvedValue({
      ...status,
      ai_key_saved: true,
    });
    vi.mocked(testAiConnection).mockResolvedValue({
      ok: true,
      model: "deepseek-chat",
      category: "courseware",
    });
    vi.mocked(pickArchiveRoot).mockResolvedValue({
      cancelled: false,
      settings: { ...status, archive_root: "/tmp/archive" },
    });
    vi.mocked(organizeArchive).mockResolvedValue({
      classified: 2,
      reused: 1,
      fallback: 0,
      moved: 2,
      unchanged: 1,
      failed: 0,
    });
  });

  it("loads settings without exposing a key and sends a strict toggle payload", async () => {
    render(<SettingsView />);
    expect(await screen.findByText(status.archive_root)).toBeTruthy();
    expect(screen.queryByText("Canvas Token")).toBeNull();
    expect(screen.queryByLabelText("邮箱密码")).toBeNull();
    expect(
      screen.getByText(/未设置邮箱账号；当前同步仅运行 Canvas/),
    ).toBeTruthy();
    expect(screen.getByText(/不会上传文件正文/)).toBeTruthy();
    expect(
      screen.getByText(/首次 macOS 授权请选择“始终允许”/),
    ).toBeTruthy();
    expect(getSettings).toHaveBeenCalledTimes(1);
    const switches = screen.getAllByRole("switch");
    fireEvent.click(switches[0]);
    await waitFor(() =>
      expect(updateSettings).toHaveBeenCalledWith({
        auto_download_current_term: false,
      }),
    );
  });

  it("saves a non-secret mail account explicitly", async () => {
    vi.mocked(updateSettings).mockResolvedValue({
      ...status,
      mail_account: "student-id",
    });
    render(<SettingsView />);
    const input = await screen.findByLabelText("邮箱账号");
    fireEvent.change(input, { target: { value: " student-id " } });
    fireEvent.click(screen.getByRole("button", { name: "保存邮箱账号" }));
    await waitFor(() =>
      expect(updateSettings).toHaveBeenCalledWith({
        mail_account: "student-id",
      }),
    );
  });

  it("imports fixed JSON and enables the connection test without rendering the key", async () => {
    render(<SettingsView />);
    const config =
      '{"_type":"newapi_channel_conn","url":"https://models.sjtu.edu.cn/api/v1","key":"test-secret","model":"deepseek-chat"}';
    const textarea = await screen.findByLabelText("粘贴连接配置 JSON");
    fireEvent.change(textarea, { target: { value: config } });
    fireEvent.click(screen.getByRole("button", { name: "保存连接配置" }));
    await waitFor(() =>
      expect(importAiConnection).toHaveBeenCalledWith(config),
    );
    expect(screen.queryByDisplayValue("test-secret")).toBeNull();
    const testButton = screen.getByRole("button", {
      name: "测试 AI 归档连接",
    });
    fireEvent.click(testButton);
    await waitFor(() => expect(testAiConnection).toHaveBeenCalledOnce());
    expect(await screen.findByText(/AI 连接测试成功/)).toBeTruthy();
  });

  it("picks a root and reports AI organize results", async () => {
    const changed = vi.fn();
    render(<SettingsView onArchiveChanged={changed} />);
    fireEvent.click(await screen.findByRole("button", { name: "选择目录" }));
    expect(await screen.findByText("/tmp/archive")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "AI 归档分类/整理" }));
    expect(
      await screen.findByText(/新分类 2，复用 1，规则回退 0；移动 2/),
    ).toBeTruthy();
    expect(changed).toHaveBeenCalledOnce();
  });
});
