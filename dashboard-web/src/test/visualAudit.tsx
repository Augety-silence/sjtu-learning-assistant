/**
 * Local-only visual fixture. This adapter never contacts the desktop bridge,
 * database, Canvas, or cloud services; all non-read actions are rejected.
 */
import { MotionConfig } from "motion/react";
import React from "react";
import ReactDOM from "react-dom/client";
import App from "@/App";
import { ToastProvider } from "@/components/Toast";
import "@/test/performanceProbe";
import "@/test/revealProbe";
import type { BridgeResponse } from "@/lib/api";
import type {
  AIChatSession,
  AIChatSessionSummary,
  AppCapabilities,
  ArchiveJob,
  ArchiveListResult,
  AssignmentItem,
  BackupStatus,
  CalendarResult,
  CourseMediaResult,
  GradebookResult,
  GradingResult,
  KnowledgeCompilerTask,
  LocalProjectList,
  MaterialTree,
  MessageDetail,
  MessageItem,
  OverviewData,
  RosterResult,
  SettingsStatus,
  ThemeMode,
  TimetableSchedule,
  TimetableStatus,
} from "@/lib/types";
import "@/index.css";
import "@/aura-pages.css";
import "@/ai-chat-aura.css";
import "@/app-brand.css";
import "@/performance.css";
import "@/workspace-motion.css";
import "@/page-motion.css";

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
    __VISUAL_AUDIT_UNCOVERED_READS__?: string[];
  }
}

const params = new URLSearchParams(window.location.search);
const theme: ThemeMode = params.get("theme") === "dark" ? "dark" : "light";
const requestedReadDelay = Number(params.get("read_delay") ?? 0);
const readDelayMs = Number.isFinite(requestedReadDelay)
  ? Math.min(2000, Math.max(0, requestedReadDelay))
  : 0;
const now = new Date();
const daysFromNow = (days: number, hour = 10) => {
  const date = new Date(now);
  date.setDate(date.getDate() + days);
  date.setHours(hour, 0, 0, 0);
  return date.toISOString();
};

const settings: SettingsStatus = {
  archive_root_ready: true,
  archive_root: "/local-visual-audit/archive",
  credential_storage_name: "仅本地合成数据",
  auto_download_current_term: true,
  organize_by_category: true,
  mail_account: "",
  canvas_token_saved: true,
  mail_password_saved: false,
  cloud_token_saved: true,
  credential_status_error: null,
  ai_enabled: true,
  ai_base_url: "https://example.invalid/v1",
  ai_model: "deepseek-chat",
  ai_key_saved: true,
  ai_chat_send_shortcut: "enter",
  ai_reply_language: "auto",
  ai_attachment_context_budget: "balanced",
  ai_auto_open_activity: true,
  ai_code_line_numbers: false,
  theme_mode: theme,
};

const courseCapabilities: AppCapabilities = {
  courses: {
    items: [
      {
        course_id: "1001",
        course_name: "文本分析与大模型",
        roles: ["teacher"],
        role_source: "visual-audit-fixture",
        can_view_calendar: true,
        can_view_members: true,
        can_view_submissions: true,
        can_manage_grades: true,
        can_comment_submissions: true,
      },
    ],
  },
  media: {},
  update: { check_only: true, automatic_install: false },
  mcp: { transport: "stdio", read_only: true },
};

const messages: MessageItem[] = [
  {
    source_id: "audit-mail-1",
    kind: "email",
    title: "本周课程讨论安排",
    source_label: "课程助教",
    occurred_at: daysFromNow(-1, 15),
    is_unread: true,
    url: null,
  },
  {
    source_id: "audit-announcement-1",
    kind: "announcement",
    title: "论文阅读清单已更新",
    source_label: "文本分析与大模型",
    occurred_at: daysFromNow(-2, 9),
    is_unread: true,
    url: "https://example.invalid/course/1001/announcements/1",
  },
  {
    source_id: "audit-assignment-1",
    kind: "assignment",
    title: "实验报告：文本分类基线",
    source_label: "文本分析与大模型",
    occurred_at: daysFromNow(2, 23),
    is_unread: false,
    url: "https://example.invalid/course/1001/assignments/42",
  },
];

