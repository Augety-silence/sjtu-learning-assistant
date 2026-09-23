// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SettingsView } from "@/components/SettingsView";
import {
  deleteCredential,
  getSettings,
  organizeArchive,
  pickArchiveRoot,
  saveCredential,
  testAiConnection,
  updateSettings,
} from "@/lib/api";
import type { SettingsStatus } from "@/lib/types";
import { cleanup, fireEvent, render, screen, waitFor } from "@/test/render";

vi.mock("@/lib/api", () => ({
  getSettings: vi.fn(),
  organizeArchive: vi.fn(),
  pickArchiveRoot: vi.fn(),
  saveCredential: vi.fn(),
  deleteCredential: vi.fn(),
  testAiConnection: vi.fn(),
  updateSettings: vi.fn(),
}));

const status: SettingsStatus = {
  archive_root_ready: true,
  archive_root: "/tmp/SJTU Study",
  auto_download_current_term: true,
  organize_by_category: true,
  mail_account: "",
  canvas_token_saved: false,
  mail_password_saved: false,
  cloud_token_saved: false,
  credential_status_error: null,
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
    vi.mocked(saveCredential).mockResolvedValue(status);
    vi.mocked(deleteCredential).mockResolvedValue(status);
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

  it("groups all connection configuration and opens an edit dialog", async () => {
    render(<SettingsView />);
    expect(await screen.findByText(status.archive_root)).toBeTruthy();
    expect(screen.getByText("Canvas")).toBeTruthy();
    expect(screen.getByText("交大邮箱")).toBeTruthy();
    expect(screen.getByText("交大云盘")).toBeTruthy();
    expect(screen.getByText("AI 模型")).toBeTruthy();
    const buttons = screen.getAllByRole("button", { name: "修改配置" });
    fireEvent.click(buttons[0]);
    expect(screen.getByRole("dialog", { name: "Canvas 配置" })).toBeTruthy();
    expect(screen.getByLabelText("Canvas Access Token")).toBeTruthy();
  });

  it("saves a mail account and password without echoing existing secrets", async () => {
    render(<SettingsView />);
    const buttons = await screen.findAllByRole("button", { name: "修改配置" });
    fireEvent.click(buttons[1]);
    fireEvent.change(screen.getByLabelText("邮箱账号"), {
      target: { value: "student@sjtu.edu.cn" },
    });
    fireEvent.change(screen.getByLabelText("邮箱密码"), {
      target: { value: "private-password" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存配置" }));
    await waitFor(() =>
      expect(updateSettings).toHaveBeenCalledWith({
        mail_account: "student@sjtu.edu.cn",
      }),
    );
    expect(saveCredential).toHaveBeenCalledWith(
      "mail",
      "private-password",
      "student@sjtu.edu.cn",
    );
  });

  it("keeps archive controls available", async () => {
    const changed = vi.fn();
    render(<SettingsView onArchiveChanged={changed} />);
    fireEvent.click(await screen.findByRole("button", { name: "选择目录" }));
    expect(await screen.findByText("/tmp/archive")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "AI 归档分类/整理" }));
    await waitFor(() => expect(organizeArchive).toHaveBeenCalledOnce());
    expect(changed).toHaveBeenCalledOnce();
  });
});
