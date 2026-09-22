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

export interface MessageItem {
  kind: "email" | "announcement";
  title: string;
  source_label: string;
  occurred_at: string | null;
  is_unread: boolean;
  url: string | null;
}

export interface MaterialNode {
  id: string;
  kind: "root" | "term" | "course" | "category" | "folder" | "file";
  name: string;
  children?: MaterialNode[];
  source_id?: string;
  course_id?: string;
  category?: string;
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
}
