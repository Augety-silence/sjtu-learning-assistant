export interface BridgeError {
  code: string;
  message: string;
}

export interface BridgeResponse<T> {
  ok: boolean;
  data?: T;
  error?: BridgeError;
}

declare global {
  interface Window {
    pywebview?: {
      api?: {
        invoke: <T>(
          action: string,
          payload?: Record<string, unknown>,
        ) => Promise<BridgeResponse<T>>;
      };
    };
  }
}

async function bridgeApi() {
  if (window.pywebview?.api) return window.pywebview.api;
  await new Promise<void>((resolve, reject) => {
    const timeout = window.setTimeout(
      () => reject(new Error("桌面 Bridge 尚未就绪，请重新打开应用。")),
      5000,
    );
    window.addEventListener(
      "pywebviewready",
      () => {
        window.clearTimeout(timeout);
        resolve();
      },
      { once: true },
    );
  });
  if (!window.pywebview?.api) throw new Error("桌面 Bridge 不可用。");
  return window.pywebview.api;
}

export async function invoke<T>(
  action: string,
  payload: Record<string, unknown> = {},
): Promise<T> {
  const response = await (await bridgeApi()).invoke<T>(action, payload);
  if (!response.ok) throw new Error(response.error?.message || "操作失败");
  return response.data as T;
}

export function getSettings() {
  return invoke<import("@/lib/types").SettingsStatus>("settings_status");
}

export function updateSettings(
  payload: Partial<
    Pick<
      import("@/lib/types").SettingsStatus,
      "auto_download_current_term" | "organize_by_category"
    >
  >,
) {
  return invoke<import("@/lib/types").SettingsStatus>(
    "settings_update",
    payload,
  );
}

export function pickArchiveRoot() {
  return invoke<{
    cancelled: boolean;
    settings: import("@/lib/types").SettingsStatus;
  }>("settings_pick_archive_root");
}

export function organizeArchive() {
  return invoke<{ moved: number; unchanged: number; failed: number }>(
    "archive_organize",
  );
}

export function openExternal(url: string): Promise<{ status: string }> {
  return invoke("open_external", { url });
}