const assignment: AssignmentItem = {
  course_id: "1001",
  course_name: "文本分析与大模型",
  id: "42",
  name: "实验报告：文本分类基线",
  description:
    "比较两种文本分类基线，说明数据划分、评价指标与误差来源。\n\n提交一份简短实验报告。",
  due_at: daysFromNow(2, 23),
  unlock_at: daysFromNow(-3, 9),
  lock_at: null,
  points_possible: 100,
  html_url: "https://example.invalid/course/1001/assignments/42",
  submission_types: ["online_text_entry"],
  submission: {
    id: null,
    workflow_state: "unsubmitted",
    submission_type: null,
    submitted_at: null,
    attempt: null,
    missing: false,
    late: false,
    score: null,
    grade: null,
    attachments: [],
  },
  can_submit: true,
  requires_external_submission: false,
  categories: ["upcoming", "unsubmitted"],
};

const materialTree: MaterialTree = {
  categories: [
    { id: "assignments", label: "课程作业" },
    { id: "courseware", label: "课件" },
    { id: "other", label: "其他" },
  ],
  download_statuses: ["all", "downloaded", "cloud_only", "pending", "failed"],
  root: {
    id: "root",
    kind: "root",
    name: "全部资料",
    children: [
      {
        id: "term:2026-fall",
        kind: "term",
        name: "2026 秋季学期",
        children: [
          {
            id: "course:1001",
            kind: "course",
            name: "文本分析与大模型",
            course_id: "1001",
            children: [
              {
                id: "category:1001:courseware",
                kind: "category",
                name: "课件",
                course_id: "1001",
                category: "courseware",
                children: [
                  {
                    id: "file:audit-lecture-notes",
                    kind: "file",
                    name: "第 3 周 · 文本表示与分类.pdf",
                    source_id: "audit-material-1",
                    course_id: "1001",
                    category: "courseware",
                    size: 842_752,
                    updated_at: daysFromNow(-2),
                    download_status: "downloaded",
                    can_open: true,
                    can_preview: true,
                    local_path: "/local-visual-audit/lecture-notes.pdf",
                  },
                ],
              },
              {
                id: "category:1001:assignments",
                kind: "category",
                name: "课程作业",
                course_id: "1001",
                category: "assignments",
                children: [],
              },
            ],
          },
        ],
      },
    ],
  },
};

const aiSessionSummary: AIChatSessionSummary = {
  id: "visual-session-1",
  title: "文本分类实验思路",
  model: "deepseek-chat",
  thinking_depth: "standard",
  preset_id: "general",
  created_at: daysFromNow(-1),
  updated_at: daysFromNow(-1),
};

const aiSession: AIChatSession = {
  ...aiSessionSummary,
  messages: [
    {
      id: "visual-user-message",
      role: "user",
      content: "如何比较文本分类基线的误差来源？",
      created_at: daysFromNow(-1, 14),
    },
    {
      id: "visual-assistant-message",
      role: "assistant",
      content:
        "可以先固定训练/验证划分，再比较词袋与 TF‑IDF 基线。报告中说明类别分布、宏平均 F1，并抽查误分类样本。",
      reasoning_content: null,
      model: "deepseek-chat",
      trace_id: "visual-trace-1",
      tool_runs: [],
      created_at: daysFromNow(-1, 14),
    },
  ],
  traces: [
    {
      id: "visual-trace-1",
      preset_id: "general",
      status: "completed",
      steps: 1,
      tool_runs: [],
      created_at: daysFromNow(-1, 14),
    },
  ],
};

