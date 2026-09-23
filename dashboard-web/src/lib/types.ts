export type ViewName =
  | "overview"
  | "deadlines"
  | "messages"
  | "assignments"
  | "materials"
  | "backup"
  | "ai-chat"
  | "settings";

export type AIModel =
  | "auto"
  | "deepseek-chat"
  | "deepseek-reasoner"
  | "minimax"
  | "minimax-m2.7"
  | "qwen"
  | "qwen3.8-27b";
export type AIThinkingDepth = "quick" | "standard" | "deep";

export type AIToolRunPhase = "prefetch" | "model";
export type AIToolRunStatus = "ok" | "rejected";

export interface AIToolRun {
  tool_name: string;
  phase: AIToolRunPhase;
  status: AIToolRunStatus;
  arguments_summary: unknown;
  result_summary: unknown;
}

export interface AIAgentTrace {
  id: string;
  preset_id: string;
  status: string;
  steps: number;
  tool_runs: AIToolRun[];
  created_at?: string | null;
}

export interface AIAgentPreset {
  id: string;
  name: string;
  description: string;
  allowed_tools: string[];
  is_default: boolean;
}

export interface AIAgentPresetsResult {
  default_preset_id: string;
  items: AIAgentPreset[];
}

export interface AIChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  reasoning_content?: string | null;
  model?: string | null;
  trace_id?: string | null;
  tool_runs?: AIToolRun[];
  created_at?: string | null;
}

