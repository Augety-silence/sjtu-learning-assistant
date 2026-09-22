import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { invoke, openExternal, updateSettings } from "@/lib/api";

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

  it("sends only the settings update payload through the bridge", async () => {
    const bridge = vi.fn().mockResolvedValue({
      ok: true,
      data: { auto_download_current_term: false },
    });
    vi.stubGlobal("pywebview", { api: { invoke: bridge } });
    await updateSettings({ auto_download_current_term: false });
    expect(bridge).toHaveBeenCalledWith("settings_update", {
      auto_download_current_term: false,
    });
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