const backupStatus: BackupStatus = {
  status: "idle",
  available: true,
  availability_message: null,
  counts: {
    canvas: 18,
    mail: 4,
    ready: 19,
    cloud_only: 2,
    missing_local: 1,
    total: 22,
  },
  progress: null,
  last_result: {
    started_at: daysFromNow(-1, 9),
    finished_at: daysFromNow(-1, 9),
    uploaded: 3,
    skipped_existing: 16,
    skipped_missing_local: 1,
    local_removed: 0,
    failed: 0,
    failures: [],
  },
};

const archiveList: ArchiveListResult = {
  items: [
    {
      id: "visual-archive-entry-1",
      filename: "第 3 周 · 文本表示与分类.pdf",
      original_abs_path: "/local-visual-audit/course-materials/lecture-03.pdf",
      archive_root_snapshot: "/local-visual-audit/archive",
      relative_path: "文本分析与大模型/课件/lecture-03.pdf",
      restore_capability: "original_path",
      source_kind: "canvas",
      status: "archived",
      last_error: null,
      retry_count: 0,
      created_at: daysFromNow(-3),
      updated_at: daysFromNow(-1),
      entry_status: "archived",
      size_bytes: 842_752,
      cloud_path: ["sjtu-learning-assistant", "lecture-03.pdf"],
      archived_at: daysFromNow(-1),
      sha256: "visual-audit-sha256-placeholder",
      version_status: "archived",
      current_version_id: "visual-archive-version-1",
      current_version_number: 1,
    },
  ],
  limit: 25,
  offset: 0,
  total: 1,
  next_cursor: null,
  has_more: false,
};

const archiveJobs: ArchiveJob[] = [
  {
    id: "visual-archive-job-1",
    idempotency_key: "visual-audit-archive-job-1",
    kind: "archive",
    entry_id: "visual-archive-entry-1",
    version_id: "visual-archive-version-1",
    status: "completed",
    bytes_total: 842_752,
    bytes_done: 842_752,
    conflict_policy: null,
    last_error: null,
    attempt_count: 1,
    created_at: daysFromNow(-1, 16),
    started_at: daysFromNow(-1, 16),
    finished_at: daysFromNow(-1, 16),
    retryable: false,
  },
];

const localProjects: LocalProjectList = {
  items: [
    {
      id: "visual-project-1",
      name: "文本分析课程笔记",
      source_root: "/local-visual-audit/course-materials",
      target_root: "/local-visual-audit/knowledge-vault",
      markdown_files: 24,
      total_bytes: 1_482_000,
      image_references: 7,
      courses: ["文本分析与大模型", "自然语言处理"],
      truncated_courses: false,
      available: true,
      issue: null,
      compile_status: "completed",
      phase_index: 6,
      total_phases: 6,
      last_compiled_at: daysFromNow(-1, 17),
      compile_error: null,
      created_at: daysFromNow(-12),
      updated_at: daysFromNow(-1, 17),
    },
  ],
};

const knowledgeTask: KnowledgeCompilerTask = {
  status: "completed",
  task_id: "visual-knowledge-task",
  source_name: "课程资料",
  target_name: "学习知识库",
  mode: "full",
  phase_index: 6,
  total_phases: 6,
  current_phase: null,
  progress: { done: 24, total: 24, current_file: null },
  counts: {
    markdown_files: 24,
    processed_files: 24,
    generated_notes: 18,
    warnings: 1,
  },
  warnings: ["1 份扫描版资料未提取到正文。"],
  events: [
    {
      time: daysFromNow(-1, 17),
      level: "success",
      message: "知识库编译完成，已生成 18 篇笔记。",
    },
  ],
  error: null,
  started_at: daysFromNow(-1, 16),
  updated_at: daysFromNow(-1, 17),
  finished_at: daysFromNow(-1, 17),
};