export interface AIChatSessionSummary {
  id: string;
  title: string;
  model: string;
  thinking_depth: AIThinkingDepth;
  preset_id: string;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface AIChatSession extends AIChatSessionSummary {
  messages: AIChatMessage[];
  traces: AIAgentTrace[];
}

export interface AIChatSendResult {
  session: AIChatSessionSummary;
  trace: AIAgentTrace;
  user_message: AIChatMessage;
  assistant_message: AIChatMessage;
}

export interface AIChatResult {
  reply: string;
  model: string;
}

export interface Deadline {
  source_id: string;
  title: string;
  course: string;
  due_at: string | null;
  submission_state: string;
  url: string | null;
}

export type MessageKind = "email" | "announcement" | "assignment";
export type MessageFilter = "all" | MessageKind;

export interface MessageItem {
  source_id: string;
  kind: MessageKind;
  title: string;
  source_label: string;
  occurred_at: string | null;
  is_unread: boolean;
  url: string | null;
}

export interface MessageResource {
  id: string;
  type: "image";
}

export interface MailMessageAttachment {
  id: string;
  name: string;
  type: string | null;
  size: number;
  is_inline: boolean;
  available: boolean;
  inline_data_url: string | null;
}

export interface CanvasMessageAttachment {
  name: string;
  type?: string | null;
  size: number | null;
  url: string;
}

export interface MessageDetail {
  title: string;
  source_label: string;
  occurred_at: string | null;
  body: string;
  body_html: string | null;
  format: "html" | "text";
  pending_body_sync: boolean;
  attachments: Array<MailMessageAttachment | CanvasMessageAttachment>;
  resources: MessageResource[];
  url: string | null;
  is_unread: boolean;
}

export interface MailAttachmentActionResult {
  source_id: string;
  attachment_id: string;
  status: "opened" | "revealed";
}

export interface MessageMarkReadPayload extends Record<string, unknown> {
  kind: MessageFilter;
  ids?: string[];
  all?: boolean;
}

export interface MaterialNode {
  id: string;
  kind: "root" | "term" | "course" | "category" | "folder" | "file";
  name: string;
  children?: MaterialNode[];
  source_id?: string;
  course_id?: string;
  category?: string;
  manual_override?: boolean;
  size?: number | null;
  updated_at?: string | null;
  download_status?: "pending" | "downloaded" | "failed" | "cloud_only";
  can_open?: boolean;
  can_preview?: boolean;
  local_path?: string | null;
  cloud_path?: string | null;
  cloud_status?: "cloud" | null;
  cloud_ready?: boolean;
}

export type MaterialPreview =
  | { kind: "text"; name: string; text: string }
  | { kind: "image"; name: string; data_url: string }
  | { kind: "pdf"; name: string; data_url: string };

export interface MaterialTree {
  root: MaterialNode;
  categories: Array<{ id: string; label: string }>;
  download_statuses: string[];
}

export interface OverviewData {
  courses: number;
  upcoming_deadlines: number;
  unread_emails: number;
  deadlines: Deadline[];
  messages: MessageItem[];
}

export interface SyncStatus {
  status: "idle" | "syncing";
  last_success_at: string | null;
  last_run_status: string | null;
  last_run_at: string | null;
}

export interface SettingsStatus {
  archive_root_ready: boolean;
  archive_root: string;
  auto_download_current_term: boolean;
  organize_by_category: boolean;
  mail_account: string;
  canvas_token_saved: boolean;
  mail_password_saved: boolean;
  cloud_token_saved: boolean;
  credential_status_error: string | null;
  ai_enabled: boolean;
  ai_base_url: string;
  ai_model: string;
  ai_key_saved: boolean;
}

export interface MaterialMoveResult {
  source_id: string;
  status: "saved" | "moved" | "unchanged";
  local_path: string | null;
}

export interface ArchiveActionResult {
  classified: number;
  reused: number;
  fallback: number;
  moved: number;
  unchanged: number;
  failed: number;
}

export type AssignmentCategory =
  | "today"
  | "upcoming"
  | "overdue"
  | "missing"
  | "unsubmitted"
  | "submitted"
  | "pending_review"
  | "graded";

export type NativeSubmissionType =
  | "online_text_entry"
  | "online_url"
  | "online_upload";

export interface AssignmentSubmission {
  id: number | string | null;
  workflow_state: string;
  submission_type: string | null;
  submitted_at: string | null;
  attempt: number | null;
  missing: boolean;
  late: boolean;
  score: number | null;
  grade: string | null;
  attachments: Array<{ id: number | string | null; name: string }>;
}

export interface AssignmentItem {
  course_id: string;
  course_name: string;
  id: string;
  name: string;
  description: string;
  due_at: string | null;
  unlock_at: string | null;
  lock_at: string | null;
  points_possible: number | null;
  html_url: string | null;
  submission_types: string[];
  submission: AssignmentSubmission | null;
  can_submit: boolean;
  requires_external_submission: boolean;
  categories: AssignmentCategory[];
}

export interface SubmissionResult {
  verified: boolean;
  status: string;
  message: string | null;
  submission_type: string | null;
  submission_id: number | string | null;
  submitted_at: string | null;
  attempt: number | null;
  attachments: Array<{ id: number | string | null; name: string }>;
  workflow_state: string | null;
}

export interface PickedLocalFile {
  cancelled: boolean;
  path?: string;
  name?: string;
  size?: number;
}

export interface PanItem {
  remote_path: string;
  name: string;
  is_directory: boolean;
  size: number | null;
  modified_at: string | null;
}

export interface PanPage {
  remote_path: string;
  page: number;
  page_size: number;
  total: number;
  has_more: boolean;
  items: PanItem[];
}

export interface BackupCounts {
  canvas: number;
  mail: number;
  ready: number;
  cloud_only: number;
  missing_local: number;
  total: number;
}

export interface BackupProgress {
  done: number;
  total: number;
  current_name: string | null;
}

export interface BackupFailure {
  source: string;
  name: string;
  remote_path: string;
  error: string;
}

export interface BackupResult {
  started_at?: string | null;
  finished_at?: string | null;
  uploaded?: number;
  skipped_existing?: number;
  skipped_missing_local?: number;
  local_removed?: number;
  failed?: number;
  failures?: BackupFailure[];
}

export interface BackupStatus {
  status: "idle" | "running" | "finished";
  available: boolean;
  availability_message: string | null;
  counts: BackupCounts;
  progress: BackupProgress | null;
  last_result: BackupResult | null;
}

export interface BackupStartResult {
  status: "started" | "already_running";
}

export interface BackupTokenResult {
  configured: boolean;
}
