// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SettingsView } from "@/components/SettingsView";
import {
  checkForUpdates,
  deleteCredential,
  exportDebugBundle,
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
  checkForUpdates: vi.fn(),
  getSettings: vi.fn(),
  openExternal: vi.fn(),
  organizeArchive: vi.fn(),
  pickArchiveRoot: vi.fn(),
  saveCredential: vi.fn(),
  deleteCredential: vi.fn(),
  exportDebugBundle: vi.fn(),
  testAiConnection: vi.fn(),
  updateSettings: vi.fn(),
}));

const status: SettingsStatus = {
  course_markdown_enabled: false,
  course_markdown_daily_calls: 100,
  archive_root_ready: true,
  archive_root: "/tmp/SJTU Study",
  auto_download_current_term: true,
  organize_by_category: true,
  mail_account: "",
  canvas_token_saved: false,
  mail_password_saved: false,
  cloud_token_saved: false,
  credential_storage_name: "Windows Credential Manager",
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
  it("设置读取完成后显现正文，使用较长的淡入时长", async () => {
    let finish: ((value: SettingsStatus) => void) | undefined;
    vi.mocked(getSettings).mockReturnValueOnce(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    const { container } = render(<SettingsView />);
    expect(screen.getByText("正在读取系统设置…")).toBeTruthy();
    finish?.(status);
    await screen.findAllByRole("button", { name: "修改配置" });
    const root = container.querySelector(".settings-page") as HTMLElement;
    expect(root.getAttribute("data-content-reveal")).toBe("ready");
    expect(root.style.getPropertyValue("--content-reveal-duration")).toBe(
      "420ms",
    );
  });

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
    vi.mocked(exportDebugBundle).mockResolvedValue({
      status: "created",
      filename: "debug.zip",
      size: 1,
      entry_count: 3,
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
      "https://my.feishu.cn/wiki/S1RywYx2gilgEtkOne0cTsqpnzd",
    );
    const buttons = screen.getAllByRole("button", { name: "修改配置" });
    fireEvent.click(buttons[0]);
    expect(screen.getByRole("dialog", { name: "Canvas 配置" })).toBeTruthy();
    expect(screen.getByLabelText("Canvas Access Token")).toBeTruthy();
  });

  it("disables configuration dialogs while a long-running task is active", async () => {
    vi.mocked(organizeArchive).mockReturnValueOnce(
      new Promise<never>(() => undefined),
    );
    render(<SettingsView />);
    await screen.findAllByRole("button", { name: "修改配置" });

    fireEvent.click(screen.getByRole("button", { name: "AI 归档分类/整理" }));

    await waitFor(() =>
      expect(
        screen
          .getAllByRole("button", { name: "修改配置" })
          .every((button) => (button as HTMLButtonElement).disabled),
      ).toBe(true),
    );
    expect(screen.queryByRole("dialog")).toBeNull();
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

  it.each([
    [0, "Canvas Access Token"],
    [1, "邮箱密码"],
    [2, "UserToken"],
    [3, "API Key"],
  ])("凭据 %s 默认隐藏，可切换且取消后清空并重新隐藏", async (index, label) => {
    render(<SettingsView />);
    const buttons = await screen.findAllByRole("button", { name: "修改配置" });
    fireEvent.click(buttons[index]);
    const input = screen.getByLabelText(label) as HTMLInputElement;
    expect(input.type).toBe("password");
    fireEvent.change(input, { target: { value: "test-only-value" } });
    const toggle = screen.getByRole("button", { name: "显示密码" });
    expect(toggle.getAttribute("type")).toBe("button");
    toggle.focus();
    expect(document.activeElement).toBe(toggle);
    fireEvent.click(toggle);
    expect(input.type).toBe("text");
    expect(input.value).toBe("test-only-value");
    expect(
      screen
        .getByRole("button", { name: "隐藏密码" })
        .getAttribute("aria-pressed"),
    ).toBe("true");
    expect(saveCredential).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "隐藏密码" }));
    expect(input.type).toBe("password");
    fireEvent.click(screen.getByRole("button", { name: "显示密码" }));
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    fireEvent.click(buttons[index]);
    const reopened = screen.getByLabelText(label) as HTMLInputElement;
    expect(reopened.type).toBe("password");
    expect(reopened.value).toBe("");
  });

  it("保存失败也重新隐藏密码，不改变凭据草稿", async () => {
    vi.mocked(saveCredential).mockRejectedValueOnce(new Error("保存失败"));
    render(<SettingsView />);
    const buttons = await screen.findAllByRole("button", { name: "修改配置" });
    fireEvent.click(buttons[0]);
    const input = screen.getByLabelText(
      "Canvas Access Token",
    ) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "test-only-value" } });
    fireEvent.click(screen.getByRole("button", { name: "显示密码" }));
    fireEvent.click(screen.getByRole("button", { name: "保存配置" }));
    await screen.findByText("保存失败");
    expect(input.type).toBe("password");
    expect(input.value).toBe("test-only-value");
  });

  it("更新检查失败时保留错误和官方发布页入口，重试清除旧状态", async () => {
    vi.mocked(checkForUpdates).mockRejectedValueOnce(
      new Error("GitHub 更新检查超时"),
    );
    render(<SettingsView />);
    fireEvent.click(await screen.findByRole("button", { name: "检查更新" }));
    expect(
      await screen.findByText("未能确认最新版本：GitHub 更新检查超时"),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "查看发布页" }));
    expect(openExternal).toHaveBeenCalledWith(
      "https://github.com/Augety-silence/sjtu-learning-assistant/releases/latest",
    );
    vi.mocked(checkForUpdates).mockResolvedValueOnce({
      current_version: "1.8.9",
      latest_version: "1.8.9",
      update_available: false,
      release: {
        page_url:
          "https://github.com/Augety-silence/sjtu-learning-assistant/releases/tag/v1.8.9",
        published_at: null,
        notes: "",
      },
      download_plan: null,
      automatic_install: false,
    });
    fireEvent.click(screen.getByRole("button", { name: "检查更新" }));
    expect(await screen.findByText("当前 1.8.9 · 最新 1.8.9")).toBeTruthy();
    expect(screen.queryByText(/未能确认最新版本/)).toBeNull();
  });

  it("exports a debug bundle with loading, success, cancel, and failure states", async () => {
    let finishExport:
      | ((value: Awaited<ReturnType<typeof exportDebugBundle>>) => void)
      | undefined;
    vi.mocked(exportDebugBundle).mockReturnValueOnce(
      new Promise<Awaited<ReturnType<typeof exportDebugBundle>>>((resolve) => {
        finishExport = resolve;
      }),
    );
    render(<SettingsView />);

    const button = await screen.findByRole("button", {
      name: "打包运行日志",
    });
    fireEvent.click(button);
    expect(await screen.findByText("正在打包已脱敏的运行日志…")).toBeTruthy();
    expect(button.getAttribute("aria-busy")).toBe("true");
    finishExport?.({
      status: "created",
      filename: "sjtu-learning-assistant-debug-20260930_160000.zip",
      size: 321,
      entry_count: 4,
    });
    expect(
      await screen.findByText(
        "调试包已保存：sjtu-learning-assistant-debug-20260930_160000.zip",
      ),
    ).toBeTruthy();
    await waitFor(() => expect(button.getAttribute("aria-busy")).toBeNull());

    vi.mocked(exportDebugBundle).mockResolvedValueOnce({ status: "cancelled" });
    fireEvent.click(button);
    expect(await screen.findByText("已取消保存调试包。")).toBeTruthy();

    vi.mocked(exportDebugBundle).mockRejectedValueOnce(
      new Error("调试包生成失败"),
    );
    fireEvent.click(button);
    expect(await screen.findByText("调试包生成失败")).toBeTruthy();
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