const calendar: CalendarResult = {
  month_start: new Date(now.getFullYear(), now.getMonth(), 1).toISOString(),
  month_end: new Date(now.getFullYear(), now.getMonth() + 1, 0).toISOString(),
  month_events: [
    {
      id: "calendar-audit-1",
      title: assignment.name,
      start_at: assignment.due_at,
      end_at: assignment.due_at,
      context_name: "文本分析与大模型",
      context_code: "course_1001",
      workflow_state: "unsubmitted",
      assignment: {
        id: 42,
        name: assignment.name,
        due_at: assignment.due_at,
        created_at: daysFromNow(-4),
        unlock_at: daysFromNow(-3),
        html_url: assignment.html_url,
      },
      html_url: assignment.html_url,
    },
  ],
  upcoming_start: now.toISOString(),
  upcoming_end: daysFromNow(14),
  upcoming_events: [],
};

const timetableStatus: TimetableStatus = {
  state: "local",
  provider: "本地课表",
  lastSyncedAt: daysFromNow(-1, 18),
  message: "显示本机导入的课程安排。",
  supportsOAuth: false,
  hasLocalData: true,
};

const timetableSchedule: TimetableSchedule = {
  events: [
    {
      id: "timetable-audit-1",
      title: "文本表示与分类",
      courseName: "文本分析与大模型",
      startAt: daysFromNow(1, 9),
      endAt: daysFromNow(1, 10),
      location: "闵行校区 · 教学楼",
      periodLabel: "第 3–4 节",
      eventType: "course",
      source: "visual-audit-fixture",
      canonicalCourseId: "1001",
    },
  ],
};

const media: CourseMediaResult = {
  items: [
    {
      source_id: "visual-video-1",
      name: "第 3 讲：文本分类与误差分析",
      media_kind: "video",
      content_type: "video/mp4",
      size: 128_000_000,
      source: params.get("video_ai") === "1" ? "video_space" : "canvas",
      course_name: "文本分析与大模型",
      teaching_class: "周二班",
      week_number: 3,
      weekday: 2,
      weekday_label: "周二",
      lesson_number: 1,
      recorded_at: daysFromNow(-3, 14),
      ended_at: daysFromNow(-3, 16),
      classroom: "闵行校区 · 教学楼",
      duration: 5_420,
      downloadable: false,
      supports_subtitle: true,
      supports_slides_pdf: true,
      playback: {
        available: true,
        status: "available",
        transport: "fixture",
        action: "play_remote_video",
        source_id: "visual-video-1",
        media_kind: "video",
        content_type: "video/mp4",
        name: "第 3 讲：文本分类与误差分析",
        url: "https://example.invalid/visual-audit-video.mp4",
      },
      subtitles: [],
    },
  ],
  subtitles: [],
  unmatched_subtitles: [],
  counts: { video: 1, audio: 0, subtitle: 0 },
};

const gradebook: GradebookResult = {
  course_id: "1001",
  assignments: [
    { id: 42, name: assignment.name, points_possible: 100 },
    { id: 43, name: "课堂练习：特征表示", points_possible: 20 },
  ],
  students: [
    { id: 2001, name: "林同学", login_id: "student-audit-1" },
    { id: 2002, name: "周同学", login_id: "student-audit-2" },
  ],
  submissions: [
    {
      user_id: 2001,
      submissions: [
        {
          assignment_id: 42,
          score: 88,
          grade: "88",
          workflow_state: "graded",
        },
      ],
    },
  ],
  rows: [
    {
      user_id: 2001,
      name: "林同学",
      login_id: "student-audit-1",
      grades: { "42": "88" },
    },
    { user_id: 2002, name: "周同学", login_id: "student-audit-2", grades: {} },
  ],
  statistics: {},
};

const grading: GradingResult = {
  items: [
    {
      id: 9001,
      user_id: 2002,
      user: { id: 2002, name: "周同学", login_id: "student-audit-2" },
      submitted_at: daysFromNow(-1, 20),
      workflow_state: "submitted",
      late: false,
      missing: false,
      score: null,
      grade: null,
      attempt: 1,
      body: "本次实验使用 TF-IDF 与线性分类器作为比较基线。",
      attachments: [],
      submission_comments: [],
    },
  ],
};

