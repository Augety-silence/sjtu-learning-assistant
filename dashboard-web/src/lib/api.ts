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

const CAPABILITY_UNAVAILABLE_CODES = new Set(["unknown_action", "not_allowed"]);

export class BridgeInvocationError extends Error {
  readonly action: string;
  readonly code: string;

  constructor(action: string, error?: BridgeError) {
    super(error?.message || "操作失败");
    this.name = "BridgeInvocationError";
    this.action = action;
    this.code = error?.code || "unknown_error";
  }
}

export class CapabilityUnavailableError extends BridgeInvocationError {
  constructor(action: string, error?: BridgeError) {
    super(action, error);
    this.name = "CapabilityUnavailableError";
  }
}

export function isCapabilityUnavailableError(
  error: unknown,
): error is CapabilityUnavailableError {
  return (
    error instanceof BridgeInvocationError &&
    CAPABILITY_UNAVAILABLE_CODES.has(error.code)
  );
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
  if (!response.ok) {
    const error = CAPABILITY_UNAVAILABLE_CODES.has(
      response.error?.code || "unknown_error",
    )
      ? new CapabilityUnavailableError(action, response.error)
      : new BridgeInvocationError(action, response.error);
    throw error;
  }
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
      | "ai_chat_send_shortcut"
      | "ai_reply_language"
      | "ai_attachment_context_budget"
      | "ai_auto_open_activity"
      | "ai_code_line_numbers"
      | "theme_mode"
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

export function getAiPresets() {
  return invoke<import("@/lib/types").AIAgentPresetsResult>("ai_presets");
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
  presetId: string,
) {
  return invoke<import("@/lib/types").AIChatSession>("ai_chat_new", {
    model,
    thinking_depth: thinkingDepth,
    preset_id: presetId,
  });
}

export function sendAiChatMessage(
  sessionId: string,
  content: string,
  model: import("@/lib/types").AIModel,
  thinkingDepth: import("@/lib/types").AIThinkingDepth,
  presetId: string,
  attachmentIds?: number[],
) {
  return invoke<import("@/lib/types").AIChatSendResult>("ai_chat_send", {
    session_id: sessionId,
    content,
    model,
    thinking_depth: thinkingDepth,
    preset_id: presetId,
    ...(attachmentIds ? { attachment_ids: attachmentIds } : {}),
  });
}

export function listAiAttachments(limit = 100) {
  return invoke<{
    items: import("@/lib/types").AIManagedAttachment[];
    count: number;
  }>("ai_attachment_list", { limit });
}

export function pickAiAttachment() {
  return invoke<{
    cancelled: boolean;
    attachment?: import("@/lib/types").AIManagedAttachment;
  }>("ai_attachment_pick");
}

export function ingestAiAttachment(path: string) {
  return invoke<import("@/lib/types").AIManagedAttachment>(
    "ai_attachment_ingest",
    { path },
  );
}

export function restoreAiAttachment(attachmentId: number) {
  return invoke<import("@/lib/types").AIManagedAttachment>(
    "ai_attachment_restore",
    { attachment_id: attachmentId },
  );
}

export function revealAiAttachment(attachmentId: number) {
  return invoke<{
    id: number;
    status: "revealed";
    attachment: import("@/lib/types").AIManagedAttachment;
  }>("ai_attachment_reveal", { attachment_id: attachmentId });
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

export function startCloudBackup(removeLocal: boolean) {
  return invoke<BackupStartResult>("backup_start", {
    remove_local: removeLocal,
  });
}

export function saveBackupToken(token: string) {
  return invoke<BackupTokenResult>("backup_token_save", { token });
}

export function deleteBackupToken() {
  return invoke<BackupTokenResult>("backup_token_delete");
}

export function getArchiveList(
  options: {
    limit?: number;
    offset?: number;
    query?: string;
    status?: string;
    sort?: string;
    cursor?: string;
  } = {},
) {
  return invoke<import("@/lib/types").ArchiveListResult>("archive_list", {
    limit: options.limit ?? 25,
    ...(options.offset !== undefined ? { offset: options.offset } : {}),
    ...(options.query ? { query: options.query } : {}),
    ...(options.status ? { status: options.status } : {}),
    ...(options.sort ? { sort: options.sort } : {}),
    ...(options.cursor ? { cursor: options.cursor } : {}),
  });
}

export function getArchiveDetail(entryId: string) {
  return invoke<import("@/lib/types").ArchiveEntry>("archive_detail", {
    entry_id: entryId,
  });
}

export function startArchive(idempotencyKey?: string) {
  return invoke<import("@/lib/types").ArchiveJob>("archive_start", {
    ...(idempotencyKey ? { idempotency_key: idempotencyKey } : {}),
  });
}

export function retryArchive(jobId: string) {
  return invoke<import("@/lib/types").ArchiveJob>("archive_retry", {
    job_id: jobId,
  });
}

export function getArchiveJobs(limit = 20, status?: string) {
  return invoke<{ items: import("@/lib/types").ArchiveJob[] }>("archive_jobs", {
    limit,
    ...(status ? { status } : {}),
  });
}

export function getArchiveJobEvents(jobId: string) {
  return invoke<{ items: import("@/lib/types").ArchiveJobEvent[] }>(
    "archive_job_events",
    { job_id: jobId },
  );
}

export function authorizeArchiveRoot() {
  return invoke<import("@/lib/types").ArchiveAuthorizedRoot>(
    "archive_authorize_root",
  );
}

export function planRestore(
  entryId: string,
  options: {
    versionId?: string;
    mode: "original" | "choose_location" | "save_as";
    authorizedRootId?: string;
  },
) {
  return invoke<import("@/lib/types").RestorePlan>("restore_plan", {
    entry_id: entryId,
    mode: options.mode,
    ...(options.versionId ? { version_id: options.versionId } : {}),
    ...(options.authorizedRootId
      ? { authorized_root_id: options.authorizedRootId }
      : {}),
  });
}

export function executeRestore(
  jobId: string,
  conflictPolicy: import("@/lib/types").ArchiveConflictPolicy,
  confirmCreateDirs = false,
) {
  return invoke<import("@/lib/types").ArchiveJob>("restore_execute", {
    job_id: jobId,
    conflict_policy: conflictPolicy,
    confirm_create_dirs: confirmCreateDirs,
  });
}

export interface RosterQuery {
  roles?: Array<"teacher" | "ta" | "student" | "observer" | "designer">;
  query?: string;
}

export function getCapabilities(courseId?: number) {
  return invoke<import("@/lib/types").AppCapabilities>("capabilities", {
    ...(courseId === undefined ? {} : { course_id: courseId }),
  });
}

export function getCalendar(year: number, month: number, courseIds?: number[]) {
  return invoke<import("@/lib/types").CalendarResult>("calendar", {
    year,
    month,
    ...(courseIds?.length ? { course_ids: courseIds } : {}),
  });
}

export function getGradebook(courseId: number) {
  return invoke<import("@/lib/types").GradebookResult>("gradebook", {
    course_id: courseId,
  });
}

export function exportGradebook(courseId: number) {
  return invoke<import("@/lib/types").ExportResult>("gradebook_export", {
    course_id: courseId,
  });
}

export function getRoster(courseId: number, query: RosterQuery = {}) {
  return invoke<import("@/lib/types").RosterResult>("roster", {
    course_id: courseId,
    ...(query.roles?.length ? { roles: query.roles } : {}),
    ...(query.query === undefined ? {} : { query: query.query }),
  });
}

export function exportRoster(courseId: number, userIds?: number[]) {
  return invoke<import("@/lib/types").ExportResult>("roster_export", {
    course_id: courseId,
    ...(userIds?.length ? { user_ids: userIds } : {}),
  });
}

export function revealAcademicExport(token: string) {
  return invoke<{ status: string; filename: string }>(
    "academic_export_reveal",
    {
      token,
    },
  );
}

export function getGrading(
  courseId: number,
  assignmentId: number,
  studentId?: number,
) {
  return invoke<
    | import("@/lib/types").GradingResult
    | import("@/lib/types").GradingSubmissionDto
  >("grading", {
    course_id: courseId,
    assignment_id: assignmentId,
    ...(studentId === undefined ? {} : { student_id: studentId }),
  });
}

export function updateGrading(
  courseId: number,
  assignmentId: number,
  studentId: number,
  update: { grade?: string | number | null; comment?: string },
) {
  return invoke<import("@/lib/types").GradingUpdateResult>("grading_update", {
    course_id: courseId,
    assignment_id: assignmentId,
    student_id: studentId,
    ...update,
  });
}

export function getCourseMedia(courseId: number, limit = 5000) {
  return invoke<import("@/lib/types").CourseMediaResult>("media", {
    course_id: courseId,
    limit: Math.min(5000, Math.max(1, Math.trunc(limit))),
  });
}

export function getMediaCapabilities() {
  return invoke<Record<string, import("@/lib/types").FeatureCapability>>(
    "media_capabilities",
  );
}

export function getMediaPreview(sourceId: string) {
  return invoke<import("@/lib/types").MediaPreviewResult>("media_preview", {
    source_id: sourceId,
  });
}

export function getVideoPlayback(sourceId: string) {
  return invoke<import("@/lib/types").MediaActionDescriptor>("video", {
    source_id: sourceId,
  });
}

export function getVideoSubtitles(sourceId: string) {
  return invoke<{
    status: "ready" | "empty" | "processing";
    message: string;
    content_type: string;
    vtt: string | null;
    cue_count: number;
  }>("video_subtitles", { source_id: sourceId });
}

export function createVideoSlidesPdf(sourceId: string) {
  return invoke<{
    status: string;
    filename: string;
    reveal_token: string;
    page_count: number;
    size: number;
  }>("video_slides_pdf", { source_id: sourceId });
}

export function createVideoScreenshotPdf(
  sourceId: string,
  intervalSeconds = 60,
) {
  return invoke<{
    status: string;
    filename: string;
    reveal_token: string;
    frame_count: number;
    size: number;
  }>("video_screenshot_pdf", {
    source_id: sourceId,
    interval_seconds: intervalSeconds,
  });
}

export interface DebugBundleExportResult {
  status: "created" | "cancelled";
  filename?: string;
  size?: number;
  entry_count?: number;
}

export function exportDebugBundle() {
  return invoke<DebugBundleExportResult>("debug_bundle_export");
}

export function checkForUpdates() {
  return invoke<import("@/lib/types").UpdateCheckResult>("update");
}

export function getMcpConfig() {
  return invoke<import("@/lib/types").McpConfig>("mcp");
}

export function startTranscriptBatch(courseId: number, sourceIds: string[]) {
  return invoke<import("@/lib/types").TranscriptBatch>(
    "transcript_batch_start",
    {
      course_id: courseId,
      source_ids: sourceIds,
    },
  );
}

export function getTranscriptBatch(batchId: string) {
  return invoke<import("@/lib/types").TranscriptBatch>("transcript_batch_get", {
    batch_id: batchId,
  });
}

export function getTranscriptJobs(courseId?: number) {
  return invoke<{ items: import("@/lib/types").TranscriptJob[] }>(
    "transcript_jobs",
    courseId ? { course_id: courseId } : {},
  );
}

export function retryTranscriptJob(jobId: string) {
  return invoke<import("@/lib/types").TranscriptBatch>("transcript_retry", {
    job_id: jobId,
  });
}

export function cancelTranscriptJob(jobId: string) {
  return invoke<import("@/lib/types").TranscriptBatch>("transcript_cancel", {
    job_id: jobId,
  });
}

export function getTranscriptArtifacts(jobId: string) {
  return invoke<{ items: import("@/lib/types").TranscriptArtifact[] }>(
    "transcript_artifacts",
    { job_id: jobId },
  );
}

export function readTranscriptArtifact(artifactId: string) {
  return invoke<import("@/lib/types").TranscriptArtifactContent>(
    "transcript_artifact_read",
    { artifact_id: artifactId },
  );
}

export function getTranscriptV2Artifacts(jobId: string) {
  return invoke<import("@/lib/types").Phase1ArtifactList>(
    "transcript_v2_artifacts",
    { job_id: jobId },
  );
}

export function readTranscriptV2Artifact(artifactId: string) {
  return invoke<import("@/lib/types").Phase1ArtifactRead>(
    "transcript_v2_artifact_read",
    { artifact_id: artifactId },
  );
}

export function revealTranscriptArtifact(artifactId: string) {
  return invoke<{ id: string; status: string }>("transcript_artifact_reveal", {
    artifact_id: artifactId,
  });
}
