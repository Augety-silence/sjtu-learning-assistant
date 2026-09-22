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
  organizeArchive,
  pickArchiveRoot,
  updateSettings,
} from "@/lib/api";
import type { SettingsStatus } from "@/lib/types";

vi.mock("@/lib/api", () => ({
  getSettings: vi.fn(),
  organizeArchive: vi.fn(),
  pickArchiveRoot: vi.fn(),
  updateSettings: vi.fn(),
}));

const status: SettingsStatus = {
  keychain_available: true,
  canvas_configured: true,
  mail_account_configured: false,
  mail_password_configured: false,
  archive_root_ready: true,
  archive_root: "/Users/test/Documents/SJTU Study",
  auto_download_current_term: true,
  organize_by_category: true,
  missing: ["mail_account", "mail_password"],
};

describe("SettingsView", () => {
  afterEach(cleanup);
  beforeEach(() => {
    vi.mocked(getSettings).mockResolvedValue(status);
    vi.mocked(updateSettings).mockResolvedValue(status);
    vi.mocked(pickArchiveRoot).mockResolvedValue({
      cancelled: false,
      settings: { ...status, archive_root: "/tmp/archive" },
    });
    vi.mocked(organizeArchive).mockResolvedValue({
      moved: 2,
      unchanged: 1,
      failed: 0,
    });
  });

  it("loads settings and sends a strict toggle payload", async () => {
    render(<SettingsView />);
    expect(await screen.findByText(status.archive_root)).toBeTruthy();
    const switches = screen.getAllByRole("switch");
    fireEvent.click(switches[0]);
    await waitFor(() =>
      expect(updateSettings).toHaveBeenCalledWith({
        auto_download_current_term: false,
      }),
    );
  });

  it("picks a root and reports organize results", async () => {
    const changed = vi.fn();
    render(<SettingsView onArchiveChanged={changed} />);
    fireEvent.click(await screen.findByRole("button", { name: "选择目录" }));
    expect(await screen.findByText("/tmp/archive")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "立即整理现有文件" }));
    expect(await screen.findByText(/移动 2，无需移动 1，失败 0/)).toBeTruthy();
    expect(changed).toHaveBeenCalledOnce();
  });
});