const overview: OverviewData = {
  courses: 3,
  upcoming_deadlines: 2,
  unread_emails: 1,
  deadlines: [
    {
      source_id: "deadline-audit-1",
      title: assignment.name,
      course: "文本分析与大模型",
      due_at: assignment.due_at,
      unlock_at: assignment.unlock_at,
      published_at: daysFromNow(-4),
      url: assignment.html_url,
      submission_state: "unsubmitted",
    },
  ],
  messages,
};

const readActions = new Set([
  "overview",
  "sync_status",
  "capabilities",
  "settings_status",
  "calendar",
  "deadlines",
  "timetable_status",
  "timetable_schedule",
  "messages",
  "message_detail",
  "message_resource",
  "assignments_list",
  "detail",
  "can_submit",
  "pan_list",
  "material_tree",
  "material_preview",
  "media_preview",
  "backup_status",
  "archive_list",
  "archive_jobs",
  "local_projects_list",
  "knowledge_compiler_status",
  "ai_presets",
  "ai_chat_sessions",
  "ai_chat_session",
  "media",
  "media_capabilities",
  "ai_capabilities",
  "transcript_jobs",
  "transcript_artifacts",
  "transcript_artifact_read",
  "transcript_v2_artifacts",
  "transcript_v2_artifact_read",
  "video",
  "video_subtitles",
  "gradebook",
  "roster",
  "grading",
]);

const writeActions = new Set([
  "sync_trigger",
  "message_mark_read",
  "material_download",
  "material_open",
  "material_move",
  "material_restore_auto",
  "settings_update",
  "settings_ai_import",
  "settings_ai_test",
  "settings_credential_save",
  "settings_credential_delete",
  "ai_chat_new",
  "ai_chat_send",
  "ai_chat_delete",
  "ai_attachment_pick",
  "ai_attachment_ingest",
  "ai_attachment_restore",
  "ai_attachment_reveal",
  "archive_organize",
  "open_external",
  "submit_text",
  "submit_url",
  "pick_local_file",
  "submit_local_file",
  "submit_cloud_file",
  "open_external_assignment",
  "backup_start",
  "backup_token_save",
  "backup_token_delete",
  "transcript_batch_start",
  "transcript_retry",
  "transcript_cancel",
  "transcript_artifact_reveal",
  "knowledge_compiler_start",
  "knowledge_compiler_cancel",
  "local_projects_add",
  "local_projects_refresh",
  "local_projects_remove",
  "grading_update",
  "gradebook_export",
  "roster_export",
  "academic_export_reveal",
]);

function messageDetail(kind: string | undefined): MessageDetail {
  const item =
    messages.find((candidate) => candidate.kind === kind) ?? messages[0];
  return {
    title: item.title,
    source_label: item.source_label,
    occurred_at: item.occurred_at,
    body: "这是仅供本地页面验收的合成消息正文，用于观察标题、正文和附件区域的文字起始位置。",
    body_html: null,
    format: "text",
    pending_body_sync: false,
    attachments: [],
    resources: [],
    url: item.url,
    is_unread: item.is_unread,
  };
}

