import type {
  MaterialMoveResult,
  MessageDetail,
  MessageFilter,
  MessageItem,
  MessageKind,
  MessageMarkReadPayload,
} from "@/lib/types";

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

export function getMessages(kind: MessageFilter) {
  return invoke<{ items: MessageItem[] }>("messages", { kind });
}

export function getMessageDetail(kind: MessageKind, sourceId: string) {
  return invoke<MessageDetail>("message_detail", {
    kind,
    source_id: sourceId,
  });
}

export function markMessagesRead(payload: MessageMarkReadPayload) {
  return invoke<{ updated: number }>("message_mark_read", payload);
}

export function moveMaterial(sourceId: string, targetNodeId: string) {
  return invoke<MaterialMoveResult>("material_move", {
    source_id: sourceId,
    target_node_id: targetNodeId,
  });
}

export function restoreMaterialAuto(sourceId: string) {
  return invoke<MaterialMoveResult>("material_restore_auto", {
    source_id: sourceId,
  });
}

export function getSettings() {
  return invoke<import("@/lib/types").SettingsStatus>("settings_status");
}

export function updateSettings(
  payload: Partial<
    Pick<
      import("@/lib/types").SettingsStatus,
      | "auto_download_current_term"
      | "organize_by_category"
      | "mail_account"
      | "ai_enabled"
      | "ai_base_url"
      | "ai_model"
    >
  >,
) {
  return invoke<import("@/lib/types").SettingsStatus>(
    "settings_update",
    payload,
  );
}

export function importAiConnection(configJson: string) {
  return invoke<import("@/lib/types").SettingsStatus>("settings_ai_import", {
    config_json: configJson,
  });
}

export function testAiConnection() {
  return invoke<{ ok: boolean; model: string; category: string }>(
    "settings_ai_test",
  );
}

export function pickArchiveRoot() {
  return invoke<{
    cancelled: boolean;
    settings: import("@/lib/types").SettingsStatus;
  }>("settings_pick_archive_root");
}

export function organizeArchive() {
  return invoke<import("@/lib/types").ArchiveActionResult>("archive_organize");
}

export function openExternal(url: string): Promise<{ status: string }> {
  return invoke("open_external", { url });
}
