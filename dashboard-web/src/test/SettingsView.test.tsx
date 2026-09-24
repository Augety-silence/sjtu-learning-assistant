// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SettingsView } from "@/components/SettingsView";
import {
  deleteCredential,
  getSettings,
  openExternal,
  organizeArchive,
  pickArchiveRoot,
  saveCredential,
  testAiConnection,
  updateSettings,
} from "@/lib/api";
import type { SettingsStatus } from "@/lib/types";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@/test/render";

vi.mock("@/lib/api", () => ({
  getSettings: vi.fn(),
  openExternal: vi.fn(),
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
  ai_chat_send_shortcut: "enter",
  ai_reply_language: "auto",
  ai_attachment_context_budget: "balanced",
  ai_auto_open_activity: true,
  ai_code_line_numbers: false,
  theme_mode: "system",
};

describe("SettingsView", () => {
  afterEach(cleanup);
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getSettings).mockResolvedValue(status);
    vi.mocked(openExternal).mockResolvedValue({ status: "opened" });
    vi.mocked(updateSettings).mockImplementation(async (changes) => ({
      ...status,
      ...changes,
    }));
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

  it("shows all theme modes and applies a saved selection", async () => {
    const onThemeModeChange = vi.fn();
    render(<SettingsView onThemeModeChange={onThemeModeChange} />);

    const system = await screen.findByRole("radio", { name: /跟随系统/ });
    expect((system as HTMLInputElement).checked).toBe(true);
    fireEvent.click(screen.getByRole("radio", { name: /深色/ }));

    await waitFor(() =>
      expect(updateSettings).toHaveBeenCalledWith({ theme_mode: "dark" }),
    );
    expect(onThemeModeChange).toHaveBeenLastCalledWith("dark");
  });

  it("groups all connection configuration and opens an edit dialog", async () => {
    render(<SettingsView />);
    expect(await screen.findByText(status.archive_root)).toBeTruthy();
    expect(screen.getByText("Canvas")).toBeTruthy();
    expect(screen.getByText("交大邮箱")).toBeTruthy();
    expect(screen.getByText("交大云盘")).toBeTruthy();
    expect(screen.getByText("AI 模型")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "打开连接配置指南" }));
    expect(openExternal).toHaveBeenCalledWith(
      "https://bytedance.larkoffice.com/wiki/Iti5wHCN2iJ2PwksWoqcZjORn5f",
    );
    const buttons = screen.getAllByRole("button", { name: "修改配置" });
    fireEvent.click(buttons[0]);
    expect(screen.getByRole("dialog", { name: "Canvas 配置" })).toBeTruthy();
    expect(screen.getByLabelText("Canvas Access Token")).toBeTruthy();
  });

  it("traps focus, closes with Escape, and restores the edit trigger", async () => {
    render(<SettingsView />);
    const trigger = (
      await screen.findAllByRole("button", {
        name: "修改配置",
      })
    )[0];
    trigger.focus();
    fireEvent.click(trigger);

    const dialog = screen.getByRole("dialog", { name: "Canvas 配置" });
    expect(dialog.getAttribute("data-motion-surface")).toBe("modal");
    expect(dialog.parentElement?.getAttribute("data-motion-layer")).toBe(
      "modal",
    );
    expect(dialog.parentElement?.parentElement).toBe(document.body);
    expect(
      within(dialog).getByRole("button", { name: "取消" }).className,
    ).toContain("bg-transparent");
    const close = within(dialog).getByRole("button", {
      name: "关闭配置窗口",
    });
    await waitFor(() => expect(document.activeElement).toBe(close));

    fireEvent.change(within(dialog).getByLabelText("Canvas Access Token"), {
      target: { value: "test-token" },
    });
    const save = within(dialog).getByRole("button", { name: "保存配置" });
    save.focus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(document.activeElement).toBe(close);
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(save);

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "Canvas 配置" })).toBeNull();
    await waitFor(() => expect(document.activeElement).toBe(trigger));
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
