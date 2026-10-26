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

function redactLocalPaths(text: string) {
  // Windows: drive paths with single or repr-doubled separators (C:\\Users\\x),
  // forward slashes and \\?\ prefixes; `\Users\x` / `\home\x` segments inside
  // UNC/WSL paths (also when followed by `/`). The UNC rule then collapses the
  // `\\server\share` (or `\\?\UNC\server\share`) root, which may itself name the
  // user (\\fs01\alice$). `\\?\C:` and `\\.\` device prefixes are left alone.
  // `m` lets `$` stop at CR/LF. The unanchored `\Users` run is bounded
  // ({1,8}) so a long backslash run cannot trigger quadratic backtracking.
  return text
    .replace(/\b[A-Za-z]:[\\/]+Users[\\/]+[^\\/\r\n]+(?=[\\/]|$)/gim, "~")
    .replace(/\\{1,8}(?:Users|home)\\{1,8}[^\\/\r\n]+(?=[\\/]|$)/gim, "~")
    .replace(
      /(^|[\s'"(\[=,，：（“‘「【])\\{2,}(?:[?.]\\+UNC\\+)?(?![?.]\\)[^\\/\s'"<>|?*:)\]）】”’」，]+\\+[^\\/\s'"<>|?*:)\]）】”’」，]+(?=[\\/\s'"<>|?*:)\]）】”’」，]|$)/gim,
      "$1~",
    )
    .replace(/\/(?:Users|home)\/[^/\r\n]+(?=\/|$)/gm, "~");
}

export class BridgeInvocationError extends Error {
  readonly action: string;
  readonly code: string;

  constructor(action: string, error?: BridgeError) {
    super(redactLocalPaths(error?.message || "操作失败"));
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

type BridgeApi = NonNullable<NonNullable<Window["pywebview"]>["api"]>;

const BRIDGE_READY_TIMEOUT_MS = 15_000;
let bridgeReady: Promise<BridgeApi> | null = null;

function readyBridge(hostWindow: Window = window): BridgeApi | null {
  const api = hostWindow.pywebview?.api;
  return api && typeof api.invoke === "function" ? api : null;
}

function waitForBridge() {
  const hostWindow = window;
  return new Promise<BridgeApi>((resolve, reject) => {
    const finish = () => {
      const api = readyBridge(hostWindow);
      if (!api) return;
      hostWindow.clearTimeout(timeout);
      hostWindow.clearInterval(poll);
      hostWindow.removeEventListener("pywebviewready", finish);
      resolve(api);
    };
    const timeout = hostWindow.setTimeout(() => {
      hostWindow.clearInterval(poll);
      hostWindow.removeEventListener("pywebviewready", finish);
      bridgeReady = null;
      // 仍是 Error 子类且 message 不变；补 code/category，调用方可区分"未就绪"与其他失败。
      reject(
        Object.assign(
          new BridgeInvocationError("bridge_ready", {
            code: "bridge_unavailable",
            message:
              "桌面 Bridge 尚未就绪，请稍后重试；若持续出现请重新打开应用。",
          }),
          { category: "bridge_unavailable", retryable: true },
        ),
      );
    }, BRIDGE_READY_TIMEOUT_MS);
    const poll = hostWindow.setInterval(finish, 100);
    hostWindow.addEventListener("pywebviewready", finish);
  });
}

async function bridgeApi(): Promise<BridgeApi> {
  const ready = readyBridge();
  if (ready) return ready;
  const pending = bridgeReady ?? waitForBridge();
  bridgeReady = pending;
  return pending;
}

export async function invoke<T>(
  action: string,
  payload: Record<string, unknown> = {},
): Promise<T> {
  const api = await bridgeApi();
  let response: BridgeResponse<T>;
  try {
    response = await api.invoke<T>(action, payload);
  } catch {
    throw new BridgeInvocationError(action, {
      code: "bridge_failed",
      message: "桌面 Bridge 通信失败，请稍后重试。",
    });
  }
  if (!response || typeof response !== "object") {
    throw new BridgeInvocationError(action);
  }
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

function invokeWithSignal<T>(
  action: string,
  payload: Record<string, unknown>,
  signal?: AbortSignal,
) {
  if (!signal) return invoke<T>(action, payload);
  if (signal.aborted)
    return Promise.reject(new DOMException("读取已取消", "AbortError"));
  return new Promise<T>((resolve, reject) => {
    const abort = () => reject(new DOMException("读取已取消", "AbortError"));
    signal.addEventListener("abort", abort, { once: true });
    void invoke<T>(action, payload)
      .then(resolve, reject)
      .finally(() => {
        signal.removeEventListener("abort", abort);
      });
  });
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

export const AI_CHAT_STREAM_EVENT = "sjtu-ai-chat-stream";

function activeAIPageContext() {
  if (typeof window === "undefined") return undefined;
  const storage = window.localStorage;
  if (!storage) return undefined;
  const raw = storage.getItem("ai-chat-active-context");
  if (!raw) return undefined;
  try {
    const parsed: unknown = JSON.parse(raw);
    return parsed && typeof parsed === "object"
      ? (parsed as import("@/lib/types").AIPageContext)
      : undefined;
  } catch {
    return undefined;
  }
}

function emitAIStream(detail: Record<string, unknown>) {
  if (
    typeof window === "undefined" ||
    typeof window.dispatchEvent !== "function" ||
    typeof CustomEvent === "undefined"
  )
    return;
  window.dispatchEvent(new CustomEvent(AI_CHAT_STREAM_EVENT, { detail }));
}

function waitForTaskPoll(delay = 120) {
  return new Promise<void>((resolve) => window.setTimeout(resolve, delay));
}

// 只对明确的瞬时故障（Bridge 通信抖动、SQLite 暂时锁定）做有界重试；
// 任务不存在、能力不可用等永久错误仍立即停止。
const TRANSIENT_TASK_POLL_CODES = new Set([
  "bridge_failed",
  "database_unavailable",
]);
const MAX_TRANSIENT_TASK_POLL_FAILURES = 2;

function isTransientTaskPollError(error: unknown) {
  return (
    error instanceof BridgeInvocationError &&
    TRANSIENT_TASK_POLL_CODES.has(error.code)
  );
}

// 用户主动停止/任务被取消：仍是 Error 子类且 message 不变，但带 name/category，
// 调用方可据此与真实失败区分（例如不弹错误提示）。
function aiTaskCancelledError(
  message = "已请求停止本次任务。正在进行的模型或工具请求返回后才会停止。",
) {
  return Object.assign(new Error(message), {
    name: "AbortError",
    category: "cancelled",
    retryable: true,
  });
}

function aiTaskDetachedError() {
  return Object.assign(new Error("AI 任务轮询已转交给新的页面实例。"), {
    name: "AbortError",
    category: "detached",
    retryable: false,
  });
}

async function requestAiTaskCancel(taskId: string) {
  let transientFailures = 0;
  for (;;) {
    try {
      return await invoke<import("@/lib/types").AITaskSnapshot>(
        "ai_task_cancel",
        { task_id: taskId },
      );
    } catch (error) {
      if (
        isTransientTaskPollError(error) &&
        transientFailures < MAX_TRANSIENT_TASK_POLL_FAILURES
      ) {
        transientFailures += 1;
        await waitForTaskPoll(250 * transientFailures);
        continue;
      }
      throw error;
    }
  }
}

function emitAIStreamEvents(
  snapshot: import("@/lib/types").AITaskSnapshot,
  taskId: string,
  sessionId: string,
) {
  for (const event of snapshot.events) {
    if (event.kind === "content_delta" || event.kind === "reasoning_delta") {
      emitAIStream({
        type: event.kind,
        task_id: taskId,
        session_id: sessionId,
        delta: event.payload.delta,
      });
    }
    if (event.kind === "tool") {
      emitAIStream({
        type: "tool",
        task_id: taskId,
        session_id: sessionId,
        run: event.payload,
      });
    }
  }
}

async function pollAiChatTask(
  sessionId: string,
  initialSnapshot: import("@/lib/types").AITaskSnapshot,
  options: {
    signal?: AbortSignal;
    pollSignal?: AbortSignal;
  },
): Promise<import("@/lib/types").AIChatSendResult> {
  let snapshot = initialSnapshot;
  const taskId = snapshot.task.id;
  let sequence = snapshot.next_sequence;
  emitAIStream({ type: "start", task_id: taskId, session_id: sessionId });
  emitAIStreamEvents(snapshot, taskId, sessionId);
  // 空闲超时：只要任务仍在产出事件或状态变化就续期，避免长回答在流式输出中被误取消；
  // 另设绝对上限防止任务永久挂起。
  const idleTimeoutMs = 180_000;
  const hardDeadline = Date.now() + 600_000;
  let deadline = Math.min(Date.now() + idleTimeoutMs, hardDeadline);
  let lastStatus = snapshot.task.status;
  let transientPollFailures = 0;
  let cancelRequested = Boolean(snapshot.task.cancel_requested);
  // 先处理最近一次快照的终态，再判断截止：截止前最后一次轮询若已 completed/failed，
  // 不能被当作超时丢弃（否则已入库的回答会被前端回滚、用户重发造成重复）。
  for (;;) {
    if (options.pollSignal?.aborted) throw aiTaskDetachedError();
    if (snapshot.task.status === "completed") {
      const result = snapshot.task.result?.chat_result;
      if (!result) throw new Error("AI 任务完成但没有返回对话结果。");
      emitAIStream({
        type: "complete",
        task_id: taskId,
        session_id: sessionId,
      });
      return result;
    }
    if (snapshot.task.status === "failed") {
      const failure = snapshot.task.result?.failure;
      const error = new Error(
        redactLocalPaths(
          failure?.message || snapshot.task.error || "AI 任务执行失败。",
        ),
      ) as Error & { category?: string; retryable?: boolean };
      error.category = failure?.category;
      error.retryable = failure?.retryable ?? true;
      throw error;
    }
    if (snapshot.task.status === "cancelled") {
      emitAIStream({
        type: "cancelled",
        task_id: taskId,
        session_id: sessionId,
      });
      throw aiTaskCancelledError("AI 任务已取消。");
    }
    // 取消请求必须由后端确认；RPC 失败时保留真实错误，不能误报“已取消”。
    if (options.signal?.aborted && !cancelRequested) {
      snapshot = await requestAiTaskCancel(taskId);
      cancelRequested = Boolean(snapshot.task.cancel_requested);
      if (
        !cancelRequested &&
        snapshot.task.status !== "completed" &&
        snapshot.task.status !== "failed" &&
        snapshot.task.status !== "cancelled"
      ) {
        throw new Error("停止请求未被后端确认，请稍后重试。");
      }
      continue;
    }
    if (Date.now() >= deadline) break;
    await waitForTaskPoll();
    if (options.pollSignal?.aborted) throw aiTaskDetachedError();
    try {
      snapshot = await invoke<import("@/lib/types").AITaskSnapshot>(
        "ai_task_status",
        { task_id: taskId, after_sequence: sequence },
      );
      transientPollFailures = 0;
    } catch (error) {
      if (
        isTransientTaskPollError(error) &&
        transientPollFailures < MAX_TRANSIENT_TASK_POLL_FAILURES
      ) {
        // 游标 sequence 未推进，重试不会丢事件；退避 250ms / 500ms。
        transientPollFailures += 1;
        await waitForTaskPoll(250 * transientPollFailures);
        continue;
      }
      await requestAiTaskCancel(taskId).catch(() => undefined);
      throw error;
    }
    if (options.pollSignal?.aborted) throw aiTaskDetachedError();
    sequence = snapshot.next_sequence;
    if (snapshot.events.length > 0 || snapshot.task.status !== lastStatus) {
      deadline = Math.min(Date.now() + idleTimeoutMs, hardDeadline);
      lastStatus = snapshot.task.status;
    }
    emitAIStreamEvents(snapshot, taskId, sessionId);
  }
  try {
    const cancelled = await requestAiTaskCancel(taskId);
    if (["completed", "failed", "cancelled"].includes(cancelled.task.status)) {
      return await pollAiChatTask(sessionId, cancelled, options);
    }
  } catch (error) {
    throw new Error(
      `AI 任务等待超时，停止请求失败：${error instanceof Error ? error.message : "未知错误"}`,
    );
  }
  throw new Error("AI 任务等待超时，已请求停止。");
}

export async function sendAiChatMessage(
  sessionId: string,
  content: string,
  model: import("@/lib/types").AIModel,
  thinkingDepth: import("@/lib/types").AIThinkingDepth,
  presetId: string,
  attachmentIds?: number[],
  options: {
    signal?: AbortSignal;
    pollSignal?: AbortSignal;
    onTaskStart?: (taskId: string) => void;
  } = {},
) {
  // 已中止的 signal 不再启动后端任务（避免白白创建再取消）。
  if (options.signal?.aborted) throw aiTaskCancelledError();
  const pageContext = activeAIPageContext();
  const snapshot = await invoke<import("@/lib/types").AITaskSnapshot>(
    "ai_chat_stream_start",
    {
      session_id: sessionId,
      content,
      model,
      thinking_depth: thinkingDepth,
      preset_id: presetId,
      ...(attachmentIds ? { attachment_ids: attachmentIds } : {}),
      ...(pageContext ? { page_context: pageContext } : {}),
    },
  );
  const taskId = snapshot.task.id;
  options.onTaskStart?.(taskId);
  return pollAiChatTask(sessionId, snapshot, options);
}

export async function resumeAiChatMessageTask(
  sessionId: string,
  taskId: string,
  options: { signal?: AbortSignal; pollSignal?: AbortSignal } = {},
) {
  const snapshot = await invoke<import("@/lib/types").AITaskSnapshot>(
    "ai_task_status",
    { task_id: taskId, after_sequence: 0 },
  );
  return pollAiChatTask(sessionId, snapshot, options);
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

export function registerAssignmentLocalFile(path: string) {
  return invoke<import("@/lib/types").PickedLocalFile>("register_local_file", {
    path,
  });
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

export function getCalendar(year: number, month: number, courseIds?: number[]) {
  return invoke<import("@/lib/types").CalendarResult>("calendar", {
    year,
    month,
    ...(courseIds?.length ? { course_ids: courseIds } : {}),
  });
}

export function getTimetableStatus() {
  return invoke<import("@/lib/types").TimetableStatus>("timetable_status");
}

export function getTimetableSchedule(startAt: string, endAt: string) {
  return invoke<import("@/lib/types").TimetableSchedule>("timetable_schedule", {
    startAt,
    endAt,
  });
}

export function previewTimetableFile() {
  return invoke<
    import("@/lib/types").TimetableImportPreview | { cancelled: true }
  >("timetable_preview_local_file");
}

export function previewTimetableSample() {
  return invoke<import("@/lib/types").TimetableImportPreview>(
    "timetable_load_bundled_sample",
  );
}

export function commitTimetableImport(previewId: string) {
  return invoke<import("@/lib/types").TimetableImportCommit>(
    "timetable_commit_preview",
    { previewId },
  );
}

export function importTimetableIcs() {
  return invoke<
    | (import("@/lib/types").TimetableImportCommit & {
        format: string;
        warnings: string[];
      })
    | { cancelled: true }
  >("timetable_import_ics");
}

export function getGradebook(courseId: number) {
  return invoke<import("@/lib/types").GradebookResult>("gradebook", {
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

export function getVideoSlides(sourceId: string) {
  return invoke<{
    status: "ready" | "empty" | "processing";
    message: string;
    pages: Array<{ index: number; time_ms: number | null }>;
  }>("video_slides_metadata", { source_id: sourceId });
}

export function getVideoSlideImage(sourceId: string, pageIndex: number) {
  return invoke<{ data_url: string }>("video_slide_image", {
    source_id: sourceId,
    page_index: pageIndex,
  });
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

export function startTranscriptBatch(courseId: number, sourceIds: string[]) {
  return invoke<import("@/lib/types").TranscriptBatch>(
    "transcript_batch_start",
    {
      course_id: courseId,
      source_ids: sourceIds,
    },
  );
}

export function prepareTranscriptPlayback(
  courseId: number,
  sourceId: string,
  positionSeconds = 0,
  playbackRate = 1,
) {
  return invoke<import("@/lib/types").TranscriptBatch>(
    "transcript_playback_prepare",
    {
      course_id: courseId,
      source_id: sourceId,
      position_seconds: positionSeconds,
      playback_rate: playbackRate,
    },
  );
}

export function updateTranscriptPlayback(
  courseId: number,
  sourceId: string,
  positionSeconds: number,
  playbackRate = 1,
  seq = 0,
) {
  return invoke<{ source_id: string; seq?: number; stale?: boolean }>(
    "transcript_playback_update",
    {
      course_id: courseId,
      source_id: sourceId,
      position_seconds: positionSeconds,
      playback_rate: playbackRate,
      seq,
    },
  );
}

export function nextTranscriptPlaybackSequence(previous = 0, now = Date.now()) {
  return Math.max(now, previous + 1);
}

export interface TranscriptLearningSegmentsDto {
  status: "ready" | "processing" | "empty";
  message?: string;
  source_version?: string;
  is_processing?: boolean;
  /** C3 contract; optional so older responses use the legacy flags. */
  processing_state?: import("@/lib/types").TranscriptProcessingState | null;
  processing_done?: boolean;
  auto_retryable?: boolean;
  stage?: string | null;
  error_code?: string | null;
  error_message?: string | null;
  retryable?: boolean;
  retry_after?: string | null;
  updated_at?: string | null;
  source_raw_sha256?: string | null;
  learning_revision?: string;
  completed_segments?: number;
  failed_segments?: number;
  available_ranges?: [number, number][];
  segments: {
    id: string;
    start_ms: number;
    end_ms: number;
    title: string;
    content: string;
    kind?: string;
    source?: string;
    provisional?: boolean;
  }[];
}

export function getTranscriptLearningSegments(
  courseId: number,
  sourceId: string,
  signal?: AbortSignal,
) {
  return invokeWithSignal<TranscriptLearningSegmentsDto>(
    "transcript_learning_segments",
    {
      course_id: courseId,
      source_id: sourceId,
    },
    signal,
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
    courseId === undefined ? {} : { course_id: courseId },
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

export function pickKnowledgeFolder() {
  return invoke<{ cancelled: boolean; path?: string; name?: string }>(
    "knowledge_compiler_pick_folder",
  );
}

export function inspectKnowledgeSource(sourceRoot: string) {
  return invoke<import("@/lib/types").KnowledgeCompilerInspection>(
    "knowledge_compiler_inspect",
    { source_root: sourceRoot },
  );
}

export function startKnowledgeCompiler(
  sourceRoot: string,
  targetRoot: string,
  mode: import("@/lib/types").KnowledgeCompilerMode,
) {
  return invoke<{
    status: "started" | "already_running";
    task: import("@/lib/types").KnowledgeCompilerTask;
  }>("knowledge_compiler_start", {
    source_root: sourceRoot,
    target_root: targetRoot,
    mode,
  });
}

export function getKnowledgeCompilerStatus(targetRoot?: string) {
  return invoke<import("@/lib/types").KnowledgeCompilerTask>(
    "knowledge_compiler_status",
    targetRoot === undefined ? {} : { target_root: targetRoot },
  );
}

export function cancelKnowledgeCompiler() {
  return invoke<import("@/lib/types").KnowledgeCompilerTask>(
    "knowledge_compiler_cancel",
  );
}
export function listLocalProjects() {
  return invoke<import("@/lib/types").LocalProjectList>("local_projects_list");
}

export function addLocalProject(sourceRoot: string, targetRoot: string) {
  return invoke<import("@/lib/types").LocalProject>("local_projects_add", {
    source_root: sourceRoot,
    target_root: targetRoot,
  });
}

export function refreshLocalProject(projectId: string) {
  return invoke<import("@/lib/types").LocalProject>("local_projects_refresh", {
    project_id: projectId,
  });
}

export function removeLocalProject(projectId: string) {
  return invoke<{ project_id: string; removed: boolean }>(
    "local_projects_remove",
    { project_id: projectId },
  );
}
