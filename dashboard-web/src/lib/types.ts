export type ViewName =
  | "overview"
  | "calendar"
  | "messages"
  | "assignments"
  | "materials"
  | "grades"
  | "roster"
  | "grading"
  | "videos"
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
export type AIChatSendShortcut = "enter" | "cmd_enter";
export type AIReplyLanguage = "auto" | "zh" | "en";
export type AIAttachmentContextBudget = "economy" | "balanced" | "deep";
export type ThemeMode = "light" | "dark" | "system";

export interface AIChatPreferences {
  ai_chat_send_shortcut: AIChatSendShortcut;
  ai_reply_language: AIReplyLanguage;
  ai_attachment_context_budget: AIAttachmentContextBudget;
  ai_auto_open_activity: boolean;
  ai_code_line_numbers: boolean;
}

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

export interface AIManagedAttachment {
  id: number;
  name: string;
  size: number;
  sha256: string;
  status: "local" | "cloud_only" | "failed";
  cloud_ready: boolean;
  summary: string | null;
  tags: string[];
  text_status: "pending" | "ready" | "unsupported" | "failed" | "unavailable";
  created_at?: string | null;
  updated_at?: string | null;
}

export interface AIChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  reasoning_content?: string | null;
  model?: string | null;
  trace_id?: string | null;
  tool_runs?: AIToolRun[];
  attachments?: AIManagedAttachment[];
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
  credential_storage_name: string;
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
  ai_chat_send_shortcut: AIChatSendShortcut;
  ai_reply_language: AIReplyLanguage;
  ai_attachment_context_budget: AIAttachmentContextBudget;
  ai_auto_open_activity: boolean;
  ai_code_line_numbers: boolean;
  theme_mode: ThemeMode;
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

export type ArchiveStatus =
  | "archived"
  | "local_changed"
  | "cloud_only"
  | "local_only"
  | "uploading"
  | "downloading"
  | "verifying"
  | "failed"
  | "interrupted"
  | "needs_verification";

export interface ArchiveEntry {
  id: string;
  filename: string;
  original_abs_path: string | null;
  archive_root_snapshot: string | null;
  relative_path: string | null;
  restore_capability:
    | "original_path"
    | "choose_location"
    | "managed_location"
    | string;
  source_kind: string;
  status: string;
  last_error: string | null;
  retry_count: number;
  created_at: string | null;
  updated_at: string | null;
  versions?: ArchiveVersion[];
}

export interface ArchiveListItem extends ArchiveEntry {
  entry_status: string;
  size_bytes: number | null;
  cloud_path: string[] | null;
  archived_at: string | null;
  sha256: string | null;
  version_status: string;
  current_version_id: string | null;
  current_version_number: number | null;
}

export interface ArchiveVersion {
  id: string;
  version_number: number;
  size: number;
  file_type: string | null;
  mtime_ns: number;
  archived_at: string | null;
  sha256: string;
  cloud_remote_id: string | null;
  cloud_remote_path: string[] | null;
  cloud_etag: string | null;
  status: string;
  last_error: string | null;
  retry_count: number;
}

export interface ArchiveListResult {
  items: ArchiveListItem[];
  limit: number;
  offset: number;
  total?: number;
  next_cursor?: string | null;
  has_more?: boolean;
}

export interface ArchiveJob {
  id: string;
  idempotency_key: string;
  kind: "archive" | "restore" | string;
  entry_id: string | null;
  version_id: string | null;
  status: string;
  bytes_total: number;
  bytes_done: number;
  conflict_policy: ArchiveConflictPolicy | null;
  last_error: string | null;
  attempt_count: number;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  retryable?: boolean;
  deduplicated?: boolean;
  reconciled?: boolean;
  target?: string;
  comparison?: {
    existing: ArchiveFileSignature | null;
    expected: ArchiveFileSignature;
  };
}

export interface ArchiveJobEvent {
  id: string;
  job_id: string;
  event_type: string;
  status: string;
  message: string | null;
  details: Record<string, unknown>;
  bytes_done: number;
  bytes_total: number;
  created_at: string | null;
}

export type ArchiveConflictPolicy =
  | "skip"
  | "save_as"
  | "overwrite"
  | "compare";

export interface ArchiveFileSignature {
  kind?: string;
  size: number | null;
  mtime_ns: number | null;
  sha256: string | null;
}

export interface ArchiveAuthorizedRoot {
  id: string;
  path: string;
  source: string;
}

export interface RestorePlan {
  job: ArchiveJob;
  target: string;
  missing_directories: string[];
  existing: ArchiveFileSignature | null;
  comparison: {
    size_matches: boolean;
    mtime_matches: boolean;
    hash_matches: boolean;
  } | null;
  expected: ArchiveFileSignature;
  requires_directory_confirmation: boolean;
}

export interface CourseCapabilities {
  course_id: string;
  course_name?: string;
  roles: string[];
  role_source: string;
  can_view_calendar: boolean;
  can_view_members: boolean;
  can_view_submissions: boolean;
  can_manage_grades: boolean;
  can_comment_submissions: boolean;
}

