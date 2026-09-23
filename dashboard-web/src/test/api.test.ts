import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  createAiChatSession,
  deleteBackupToken,
  getAiPresets,
  getBackupStatus,
  getMessageResource,
  getSettings,
  invoke,
  openExternal,
  openMailAttachment,
  revealMailAttachment,
  saveBackupToken,
  sendAiChatMessage,
  startCloudBackup,
  updateSettings,
} from "@/lib/api";

beforeEach(() => {
  vi.stubGlobal("window", globalThis);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("pywebview bridge client", () => {
  it("invokes the allowlisted bridge without fetch", async () => {
    const bridge = vi
      .fn()
      .mockResolvedValue({ ok: true, data: { status: "ok" } });
    vi.stubGlobal("pywebview", { api: { invoke: bridge } });
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    await expect(invoke<{ status: string }>("health")).resolves.toEqual({
      status: "ok",
    });
    expect(bridge).toHaveBeenCalledWith("health", {});
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("surfaces sanitized bridge errors", async () => {
    vi.stubGlobal("pywebview", {
      api: {
        invoke: vi.fn().mockResolvedValue({
          ok: false,
          error: { code: "operation_failed", message: "操作失败" },
        }),
      },
    });
    await expect(invoke("overview")).rejects.toThrow("操作失败");
  });

  it("loads settings through the non-secret status action only", async () => {
    const bridge = vi.fn().mockResolvedValue({
      ok: true,
      data: { archive_root: "/tmp/archive" },
    });
    vi.stubGlobal("pywebview", { api: { invoke: bridge } });
    await getSettings();
    expect(bridge).toHaveBeenCalledOnce();
    expect(bridge).toHaveBeenCalledWith("settings_status", {});
  });

  it("sends only the settings update payload through the bridge", async () => {
    const bridge = vi.fn().mockResolvedValue({
      ok: true,
      data: { mail_account: "student-id" },
    });
    vi.stubGlobal("pywebview", { api: { invoke: bridge } });
    await updateSettings({ mail_account: "student-id" });
    expect(bridge).toHaveBeenCalledWith("settings_update", {
      mail_account: "student-id",
    });
  });

  it("uses the rich message resource and mail attachment bridge DTOs", async () => {
    const bridge = vi.fn().mockResolvedValue({
      ok: true,
      data: { data_url: "data:image/png;base64,a" },
    });
    vi.stubGlobal("pywebview", { api: { invoke: bridge } });

    await getMessageResource("announcement", "message-1", "resource-1");
    expect(bridge).toHaveBeenLastCalledWith("message_resource", {
      kind: "announcement",
      source_id: "message-1",
      resource_id: "resource-1",
    });
    await openMailAttachment("mail-1", "attachment-1");
    expect(bridge).toHaveBeenLastCalledWith("mail_attachment_open", {
      kind: "email",
      source_id: "mail-1",
      attachment_id: "attachment-1",
    });
    await revealMailAttachment("mail-1", "attachment-1");
    expect(bridge).toHaveBeenLastCalledWith("mail_attachment_reveal", {
      kind: "email",
      source_id: "mail-1",
      attachment_id: "attachment-1",
    });
  });

  it("sends the complete agent preset contract for new sessions and turns", async () => {
    const bridge = vi.fn().mockResolvedValue({
      ok: true,
      data: { default_preset_id: "general", items: [] },
    });
    vi.stubGlobal("pywebview", { api: { invoke: bridge } });

    await getAiPresets();
    expect(bridge).toHaveBeenLastCalledWith("ai_presets", {});

    await createAiChatSession("auto", "standard", "general");
    expect(bridge).toHaveBeenLastCalledWith("ai_chat_new", {
      model: "auto",
      thinking_depth: "standard",
      preset_id: "general",
    });

    await sendAiChatMessage(
      "session-1",
      "查询课程文件",
      "deepseek-chat",
      "deep",
      "review-planner",
    );
    expect(bridge).toHaveBeenLastCalledWith("ai_chat_send", {
      session_id: "session-1",
      content: "查询课程文件",
      model: "deepseek-chat",
      thinking_depth: "deep",
      preset_id: "review-planner",
    });
  });

  it("uses empty payloads for the fixed backup contract", async () => {
    const bridge = vi.fn().mockResolvedValue({
      ok: true,
      data: { status: "idle" },
    });
    vi.stubGlobal("pywebview", { api: { invoke: bridge } });

    await getBackupStatus();
    expect(bridge).toHaveBeenLastCalledWith("backup_status", {});
    await startCloudBackup();
    expect(bridge).toHaveBeenLastCalledWith("backup_start", {});
  });

  it("sends the token only to Keychain save and uses an empty delete payload", async () => {
    const bridge = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, data: { configured: true } })
      .mockResolvedValueOnce({ ok: true, data: { configured: false } });
    vi.stubGlobal("pywebview", { api: { invoke: bridge } });
    const token = ["private", "pan", "token"].join("-");

    const saved = await saveBackupToken(token);
    expect(saved).toEqual({ configured: true });
    expect(JSON.stringify(saved)).not.toContain(token);
    expect(bridge).toHaveBeenLastCalledWith("backup_token_save", { token });

    await expect(deleteBackupToken()).resolves.toEqual({ configured: false });
    expect(bridge).toHaveBeenLastCalledWith("backup_token_delete", {});
  });

  it("routes external URLs through the bridge", async () => {
    const bridge = vi
      .fn()
      .mockResolvedValue({ ok: true, data: { status: "opened" } });
    vi.stubGlobal("pywebview", { api: { invoke: bridge } });
    await openExternal("https://example.edu");
    expect(bridge).toHaveBeenCalledWith("open_external", {
      url: "https://example.edu",
    });
  });
});