function fixtureData(action: string, payload: Record<string, unknown> = {}) {
  switch (action) {
    case "overview":
      return overview;
    case "sync_status":
      return {
        status: "idle",
        last_success_at: daysFromNow(-1, 18),
        last_run_status: "success",
        last_run_at: daysFromNow(-1, 18),
      };
    case "capabilities":
      return courseCapabilities;
    case "settings_status":
      return settings;
    case "calendar":
      return calendar;
    case "deadlines":
      return { items: overview.deadlines };
    case "timetable_status":
      return timetableStatus;
    case "timetable_schedule":
      return timetableSchedule;
    case "messages":
      return {
        items:
          payload.kind === "all"
            ? messages
            : messages.filter((item) => item.kind === payload.kind),
      };
    case "message_detail":
      return messageDetail(String(payload.kind ?? ""));
    case "message_resource":
      return {
        data_url: "data:text/plain;charset=utf-8,Local%20visual%20fixture",
      };
    case "assignments_list":
      return {
        category: String(payload.category ?? "today"),
        items: [assignment],
      };
    case "detail":
      return assignment;
    case "can_submit":
      return {
        can_submit: true,
        requires_external_submission: false,
        submission_types: assignment.submission_types,
      };
    case "pan_list":
      return {
        remote_path: "",
        page: 1,
        page_size: 50,
        total: 0,
        has_more: false,
        items: [],
      };
    case "material_tree":
      return materialTree;
    case "material_preview":
    case "media_preview":
      return {
        kind: "text",
        name: "本地合成预览.txt",
        text: "本地视觉验收夹具，不含真实课程文件。",
      };
    case "backup_status":
      return backupStatus;
    case "archive_list":
      return archiveList;
    case "archive_jobs":
      return { items: archiveJobs };
    case "local_projects_list":
      return localProjects;
    case "knowledge_compiler_status":
      return knowledgeTask;
    case "ai_presets":
      return {
        default_preset_id: "general",
        items: [
          {
            id: "general",
            name: "学习数据 Agent",
            description: "检索合成课程与学习资料",
            allowed_tools: [
              "list_courses",
              "search_course_files",
              "get_deadlines",
            ],
            is_default: true,
          },
        ],
      };
    case "ai_chat_sessions":
      return { items: [aiSessionSummary] };
    case "ai_chat_session":
      return aiSession;
    case "media":
      return media;
    case "ai_capabilities":
      return {
        schema_version: 1,
        tasks: [],
        preflight: {
          enabled: params.get("ai_ready") !== "0",
          configured: params.get("ai_ready") !== "0",
          ready_for_request: params.get("ai_ready") !== "0",
          connection_verified: false,
          model: "deepseek-chat",
          key_status_available: true,
          reasons: params.get("ai_ready") === "0" ? ["ai_disabled"] : [],
          streaming_implemented: false,
        },
      };
    case "media_capabilities":
      return {
        video_screenshot_pdf: { available: false, reason: "仅本地视觉验收" },
      };
    case "transcript_jobs":
      return {
        items:
          params.get("video_ai") === "1"
            ? [
                {
                  id: "a".repeat(32),
                  batch_id: "b".repeat(32),
                  source_id: "visual-video-1",
                  title: "合成课程 · 第三讲",
                  status: "completed",
                  stage: "completed",
                  progress: 100,
                  attempts: 1,
                  phase1_status: "completed",
                  message: "合成验收材料已完成；未调用模型。",
                },
              ]
            : [],
      };
    case "transcript_artifacts":
      return {
        items: [
          {
            id: "visual-summary",
            kind: "summary_json",
            label: "合成学习材料",
            content_type: "application/json",
          },
        ],
      };
    case "transcript_artifact_read":
      return {
        id: "visual-summary",
        kind: "summary_json",
        content: JSON.stringify({
          knowledge_points: ["合成示例：边际贡献是销售收入减去变动成本。"],
          study_guide: {
            questions: [
              {
                prompt: "单位售价100元、变动成本60元，边际贡献是多少？",
                answer: "40元。",
                source: "自编练习或例子",
              },
            ],
          },
        }),
      };
    case "transcript_v2_artifacts":
      return {
        available: true,
        status: "completed",
        pipeline_status: "completed",
        quality: null,
        warnings: [],
        items: ["corrected", "pipeline_events"].map((kind) => ({
          id: `visual-${kind}`,
          kind,
          label: kind,
          content_type:
            kind === "corrected" ? "text/markdown" : "application/json",
          available: true,
          version: "v2",
        })),
      };
    case "transcript_v2_artifact_read": {
      const kind =
        payload.artifact_id === "visual-pipeline_events"
          ? "pipeline_events"
          : "corrected";
      const data = [
        {
          agent: "terminology",
          status: "ok",
          token_usage: {
            prompt_tokens: 609,
            completion_tokens: 278,
            total_tokens: 887,
          },
        },
        {
          agent: "correction",
          status: "ok",
          token_usage: {
            prompt_tokens: 1438,
            completion_tokens: 87,
            total_tokens: 1525,
          },
        },
        {
          agent: "critic",
          status: "ok",
          token_usage: {
            prompt_tokens: 669,
            completion_tokens: 25,
            total_tokens: 694,
          },
        },
      ];
      const content =
        kind === "corrected"
          ? "# 合成校对字幕\n\n边际贡献等于销售收入减去变动成本。"
          : JSON.stringify(data);
      return {
        id: payload.artifact_id,
        kind,
        content,
        version: "v2",
        content_type:
          kind === "corrected" ? "text/markdown" : "application/json",
        size: content.length,
        sha256: "c".repeat(64),
        ...(kind === "pipeline_events" ? { data } : {}),
      };
    }
    case "video":
      return media.items[0].playback;
    case "video_subtitles":
      return {
        status: "ready",
        message: "已加载本地合成字幕。",
        content_type: "text/vtt",
        vtt: "WEBVTT\n\n00:00:01.000 --> 00:00:04.000\n这是用于视觉验收的字幕。\n",
        cue_count: 1,
      };
    case "gradebook":
      return gradebook;
    case "roster":
      return {
        items: [
          {
            id: 2001,
            name: "林同学",
            sortable_name: "林, 同学",
            login_id: "student-audit-1",
            email: "student-audit-1@example.invalid",
            created_at: daysFromNow(-30),
            academic_roles: ["学生"],
            enrollments: [{ course_section_id: "文本分析与大模型 01 班" }],
          },
          {
            id: 2002,
            name: "周同学",
            sortable_name: "周, 同学",
            login_id: "student-audit-2",
            email: "student-audit-2@example.invalid",
            created_at: daysFromNow(-20),
            academic_roles: ["学生"],
            enrollments: [{ course_section_id: "文本分析与大模型 01 班" }],
          },
        ],
      } satisfies RosterResult;
    case "grading":
      return grading;
    default:
      return undefined;
  }
}