export interface FeatureCapability {
  capability?: string;
  available: boolean;
  status?: string;
  reason?: string | null;
}

export interface AppCapabilities {
  courses: { items: CourseCapabilities[] } | CourseCapabilities;
  media: Record<string, FeatureCapability>;
  update: { check_only: boolean; automatic_install: boolean };
  mcp: { transport: string; read_only: boolean };
}

export interface CalendarEventDto extends Record<string, unknown> {
  id?: string | number;
  title?: string;
  start_at?: string | null;
  end_at?: string | null;
  context_name?: string;
  context_code?: string;
  workflow_state?: string;
  assignment?: Record<string, unknown>;
}

export interface CalendarResult {
  month_start: string;
  month_end: string;
  month_events: CalendarEventDto[];
  upcoming_start: string;
  upcoming_end: string;
  upcoming_events: CalendarEventDto[];
}

export type TimetableConnectionState =
  | "loading"
  | "empty"
  | "awaiting_configuration"
  | "connected"
  | "local"
  | "offline";

export interface TimetableStatus {
  state: TimetableConnectionState;
  provider: string;
  lastSyncedAt: string | null;
  message: string | null;
  supportsOAuth: boolean;
  hasLocalData: boolean;
}

export interface TimetableEvent {
  id: string;
  title: string;
  courseName: string;
  startAt: string;
  endAt: string | null;
  location: string | null;
  periodLabel: string | null;
  eventType: "course";
  source: string;
  canonicalCourseId: string | null;
}

export interface TimetableSchedule {
  events: TimetableEvent[];
}

export interface TimetablePreviewCourse {
  id?: string;
  name: string;
  location?: string | null;
  sessions?: number;
}

export interface TimetableImportPreview {
  previewId: string;
  format: "json" | "ics" | string;
  courses: number | Array<string | TimetablePreviewCourse>;
  sessions: number;
  warnings: string[];
}

export interface TimetableImportCommit {
  status: string;
  importedCourses: number;
  importedSessions: number;
  updatedSessions: number;
}

export interface GradebookAssignmentDto extends Record<string, unknown> {
  id: string | number;
  name?: string;
  points_possible?: number | null;
}

export interface GradebookStudentDto extends Record<string, unknown> {
  id: string | number;
  name?: string;
  login_id?: string | null;
}

export interface GradebookSubmissionDto extends Record<string, unknown> {
  assignment_id?: string | number;
  score?: number | null;
  grade?: string | null;
  workflow_state?: string;
  late?: boolean;
  missing?: boolean;
}

export interface GradebookSubmissionGroupDto extends Record<string, unknown> {
  user_id?: string | number;
  submissions?: GradebookSubmissionDto[];
}

export interface GradebookRowDto extends Record<string, unknown> {
  user_id: string | number;
  name?: string;
  login_id?: string | null;
  grades?: Record<string, string>;
}

export interface GradebookResult {
  course_id: string;
  assignments: GradebookAssignmentDto[];
  students: GradebookStudentDto[];
  submissions: GradebookSubmissionGroupDto[];
  rows: GradebookRowDto[];
  statistics: Record<string, unknown>;
}

export interface RosterMemberDto extends Record<string, unknown> {
  id: string | number;
  name?: string;
  sortable_name?: string | null;
  login_id?: string | null;
  email?: string | null;
  created_at?: string | null;
  academic_roles?: string[];
  enrollments?: Array<Record<string, unknown>>;
}

export interface RosterResult {
  items: RosterMemberDto[];
}

export interface GradingAttachmentDto extends Record<string, unknown> {
  id?: string | number;
  display_name?: string;
  filename?: string;
  size?: number | null;
  source_id?: string;
}

export interface GradingCommentDto extends Record<string, unknown> {
  id?: string | number;
  author_name?: string;
  author?: { display_name?: string };
  comment?: string;
  text_comment?: string;
  created_at?: string | null;
}

export interface GradingSubmissionDto extends Record<string, unknown> {
  id?: string | number;
  user_id?: string | number;
  user?: Record<string, unknown>;
  submitted_at?: string | null;
  workflow_state?: string;
  late?: boolean;
  missing?: boolean;
  score?: number | null;
  grade?: string | null;
  attempt?: number | null;
  body?: string | null;
  attachments?: GradingAttachmentDto[];
  submission_comments?: GradingCommentDto[];
}

export interface GradingResult {
  items: GradingSubmissionDto[];
}

export interface ExportResult {
  filename: string;
  content_type: string;
  row_count: number;
  status: string;
  reveal_token: string;
}

export interface GradingUpdateResult {
  submission: GradingSubmissionDto;
  grade_verified: boolean;
  comment_verified: boolean;
  recovered_after_uncertain_write: boolean;
}

export interface MediaActionDescriptor {
  available: boolean;
  status?: string;
  transport: string;
  action: string;
  source_id: string;
  media_kind?: string;
  content_type?: string | null;
  name?: string;
  download_name?: string;
  url?: string;
  urls?: string[];
}

