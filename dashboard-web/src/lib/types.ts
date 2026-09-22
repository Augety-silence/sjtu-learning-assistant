export type ViewName =
  | "overview"
  | "deadlines"
  | "messages"
  | "materials"
  | "settings";

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
  download_status?: "pending" | "downloaded" | "failed";
  can_open?: boolean;
  local_path?: string | null;
}

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
