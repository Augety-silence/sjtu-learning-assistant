import type {
  BackupStartResult,
  BackupStatus,
  BackupTokenResult,
  MailAttachmentActionResult,
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

export function getMessageResource(
  kind: MessageKind,
  sourceId: string,
  resourceId: string,
) {
  return invoke<{ data_url: string }>("message_resource", {
    kind,
    source_id: sourceId,
    resource_id: resourceId,
  });
}

export function openMailAttachment(sourceId: string, attachmentId: string) {
  return invoke<MailAttachmentActionResult>("mail_attachment_open", {
    kind: "email",
    source_id: sourceId,
    attachment_id: attachmentId,
  });
}

export function revealMailAttachment(sourceId: string, attachmentId: string) {
  return invoke<MailAttachmentActionResult>("mail_attachment_reveal", {
    kind: "email",
    source_id: sourceId,
    attachment_id: attachmentId,
  });
}

export function markMessagesRead(payload: MessageMarkReadPayload) {
  return invoke<{ updated: number }>("message_mark_read", payload);
}

export function previewMaterial(sourceId: string) {
  return invoke<import("@/lib/types").MaterialPreview>("material_preview", {
    source_id: sourceId,
  });
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

export function saveCredential(
  kind: "canvas" | "mail" | "cloud" | "ai",
  value: string,
  account = "",
) {
  return invoke<import("@/lib/types").SettingsStatus>(
    "settings_credential_save",
    { kind, value, account },
  );
}

export function deleteCredential(
  kind: "canvas" | "mail" | "cloud" | "ai",
  account = "",
) {
  return invoke<import("@/lib/types").SettingsStatus>(
    "settings_credential_delete",
    { kind, account },
  );
}

export function getAiChatSessions() {
  return invoke<{ items: import("@/lib/types").AIChatSessionSummary[] }>(
    "ai_chat_sessions",
  );
}

export function getAiChatSession(sessionId: string) {
  return invoke<import("@/lib/types").AIChatSession>("ai_chat_session", {
    session_id: sessionId,
  });
}

export function createAiChatSession(
  model: import("@/lib/types").AIModel,
  thinkingDepth: import("@/lib/types").AIThinkingDepth,
) {
  return invoke<import("@/lib/types").AIChatSession>("ai_chat_new", {
    model,
    thinking_depth: thinkingDepth,
  });
}

export function sendAiChatMessage(
  sessionId: string,
  content: string,
  model: import("@/lib/types").AIModel,
  thinkingDepth: import("@/lib/types").AIThinkingDepth,
) {
  return invoke<import("@/lib/types").AIChatSendResult>("ai_chat_send", {
    session_id: sessionId,
    content,
    model,
    thinking_depth: thinkingDepth,
  });
}

export function deleteAiChatSession(sessionId: string) {
  return invoke<{ deleted: boolean }>("ai_chat_delete", {
    session_id: sessionId,
  });
}

export function sendAiChat(
  messages: Array<{ role: "user" | "assistant"; content: string }>,
) {
  return invoke<import("@/lib/types").AIChatResult>("ai_chat", { messages });
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

export function getAssignments(
  category: import("@/lib/types").AssignmentCategory,
) {
  return invoke<{
    category: string;
    items: import("@/lib/types").AssignmentItem[];
  }>("assignments_list", { category });
}

export function getAssignmentDetail(courseId: number, assignmentId: number) {
  return invoke<import("@/lib/types").AssignmentItem>("detail", {
    course_id: courseId,
    assignment_id: assignmentId,
  });
}

export function canSubmitAssignment(
  courseId: number,
  assignmentId: number,
  submissionType?: import("@/lib/types").NativeSubmissionType,
) {
  return invoke<{
    can_submit: boolean;
    requires_external_submission: boolean;
    submission_types: string[];
  }>("can_submit", {
    course_id: courseId,
    assignment_id: assignmentId,
    ...(submissionType ? { submission_type: submissionType } : {}),
  });
}

export function submitAssignmentText(
  courseId: number,
  assignmentId: number,
  text: string,
) {
  return invoke<import("@/lib/types").SubmissionResult>("submit_text", {
    course_id: courseId,
    assignment_id: assignmentId,
    text,
  });
}

export function submitAssignmentUrl(
  courseId: number,
  assignmentId: number,
  url: string,
) {
  return invoke<import("@/lib/types").SubmissionResult>("submit_url", {
    course_id: courseId,
    assignment_id: assignmentId,
    url,
  });
}

export function pickAssignmentLocalFile() {
  return invoke<import("@/lib/types").PickedLocalFile>("pick_local_file");
}

export function submitAssignmentLocalFile(
  courseId: number,
  assignmentId: number,
  path: string,
) {
  return invoke<import("@/lib/types").SubmissionResult>("submit_local_file", {
    course_id: courseId,
    assignment_id: assignmentId,
    path,
  });
}

function panPathSegments(remotePath: string): string[] {
  return remotePath ? remotePath.split("/") : [];
}

export function getPanFiles(remotePath = "", page = 1, pageSize = 50) {
  return invoke<import("@/lib/types").PanPage>("pan_list", {
    remote_path: panPathSegments(remotePath),
    page,
    page_size: pageSize,
  });
}

export function submitAssignmentCloudFile(
  courseId: number,
  assignmentId: number,
  remotePath: string,
) {
  return invoke<import("@/lib/types").SubmissionResult>("submit_cloud_file", {
    course_id: courseId,
    assignment_id: assignmentId,
    remote_path: panPathSegments(remotePath),
  });
}

export function openExternalAssignment(courseId: number, assignmentId: number) {
  return invoke<import("@/lib/types").SubmissionResult>(
    "open_external_assignment",
    {
      course_id: courseId,
      assignment_id: assignmentId,
    },
  );
}

export function getBackupStatus() {
  return invoke<BackupStatus>("backup_status");
}

export function startCloudBackup() {
  return invoke<BackupStartResult>("backup_start");
}

export function saveBackupToken(token: string) {
  return invoke<BackupTokenResult>("backup_token_save", { token });
}

export function deleteBackupToken() {
  return invoke<BackupTokenResult>("backup_token_delete");
}