async function localInvoke<T>(
  action: string,
  payload: Record<string, unknown> = {},
): Promise<BridgeResponse<T>> {
  if (readActions.has(action)) {
    if (readDelayMs > 0) {
      await new Promise<void>((resolve) =>
        window.setTimeout(resolve, readDelayMs),
      );
    }
    const data = fixtureData(action, payload);
    if (data !== undefined) return { ok: true, data: data as T };
    const uncovered = window.__VISUAL_AUDIT_UNCOVERED_READS__ ?? [];
    if (!uncovered.includes(action)) uncovered.push(action);
    window.__VISUAL_AUDIT_UNCOVERED_READS__ = uncovered;
    console.warn(`[本地视觉验收] 未覆盖的读取操作：${action}`, payload);
    return {
      ok: false,
      error: {
        code: "visual_fixture_uncovered_read",
        message: `本地视觉验收尚未为读取操作「${action}」准备合成数据。`,
      },
    };
  }

  const knownWrite = writeActions.has(action);
  if (!knownWrite) {
    const uncovered = window.__VISUAL_AUDIT_UNCOVERED_READS__ ?? [];
    if (!uncovered.includes(action)) uncovered.push(action);
    window.__VISUAL_AUDIT_UNCOVERED_READS__ = uncovered;
    console.warn(`[本地视觉验收] 未登记操作：${action}`, payload);
  }
  return {
    ok: false,
    error: {
      code: "validation_environment",
      message: `本地视觉验收环境已拒绝操作「${action}」；不会写入数据库、云端或外部服务。`,
    },
  };
}

window.pywebview = { api: { invoke: localInvoke } };

const root = document.getElementById("root");
if (!root) throw new Error("本地视觉验收入口缺少 #root 容器。");

ReactDOM.createRoot(root).render(
  <React.StrictMode>
    <MotionConfig reducedMotion="user">
      <ToastProvider>
        <App />
      </ToastProvider>
    </MotionConfig>
  </React.StrictMode>,
);