export interface CourseMediaItemDto extends Record<string, unknown> {
  source_id: string;
  name: string;
  media_kind: "video" | "audio";
  content_type?: string | null;
  size?: number | null;
  source?: "canvas" | "video_space" | "legacy";
  course_name?: string;
  teaching_class?: string | null;
  week_number?: number | null;
  weekday?: number | null;
  weekday_label?: string | null;
  lesson_number?: number | null;
  recorded_at?: string | null;
  ended_at?: string | null;
  classroom?: string | null;
  duration?: number | null;
  downloadable?: boolean;
  supports_subtitle?: boolean;
  supports_slides_pdf?: boolean;
  playback: MediaActionDescriptor;
  subtitles?: Array<Record<string, unknown>>;
}

export interface CourseMediaResult {
  items: CourseMediaItemDto[];
  subtitles: Array<Record<string, unknown>>;
  unmatched_subtitles: Array<Record<string, unknown>>;
  counts: { video: number; audio: number; subtitle: number };
  warning?: string;
}

export interface MediaPreviewResult extends Record<string, unknown> {
  kind: string;
  name?: string;
  text?: string;
  data_url?: string;
}

export interface UpdateCheckResult extends Record<string, unknown> {
  current_version: string;
  latest_version: string;
  update_available: boolean;
  automatic_install: boolean;
  release?: { page_url?: string; notes?: string; published_at?: string | null };
  download_plan?: {
    asset_name: string;
    download_url: string;
    size: number;
    sha256?: string | null;
    release_page_url: string;
    requires_sha256_verification: boolean;
  } | null;
}

export interface McpConfig {
  transport: "stdio";
  command: string;
  args: string[];
  read_only: boolean;
}

export type TranscriptJobStatus =
  | "unsaved"
  | "queued"
  | "fetching"
  | "saved"
  | "waiting_remote"
  | "waiting_for_ai"
  | "organizing"
  | "completed"
  | "completed_with_warnings"
  | "partial"
  | "failed"
  | "interrupted"
  | "cancelled";

export type Phase1PipelineStatus =
  | "completed"
  | "completed_with_warnings"
  | "partial"
  | "failed";

export interface Phase1QualityReport {
  score: number;
  passed: boolean;
  metrics: Record<string, number>;
  warnings: string[];
  schema_pass: boolean;
  critic_pass_rate: number;
  uncertain_rate: number;
  numeric_change_count: number;
  unsupported_change_count: number;
  status: Phase1PipelineStatus;
}

export interface TranscriptJob {
  id: string;
  batch_id: string;
  source_id: string;
  title: string;
  status: TranscriptJobStatus;
  stage: string;
  progress: number;
  attempts: number;
  message?: string | null;
  error?: string | null;
  reused?: boolean;
  phase1_status?: Phase1PipelineStatus | null;
  pipeline_status?: Phase1PipelineStatus | null;
  quality?: Phase1QualityReport | null;
  phase1_reused?: boolean;
  phase1_warning?: string | null;
  partial_warning?: boolean;
}

export interface TranscriptBatch {
  id: string;
  course_id: string;
  course_name: string;
  status: TranscriptJobStatus;
  progress: number;
  created_at: string;
  updated_at: string;
  jobs: TranscriptJob[];
}

export interface TranscriptArtifact {
  id: string;
  kind: "raw_vtt" | "cues" | "cleaned" | "summary_json" | "summary";
  label: string;
  content_type: string;
}

export interface TranscriptArtifactContent {
  id: string;
  kind: TranscriptArtifact["kind"];
  content: string;
}

export type Phase1ArtifactKind =
  | "semantic_chunks"
  | "corrected_json"
  | "corrected"
  | "correction_diff"
  | "uncertain"
  | "quality"
  | "course_memory_version"
  | "pipeline_events"
  | "training_examples";

export type Phase1ArtifactContentType =
  | "application/json"
  | "application/x-ndjson"
  | "text/markdown";

export interface Phase1ArtifactItem {
  id: string | null;
  kind: Phase1ArtifactKind;
  label: string;
  content_type: Phase1ArtifactContentType;
  available: boolean;
  version: "v2";
  size?: number;
  sha256?: string;
}

export interface Phase1ArtifactList {
  available: boolean;
  status: Phase1PipelineStatus | null;
  pipeline_status: Phase1PipelineStatus | null;
  quality: Phase1QualityReport | null;
  warnings: string[];
  items: Phase1ArtifactItem[];
}

export interface Phase1ArtifactRead {
  id: string;
  kind: Phase1ArtifactKind;
  content: string;
  version: "v2";
  content_type: Phase1ArtifactContentType;
  size: number;
  sha256: string;
  data?: Record<string, unknown> | unknown[];
}

export interface Phase1CorrectionChange {
  original: string;
  corrected: string;
  type: string;
  confidence: number;
  reason: string;
  start: number;
  end: number;
  cue_ids: string[];
  evidence: string[];
}

export interface Phase1TermCandidate {
  original: string;
  canonical: string;
  category: string;
  confidence: number;
  source: string;
  cue_ids: string[];
  evidence: string[];
}

export interface Phase1UncertainSpan {
  text: string;
  candidates: Phase1TermCandidate[];
  confidence: number;
  start: number;
  end: number;
  cue_ids: string[];
}
