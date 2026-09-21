export type ViewName = "overview" | "deadlines" | "messages" | "materials";

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

export interface Material {
  source_id: string;
  name: string;
  course: string;
  course_id: string;
  term: string;
  size: number | null;
  updated_at: string | null;
  download_status: "pending" | "downloaded" | "failed";
  can_open: boolean;
}

export interface OverviewData {
  courses: number;
  upcoming_deadlines: number;
  unread_emails: number;
  deadlines: Deadline[];
  messages: MessageItem[];
}

export interface MaterialFilters {
  terms: string[];
  courses: Array<{ id: string; name: string; term: string | null }>;
  download_statuses: string[];
}

export interface SyncStatus {
  status: "idle" | "syncing";
  last_success_at: string | null;
  last_run_status: string | null;
  last_run_at: string | null;
}
