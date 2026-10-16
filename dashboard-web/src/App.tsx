import { Sparkles } from "lucide-react";
import {
  type ReactNode,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { AIChatView } from "@/components/AIChatView";
import { AppShell } from "@/components/AppShell";
import {
  AssignmentsView,
  type AssignmentTarget,
} from "@/components/AssignmentsView";
import { BackupView } from "@/components/BackupView";
import type { CalendarEventItem } from "@/components/CalendarView";
import { ContentReveal } from "@/components/ContentReveal";
import { DeadlinesView } from "@/components/DeadlinesView";
import {
  type GradeAssignment,
  type GradeSaveInput,
  GradesView,
  type StudentGradeRow,
} from "@/components/GradesView";
import { type GradingSubmission, GradingView } from "@/components/GradingView";
import { KnowledgeCompilerView } from "@/components/KnowledgeCompilerView";
import { LocalProjectsView } from "@/components/LocalProjectsView";
import { MaterialsView } from "@/components/MaterialsView";
import { MessagesView } from "@/components/MessagesView";
import { OverviewView } from "@/components/OverviewView";
import {
  type RosterExportRequest,
  type RosterMember,
  RosterView,
} from "@/components/RosterView";
import { ScheduleView } from "@/components/ScheduleView";
import { SettingsView } from "@/components/SettingsView";
import { useToast } from "@/components/Toast";
import { Button } from "@/components/ui/Button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/Tabs";
import { type CourseVideoItem, VideosView } from "@/components/VideosView";
import { WorkspaceTransition } from "@/components/WorkspaceTransition";
import { explainAIReadiness, getAICapabilities } from "@/lib/aiCapabilities";
import {
  cancelTranscriptJob,
  createVideoScreenshotPdf,
  createVideoSlidesPdf,
  exportRoster,
  getCalendar,
  getCourseMedia,
  getGradebook,
  getGrading,
  getMediaCapabilities,
  getRoster,
  getSettings,
  getTranscriptJobs,
  getTranscriptLearningSegments,
  getVideoPlayback,
  getVideoSlideImage,
  getVideoSlides,
  getVideoSubtitles,
  invoke,
  nextTranscriptPlaybackSequence,
  prepareTranscriptPlayback,
  retryTranscriptJob,
  revealAcademicExport,
  revealTranscriptArtifact,
  startTranscriptBatch,
  updateGrading,
  updateTranscriptPlayback,
} from "@/lib/api";
import {
  type BoundedReadRequest,
  createBoundedReadPool,
} from "@/lib/boundedReadPool";
import { applyThemeMode } from "@/lib/theme";
import type {
  AppCapabilities,
  CalendarEventDto,
  CourseCapabilities,
  CourseMediaItemDto,
  GradebookResult,
  GradingSubmissionDto,
  SyncStatus,
  ThemeMode,
  TranscriptArtifact,
  TranscriptJob,
  ViewName,
} from "@/lib/types";

const views: ViewName[] = [
  "overview",
  "calendar",
  "messages",
  "assignments",
  "materials",
  "grades",
  "roster",
  "grading",
  "videos",
  "backup",
  "local-projects",
  "knowledge",
  "ai-chat",
  "settings",
];
const baseViews: ViewName[] = [
  "overview",
  "calendar",
  "messages",
  "assignments",
  "materials",
  "videos",
  "backup",
  "local-projects",
  "knowledge",
  "ai-chat",
  "settings",
];

const pageAILabels: Record<ViewName, string> = {
  overview: "概览",
  calendar: "日程",
  messages: "消息",
  assignments: "作业中心",
  materials: "课程资料",
  grades: "成绩",
  roster: "花名册",
  grading: "作业批改",
  videos: "课程视频",
  backup: "云盘备份",
  "local-projects": "本地项目",
  knowledge: "知识库编译",
  "ai-chat": "AI 助手",
  settings: "设置",
};

const pageAIPrompts: Record<ViewName, string> = {
  overview:
    "根据概览中的临期事项和最新消息，给出今天最值得优先处理的三件事，并说明证据。",
  calendar: "检查当前日程与截止事项，指出时间冲突，并给出本周学习安排建议。",
  messages: "检索最近的课程消息，区分必须处理、建议关注和仅供知悉的内容。",
  assignments:
    "梳理当前作业要求和截止时间，给出可执行的完成顺序；不要替我提交。",
  materials: "根据当前课程资料范围，指出最值得先读的文件和资料缺口。",
  grades: "基于当前可见成绩与反馈，总结有证据支持的薄弱点和复习建议。",
  roster: "仅基于当前授权字段汇总名单信息，不推断学生能力或敏感属性。",
  grading: "基于当前评分量规给出反馈草稿和需人工确认的评分证据，不发布成绩。",
  videos: "结合课程视频、字幕和已有学习材料，建议下一步复习与主动练习。",
  backup:
    "解释当前备份状态和风险，并给出备份范围建议；不要执行上传、删除或恢复。",
  "local-projects": "基于当前本地项目索引，给出下一步行动和可能缺失的资料。",
  knowledge: "检查当前知识库编译范围，建议合适的编译模式和需要先确认的问题。",
  "ai-chat": "请基于本地学习数据回答问题，并标明证据与不确定性。",
  settings: "解释当前 AI 连接配置和兼容性风险，不读取或展示任何密钥。",
};

type ReadRequest = BoundedReadRequest;

function useBoundedReadRequests(): ReadRequest {
  const pool = useRef<ReadRequest | null>(null);
  if (!pool.current) pool.current = createBoundedReadPool();
  return pool.current;
}

type ScheduleMode = "calendar" | "deadlines";

function initialView(): ViewName {
  const requested = window.location.hash.replace("#/", "");
  const aliases: Record<string, ViewName> = {
    deadlines: "calendar",
    "canvas-agent": "ai-chat",
  };
  const value = aliases[requested] ?? (requested as ViewName);
  return views.includes(value) ? value : "overview";
}

function messageFrom(reason: unknown, fallback: string) {
  return reason instanceof Error ? reason.message : fallback;
}

function courseCapabilities(capabilities: AppCapabilities | null) {
  if (!capabilities) return [];
  return "items" in capabilities.courses
    ? capabilities.courses.items
    : [capabilities.courses];
}

function courseNumber(course: CourseCapabilities | undefined) {
  if (!course || !/^\d+$/.test(course.course_id)) return null;
  const value = Number(course.course_id);
  return Number.isSafeInteger(value) && value > 0 ? value : null;
}

function availableViewsFor(capabilities: AppCapabilities | null): ViewName[] {
  const courses = courseCapabilities(capabilities);
  const staffCourses = courses.filter(
    (course) =>
      course.roles.some((role) => role === "teacher" || role === "ta") &&
      (course.can_manage_grades || course.can_view_submissions),
  );
  return [
    ...baseViews,
    ...(staffCourses.length
      ? (["grades", "roster", "grading"] as ViewName[])
      : []),
  ];
}

function eventDate(event: CalendarEventDto) {
  const assignment = event.assignment ?? {};
  const dueAt = assignment.due_at;
  return (
    event.start_at ?? event.end_at ?? (typeof dueAt === "string" ? dueAt : null)
  );
}

function optionalIsoDate(value: unknown): string | null {
  if (typeof value !== "string" || !value.trim()) return null;
  const raw = value.trim().endsWith("Z")
    ? `${value.trim().slice(0, -1)}+00:00`
    : value.trim();
  const date = new Date(raw);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

function positiveInteger(value: unknown): number | null {
  if (typeof value === "number" && Number.isSafeInteger(value) && value > 0) {
    return value;
  }
  if (typeof value !== "string" || !/^\d+$/.test(value)) return null;
  const number = Number(value);
  return Number.isSafeInteger(number) && number > 0 ? number : null;
}

function adaptCalendarEvent(
  event: CalendarEventDto,
  index: number,
): CalendarEventItem | null {
  const startAt = eventDate(event);
  if (!startAt) return null;
  const assignment = event.assignment ?? {};
  const assignmentName = assignment.name;
  const contextCode = event.context_code ?? "Canvas";
  const courseMatch = /^course_(\d+)$/.exec(contextCode);
  return {
    id: String(event.id ?? assignment.id ?? `${startAt}:${index}`),
    title:
      event.title ??
      (typeof assignmentName === "string" ? assignmentName : "课程事项"),
    courseName: event.context_name ?? contextCode.replace(/^course_/, "课程 "),
    startAt,
    endAt: event.end_at,
    status:
      event.workflow_state === "graded"
        ? "graded"
        : event.workflow_state === "submitted"
          ? "submitted"
          : undefined,
    eventType: "assignment",
    source: "canvas",
    assignmentCourseId: positiveInteger(courseMatch?.[1]),
    assignmentId: positiveInteger(assignment.id),
    publishedAt: optionalIsoDate(assignment.created_at),
    availableAt: optionalIsoDate(assignment.unlock_at),
    url:
      typeof event.html_url === "string"
        ? event.html_url
        : typeof assignment.html_url === "string"
          ? assignment.html_url
          : null,
  };
}

function ScheduleAdapter({
  mode,
  syncStatus,
  onModeChange,
  onNavigateAssignments,
  onAskAI,
}: {
  mode: ScheduleMode;
  syncStatus: SyncStatus | null;
  onModeChange: (mode: ScheduleMode) => void;
  onNavigateAssignments: (target?: AssignmentTarget) => void;
  onAskAI: (prompt: string) => void;
}) {
  const [month, setMonth] = useState(
    () => new Date(new Date().getFullYear(), new Date().getMonth(), 1),
  );
  const [events, setEvents] = useState<CalendarEventItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const loadCanvas = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await getCalendar(
        month.getFullYear(),
        month.getMonth() + 1,
      );
      setEvents(
        [...result.month_events, ...result.upcoming_events]
          .map(adaptCalendarEvent)
          .filter((item): item is CalendarEventItem => item !== null)
          .filter(
            (item, index, items) =>
              items.findIndex((candidate) => candidate.id === item.id) ===
              index,
          ),
      );
    } catch (reason) {
      setError(messageFrom(reason, "Canvas 日历加载失败"));
    } finally {
      setLoading(false);
    }
  }, [month]);
  useEffect(() => void loadCanvas(), [loadCanvas]);

  if (mode === "deadlines") return <DeadlinesView embedded />;
  return (
    <ScheduleView
      canvasEvents={events}
      canvasLoading={loading}
      canvasError={error}
      canvasSyncedAt={syncStatus?.last_canvas_success_at ?? null}
      month={month}
      onMonthChange={setMonth}
      onRetryCanvas={loadCanvas}
      onOpenCanvasEvent={(event) => {
        if (event.assignmentCourseId && event.assignmentId) {
          onNavigateAssignments({
            courseId: event.assignmentCourseId,
            assignmentId: event.assignmentId,
          });
          return;
        }
        onNavigateAssignments();
      }}
      onAskAI={onAskAI}
    />
  );
}

function gradeStatus(submission: Record<string, unknown> | undefined) {
  if (!submission) return "unsubmitted" as const;
  if (submission.missing) return "unsubmitted" as const;
  if (submission.late) return "late" as const;
  if (submission.score != null || submission.grade != null)
    return "graded" as const;
  return submission.workflow_state === "submitted"
    ? ("submitted" as const)
    : ("unsubmitted" as const);
}

function adaptGradebook(result: GradebookResult) {
  const assignments: GradeAssignment[] = result.assignments.map(
    (assignment) => ({
      id: String(assignment.id),
      name: assignment.name ?? `作业 ${assignment.id}`,
      pointsPossible:
        typeof assignment.points_possible === "number"
          ? assignment.points_possible
          : null,
    }),
  );
  const grouped = new Map(
    result.submissions.map((group) => [
      String(group.user_id ?? ""),
      group.submissions ?? [],
    ]),
  );
  const students: StudentGradeRow[] = result.students.map((student) => {
    const studentId = String(student.id);
    const submissions = grouped.get(studentId) ?? [];
    return {
      studentId,
      studentName: student.name ?? `学生 ${studentId}`,
      loginId: student.login_id,
      grades: Object.fromEntries(
        assignments.map((assignment) => {
          const submission = submissions.find(
            (item) => String(item.assignment_id) === assignment.id,
          );
          return [
            assignment.id,
            {
              score:
                typeof submission?.score === "number" ? submission.score : null,
              displayGrade:
                typeof submission?.grade === "string" ? submission.grade : null,
              status: gradeStatus(submission),
              editable: true,
            },
          ];
        }),
      ),
    };
  });
  return { assignments, students };
}

function GradesAdapter({ courseId }: { courseId: number | null }) {
  const [book, setBook] = useState<GradebookResult | null>(null);
  const [loading, setLoading] = useState(Boolean(courseId));
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    if (!courseId) return;
    setLoading(true);
    setError(null);
    try {
      setBook(await getGradebook(courseId));
    } catch (reason) {
      setError(messageFrom(reason, "评分册加载失败"));
    } finally {
      setLoading(false);
    }
  }, [courseId]);
  useEffect(() => void load(), [load]);
  const adapted = useMemo(
    () => (book ? adaptGradebook(book) : { assignments: [], students: [] }),
    [book],
  );
  const save = async (input: GradeSaveInput) => {
    if (!courseId) return;
    await updateGrading(
      courseId,
      Number(input.assignmentId),
      Number(input.studentId),
      { grade: input.score },
    );
    await load();
  };
  return (
    <GradesView
      {...adapted}
      loading={loading}
      error={error}
      permissionDenied={!courseId}
      onRetry={load}
      onSaveGrade={save}
    />
  );
}

function memberRole(member: Record<string, unknown>) {
  const roles = Array.isArray(member.academic_roles)
    ? member.academic_roles.filter(
        (role): role is string => typeof role === "string",
      )
    : [];
  return roles.join("、") || "成员";
}

function memberSection(member: Record<string, unknown>) {
  const enrollments = Array.isArray(member.enrollments)
    ? member.enrollments
    : [];
  for (const item of enrollments) {
    if (!item || typeof item !== "object") continue;
    const enrollment = item as Record<string, unknown>;
    const value = enrollment.course_section_id ?? enrollment.section_id;
    if (typeof value === "string" || typeof value === "number")
      return String(value);
  }
  return null;
}

function RosterAdapter({ courseId }: { courseId: number | null }) {
  const [members, setMembers] = useState<RosterMember[]>([]);
  const [loading, setLoading] = useState(Boolean(courseId));
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    if (!courseId) return;
    setLoading(true);
    setError(null);
    try {
      const result = await getRoster(courseId);
      setMembers(
        result.items.map((member) => ({
          id: String(member.id),
          name: member.name ?? `成员 ${member.id}`,
          sortableName: member.sortable_name,
          loginId: member.login_id,
          email: member.email,
          role: memberRole(member),
          section: memberSection(member),
          joinedAt: member.created_at,
        })),
      );
    } catch (reason) {
      setError(messageFrom(reason, "课程花名册加载失败"));
    } finally {
      setLoading(false);
    }
  }, [courseId]);
  useEffect(() => void load(), [load]);
  const runExport = async (request: RosterExportRequest) => {
    if (!courseId) return;
    const ids = request.members
      .map((member) => Number(member.id))
      .filter((id) => Number.isSafeInteger(id) && id > 0);
    const result = await exportRoster(courseId, ids);
    await revealAcademicExport(result.reveal_token);
  };
  return (
    <RosterView
      members={members}
      loading={loading}
      error={error}
      permissionDenied={!courseId}
      onRetry={load}
      onExport={runExport}
    />
  );
}

function adaptGradingSubmission(
  submission: GradingSubmissionDto,
  index: number,
): GradingSubmission {
  const user =
    submission.user && typeof submission.user === "object"
      ? submission.user
      : {};
  const studentId = String(submission.user_id ?? user.id ?? index + 1);
  const attachments = Array.isArray(submission.attachments)
    ? submission.attachments
    : [];
  const comments = Array.isArray(submission.submission_comments)
    ? submission.submission_comments
    : [];
  const status = submission.missing
    ? "missing"
    : submission.late
      ? "late"
      : submission.score != null || submission.grade != null
        ? "graded"
        : "submitted";
  return {
    id: String(submission.id ?? `${studentId}:${index}`),
    studentId,
    studentName:
      (typeof user.name === "string" && user.name) ||
      (typeof user.display_name === "string" && user.display_name) ||
      `学生 ${studentId}`,
    loginId: typeof user.login_id === "string" ? user.login_id : null,
    submittedAt: submission.submitted_at,
    status,
    score: submission.score,
    displayGrade: submission.grade,
    attempt: submission.attempt,
    body: submission.body,
    attachments: attachments.map((attachment, attachmentIndex) => ({
      id: String(attachment.id ?? attachment.source_id ?? attachmentIndex),
      name:
        attachment.display_name ??
        attachment.filename ??
        `附件 ${attachmentIndex + 1}`,
      size: attachment.size,
      downloadStatus: "ready",
    })),
    comments: comments.map((comment, commentIndex) => ({
      id: String(comment.id ?? commentIndex),
      author: comment.author_name ?? comment.author?.display_name ?? "课程教师",
      content: comment.comment ?? comment.text_comment ?? "",
      createdAt: comment.created_at,
    })),
  };
}

function GradingAdapter({ courseId }: { courseId: number | null }) {
  const [assignments, setAssignments] = useState<GradeAssignment[]>([]);
  const [selectedAssignmentId, setSelectedAssignmentId] = useState<string>("");
  const [assignment, setAssignment] = useState<GradeAssignment | null>(null);
  const [submissions, setSubmissions] = useState<GradingSubmission[]>([]);
  const [loading, setLoading] = useState(Boolean(courseId));
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    if (!courseId) return;
    setLoading(true);
    setError(null);
    try {
      const book = await getGradebook(courseId);
      const availableAssignments = book.assignments.map((item) => ({
        id: String(item.id),
        name: item.name ?? `作业 ${item.id}`,
        pointsPossible:
          typeof item.points_possible === "number"
            ? item.points_possible
            : null,
      }));
      setAssignments(availableAssignments);
      const nextAssignment =
        availableAssignments.find((item) => item.id === selectedAssignmentId) ??
        availableAssignments[0];
      if (!nextAssignment) {
        setSelectedAssignmentId("");
        setAssignment(null);
        setSubmissions([]);
        return;
      }
      if (nextAssignment.id !== selectedAssignmentId)
        setSelectedAssignmentId(nextAssignment.id);
      setAssignment(nextAssignment);
      const result = await getGrading(courseId, Number(nextAssignment.id));
      const items =
        "items" in result && Array.isArray(result.items)
          ? result.items
          : [result as GradingSubmissionDto];
      setSubmissions(items.map(adaptGradingSubmission));
    } catch (reason) {
      setError(messageFrom(reason, "作业提交加载失败"));
    } finally {
      setLoading(false);
    }
  }, [courseId, selectedAssignmentId]);
  useEffect(() => void load(), [load]);
  const update = async (
    studentId: string,
    payload: { grade?: number | null; comment?: string },
  ) => {
    if (!courseId || !assignment) return;
    await updateGrading(
      courseId,
      Number(assignment.id),
      Number(studentId),
      payload,
    );
    await load();
  };
  return (
    <ContentReveal className="section-stack" ready={!loading}>
      {assignments.length > 1 && (
        <div className="finder-toolbar">
          <label>
            <span>作业</span>
            <select
              aria-label="选择要批改的作业"
              value={selectedAssignmentId}
              onChange={(event) => setSelectedAssignmentId(event.target.value)}
            >
              {assignments.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
          </label>
        </div>
      )}
      <GradingView
        assignmentName={assignment?.name}
        maxScore={assignment?.pointsPossible}
        submissions={submissions}
        loading={loading}
        error={error}
        permissionDenied={!courseId}
        onRetry={load}
        onSaveScore={({ studentId, score }) =>
          update(studentId, { grade: score })
        }
        onAddComment={({ studentId, comment }) =>
          update(studentId, { comment })
        }
        onDownloadAttachment={(attachment) =>
          invoke("material_download", { source_id: attachment.id })
        }
      />
    </ContentReveal>
  );
}

function videoDisplayTitle(item: CourseMediaItemDto) {
  const parts: string[] = [];
  if (item.week_number) parts.push(`第${item.week_number}周`);
  if (item.weekday_label) parts.push(item.weekday_label);
  else if (item.weekday)
    parts.push(`周${"一二三四五六日".charAt(item.weekday - 1)}`);
  if (item.lesson_number) parts.push(`第${item.lesson_number}节`);
  if (parts.length) return parts.join(" · ");
  const courseName = item.course_name || "";
  const withoutCourse =
    courseName && item.name.startsWith(courseName)
      ? item.name.slice(courseName.length).trim()
      : item.name;
  return withoutCourse || item.name;
}

function adaptVideo(item: CourseMediaItemDto): CourseVideoItem {
  const subtitle = item.subtitles?.find(
    (candidate) => typeof candidate.source_id === "string",
  );
  return {
    id: item.source_id,
    title: videoDisplayTitle(item),
    originalTitle: item.name,
    courseName: item.course_name ?? "未命名课程",
    source:
      item.source === "video_space" || item.source === "legacy"
        ? item.source
        : "canvas",
    recordedAt: item.recorded_at,
    duration: item.duration,
    teachingClass: item.teaching_class,
    classroom: item.classroom,
    playable: item.playback.available,
    downloadable: item.downloadable !== false,
    subtitleId:
      typeof subtitle?.source_id === "string" ? subtitle.source_id : undefined,
    supportsSubtitle: item.supports_subtitle ?? Boolean(subtitle),
    supportsSlidesPdf: item.supports_slides_pdf ?? false,
  };
}

function VideosAdapter({
  courseId,
  courseName,
  courseOptions,
  onCourseChange,
  courseResolved,
  courseResolutionFailed,
  courseResolutionMessage,
  onResolveCourseRetry,
  refreshVersion,
  readRequest,
}: {
  courseId: number | null;
  courseName: string;
  courseOptions: Array<{ id: number; name: string }>;
  onCourseChange: (courseId: number) => void;
  courseResolved: boolean;
  courseResolutionFailed: boolean;
  courseResolutionMessage: string;
  onResolveCourseRetry: () => void;
  refreshVersion: number;
  readRequest: ReadRequest;
}) {
  const playbackSeq = useRef(new Map<string, number>());
  const [videos, setVideos] = useState<CourseVideoItem[]>([]);
  const [loadedCourseId, setLoadedCourseId] = useState<number | null>(null);
  const [slidesPdfAvailable, setSlidesPdfAvailable] = useState(false);
  const [loading, setLoading] = useState(Boolean(courseId));
  const [error, setError] = useState<string | null>(null);
  const [toolError, setToolError] = useState<string | null>(null);
  const [transcriptError, setTranscriptError] = useState<string | null>(null);
  const [taskRetry, setTaskRetry] = useState(0);
  const [aiReadiness, setAIReadiness] = useState<{
    configured: boolean;
    message: string;
    model: string;
    connectionVerified: boolean;
    status: "checking" | "ready" | "blocked" | "unknown";
  }>({
    status: "checking",
    configured: false,
    message: "正在检查 AI 配置…",
    model: "",
    connectionVerified: false,
  });
  useEffect(() => {
    const controller = new AbortController();
    void readRequest(
      "ai_capabilities:videos",
      () => getAICapabilities("videos"),
      controller.signal,
      "AI 配置读取超时，请检查设置。",
    )
      .then(({ preflight }) => {
        if (controller.signal.aborted) return;
        setAIReadiness({
          status: preflight.ready_for_request ? "ready" : "blocked",
          configured: preflight.ready_for_request,
          message: explainAIReadiness(preflight),
          model: preflight.model,
          connectionVerified: preflight.connection_verified,
        });
      })
      .catch((reason) => {
        if (controller.signal.aborted) return;
        setAIReadiness({
          status: "unknown",
          configured: false,
          message: messageFrom(reason, "AI 配置暂时无法读取，请检查设置。"),
          model: "",
          connectionVerified: false,
        });
      });
    return () => controller.abort();
  }, [readRequest, refreshVersion, taskRetry]);
  const [transcriptJobs, setTranscriptJobs] = useState<TranscriptJob[]>([]);
  const [transcriptCourseId, setTranscriptCourseId] = useState<number | null>(
    null,
  );
  const mediaRequestSequence = useRef(0);
  const mediaWait = useRef<AbortController | null>(null);
  const activeCourseId = useRef(courseId);
  activeCourseId.current = courseId;
  const load = useCallback(async () => {
    const request = ++mediaRequestSequence.current;
    mediaWait.current?.abort();
    const controller = new AbortController();
    mediaWait.current = controller;
    setVideos([]);
    setSlidesPdfAvailable(false);
    setToolError(null);
    setError(null);
    if (!courseId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const result = await readRequest(
        `course_media:${courseId}`,
        () => getCourseMedia(courseId),
        controller.signal,
        "课程录像读取超时，请检查连接后重新加载。",
      );
      if (
        mediaRequestSequence.current !== request ||
        activeCourseId.current !== courseId
      )
        return;
      setLoadedCourseId(courseId);
      setSlidesPdfAvailable(false);
      setVideos(
        result.items
          .filter((item) => item.media_kind === "video")
          .map((item) => {
            const video = adaptVideo(item);
            return {
              ...video,
              supportsSlidesPdf:
                video.source === "video_space"
                  ? video.supportsSlidesPdf
                  : false,
            };
          }),
      );
      // Optional tooling must not delay showing the already loaded videos.
      void readRequest(
        "media_capabilities",
        getMediaCapabilities,
        controller.signal,
        "视频工具能力读取超时。",
      )
        .then((capabilities) => {
          if (
            controller.signal.aborted ||
            mediaRequestSequence.current !== request ||
            activeCourseId.current !== courseId
          )
            return;
          const canCreateSlides = Boolean(
            capabilities.video_screenshot_pdf?.available,
          );
          setSlidesPdfAvailable(canCreateSlides);
          setVideos((current) =>
            current.map((video) =>
              video.source === "canvas"
                ? { ...video, supportsSlidesPdf: canCreateSlides }
                : video,
            ),
          );
        })
        .catch((reason) => {
          if (
            !controller.signal.aborted &&
            mediaRequestSequence.current === request
          ) {
            setToolError(messageFrom(reason, "视频导出工具暂不可用。"));
          }
        });
    } catch (reason) {
      if (
        mediaRequestSequence.current === request &&
        activeCourseId.current === courseId
      )
        setError(messageFrom(reason, "课程视频加载失败"));
    } finally {
      if (
        mediaRequestSequence.current === request &&
        activeCourseId.current === courseId
      )
        setLoading(false);
    }
  }, [courseId, refreshVersion, readRequest]);
  useEffect(() => {
    void load();
    return () => {
      mediaRequestSequence.current += 1;
      mediaWait.current?.abort();
    };
  }, [load]);
  useEffect(() => {
    setTranscriptCourseId(null);
    if (!courseId) {
      setTranscriptJobs([]);
      return;
    }
    let active = true;
    let pending = false;
    const controller = new AbortController();
    const refresh = async () => {
      if (!active || pending || document.hidden) return;
      pending = true;
      try {
        const result = await readRequest(
          `transcript_jobs:${courseId}`,
          () => getTranscriptJobs(courseId),
          controller.signal,
          "学习材料状态读取超时，视频仍可播放。",
        );
        if (active) {
          setTranscriptCourseId(courseId);
          setTranscriptError(null);
          setTranscriptJobs((current) =>
            JSON.stringify(current) === JSON.stringify(result.items)
              ? current
              : result.items,
          );
        }
      } catch (reason) {
        if (active)
          setTranscriptError(
            messageFrom(reason, "学习材料状态读取失败，视频仍可播放。"),
          );
      } finally {
        pending = false;
      }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 2500);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      active = false;
      controller.abort();
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, [courseId, readRequest, taskRetry]);
  const visibleVideos = loadedCourseId === courseId ? videos : [];
  const loadSlides = useCallback(async (video: CourseVideoItem) => {
    const result = await getVideoSlides(video.id);
    return {
      status: result.status,
      message: result.message,
      pages: result.pages.map(({ index, time_ms }) => ({
        index,
        timeMs: time_ms,
      })),
    };
  }, []);
  const loadSlideImage = useCallback(
    async (video: CourseVideoItem, pageIndex: number) =>
      (await getVideoSlideImage(video.id, pageIndex)).data_url,
    [],
  );
  return (
    <>
      {(toolError || transcriptError) && (
        <div className="inline-notice" role="status">
          <span>{transcriptError || toolError}</span>
          <Button
            variant="link"
            size="sm"
            onClick={() => {
              setTaskRetry((value) => value + 1);
              if (toolError) void load();
            }}
          >
            重新检查
          </Button>
        </div>
      )}
      <VideosView
        videos={visibleVideos}
        aiReadiness={aiReadiness}
        onLoadSlides={loadSlides}
        onLoadSlideImage={loadSlideImage}
        courseId={courseId}
        courseName={courseName}
        courseOptions={courseOptions}
        onCourseChange={onCourseChange}
        transcriptJobs={transcriptCourseId === courseId ? transcriptJobs : []}
        loading={
          !courseResolved ||
          (Boolean(courseId) &&
            (loading || (!error && loadedCourseId !== courseId)))
        }
        error={
          courseResolutionFailed
            ? courseResolutionMessage ||
              "暂时无法确认课程视频权限，请重新检查。"
            : error
        }
        permissionDenied={
          courseResolved && !courseResolutionFailed && !courseId
        }
        onRetry={courseResolutionFailed ? onResolveCourseRetry : load}
        onPrepareVideo={async (video, position) => {
          if (
            !courseId ||
            activeCourseId.current !== courseId ||
            !visibleVideos.some((item) => item.id === video.id)
          )
            return;
          const batch = await prepareTranscriptPlayback(
            courseId,
            video.id,
            position.positionSeconds,
            position.playbackRate,
          );
          if (activeCourseId.current !== courseId) return;
          setTranscriptCourseId(courseId);
          setTranscriptJobs((current) => [
            ...batch.jobs,
            ...current.filter(
              (item) => !batch.jobs.some((job) => job.id === item.id),
            ),
          ]);
        }}
        onUpdatePlayback={async (video, position) => {
          if (
            !courseId ||
            activeCourseId.current !== courseId ||
            !visibleVideos.some((item) => item.id === video.id)
          )
            return;
          const seq = nextTranscriptPlaybackSequence(
            playbackSeq.current.get(video.id) ?? 0,
          );
          playbackSeq.current.set(video.id, seq);
          await updateTranscriptPlayback(
            courseId,
            video.id,
            position.positionSeconds,
            position.playbackRate,
            seq,
          );
        }}
        onLoadLearningSegments={async (video, signal) => {
          if (!courseId || !visibleVideos.some((item) => item.id === video.id))
            return { status: "empty", segments: [] };
          const result = await getTranscriptLearningSegments(
            courseId,
            video.id,
            signal,
          );
          return {
            status: result.status,
            message: result.message,
            isProcessing: result.is_processing,
            processingState: result.processing_state,
            processingDone: result.processing_done,
            autoRetryable: result.auto_retryable,
            stage: result.stage,
            errorCode: result.error_code,
            errorMessage: result.error_message,
            retryable: result.retryable,
            retryAfter: result.retry_after,
            updatedAt: result.updated_at,
            sourceRawSha256: result.source_raw_sha256,
            learningRevision: result.learning_revision,
            completedSegments: result.completed_segments,
            failedSegments: result.failed_segments,
            sourceVersion: result.source_version,
            availableRanges: result.available_ranges,
            segments: result.segments.map((segment) => ({
              id: segment.id,
              startMs: segment.start_ms,
              endMs: segment.end_ms,
              title: segment.title,
              content: segment.content,
              kind: segment.kind,
              source: segment.source,
              provisional: segment.provisional,
            })),
          };
        }}
        onStartTranscript={async (selectedVideos) => {
          if (!courseId) throw new Error("请先选择课程。");
          if (
            loadedCourseId !== courseId ||
            selectedVideos.some(
              (selectedVideo) =>
                !visibleVideos.some((video) => video.id === selectedVideo.id),
            )
          )
            throw new Error("课程已切换，请在当前课程重新选择录像。");
          const batch = await startTranscriptBatch(
            courseId,
            selectedVideos.map((video) => video.id),
          );
          if (activeCourseId.current !== courseId) return;
          setTranscriptCourseId(courseId);
          setTranscriptJobs((current) => [...batch.jobs, ...current]);
        }}
        onRetryTranscript={async (job) => {
          const batch = await retryTranscriptJob(job.id);
          if (activeCourseId.current !== courseId) return;
          setTranscriptCourseId(courseId);
          setTranscriptJobs((current) => [...batch.jobs, ...current]);
        }}
        onCancelTranscript={async (job) => {
          const batch = await cancelTranscriptJob(job.id);
          if (activeCourseId.current !== courseId) return;
          setTranscriptCourseId(courseId);
          setTranscriptJobs((current) => [
            ...batch.jobs,
            ...current.filter((item) => item.id !== job.id),
          ]);
        }}
        onRevealTranscript={async (artifact: TranscriptArtifact) => {
          await revealTranscriptArtifact(artifact.id);
        }}
        onPlay={async (video) => {
          const playback = await getVideoPlayback(video.id);
          if (!playback.available) {
            throw new Error("该视频当前不可播放。");
          }
          if (
            playback.action === "play_remote_video" &&
            typeof playback.url === "string"
          ) {
            return { url: playback.url };
          }
          if (playback.action !== "play_course_media") {
            throw new Error("该视频当前不可播放。");
          }
          await invoke("material_open", { source_id: playback.source_id });
          return { kind: "external_opened" as const };
        }}
        onLoadSubtitles={async (video) => {
          try {
            const subtitles = await getVideoSubtitles(video.id);
            return {
              subtitleVtt: subtitles.vtt,
              subtitleStatus: subtitles.status,
              subtitleMessage: subtitles.message,
            };
          } catch (reason) {
            return {
              subtitleStatus: "error" as const,
              subtitleMessage: messageFrom(
                reason,
                "字幕加载失败，请稍后重试。",
              ),
            };
          }
        }}
        onDownload={(video) =>
          invoke("material_download", { source_id: video.id })
        }
        onDownloadSubtitle={(video) => {
          if (!video.subtitleId) throw new Error("该视频没有可下载的字幕。");
          return invoke("material_download", { source_id: video.subtitleId });
        }}
        onCreateSlidesPdf={async (video) => {
          if (video.source !== "video_space" && !slidesPdfAvailable) {
            throw new Error("本地视频截图 PDF 当前不可用。");
          }
          const result =
            video.source === "video_space"
              ? await createVideoSlidesPdf(video.id)
              : await createVideoScreenshotPdf(video.id);
          await revealAcademicExport(result.reveal_token);
          return {
            scope: "video" as const,
            open: async () => {
              await revealAcademicExport(result.reveal_token);
            },
          };
        }}
      />
    </>
  );
}

export default function App() {
  const readRequest = useBoundedReadRequests();
  const [view, setViewState] = useState<ViewName>(initialView);
  const [scheduleMode, setScheduleMode] = useState<ScheduleMode>("calendar");
  const [assignmentTarget, setAssignmentTarget] =
    useState<AssignmentTarget | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [syncStatus, setSyncStatus] = useState<SyncStatus | null>(null);
  const [syncRequested, setSyncRequested] = useState(false);
  const [dataVersion, setDataVersion] = useState(0);
  const [themeMode, setThemeMode] = useState<ThemeMode>("system");
  const [capabilities, setCapabilities] = useState<AppCapabilities | null>(
    null,
  );
  const [capabilitiesInitialized, setCapabilitiesInitialized] = useState(false);
  const [capabilitiesFailed, setCapabilitiesFailed] = useState(false);
  const [capabilitiesError, setCapabilitiesError] = useState("");
  const { showToast } = useToast();
  const availableViews = useMemo(
    () => availableViewsFor(capabilities),
    [capabilities],
  );
  const staffCourses = courseCapabilities(capabilities).filter(
    (course) =>
      course.roles.some((role) => role === "teacher" || role === "ta") &&
      (course.can_manage_grades || course.can_view_submissions),
  );
  const learnerCourses = courseCapabilities(capabilities).filter((course) =>
    course.roles.some((role) => ["student", "teacher", "ta"].includes(role)),
  );
  const [staffCourseId, setStaffCourseId] = useState<number | null>(null);
  const [selectedLearnerCourseId, setSelectedLearnerCourseId] = useState<
    number | null
  >(null);
  const learnerCourseIds = learnerCourses.flatMap((course) => {
    const id = courseNumber(course);
    return id ? [id] : [];
  });
  const learnerCourseId =
    selectedLearnerCourseId &&
    learnerCourseIds.includes(selectedLearnerCourseId)
      ? selectedLearnerCourseId
      : (learnerCourseIds[0] ?? null);

  useEffect(() => {
    const available = staffCourses.flatMap((course) => {
      const id = courseNumber(course);
      return id ? [id] : [];
    });
    if (!staffCourseId || !available.includes(staffCourseId))
      setStaffCourseId(available[0] ?? null);
  }, [capabilities, staffCourseId]);

  const coursePicker = (
    courses: CourseCapabilities[],
    value: number | null,
    onChange: (value: number) => void,
  ) =>
    courses.length > 1 ? (
      <div className="finder-toolbar" aria-label="课程范围">
        <label>
          <span>课程</span>
          <select
            aria-label="选择课程"
            value={value ?? ""}
            onChange={(event) => onChange(Number(event.target.value))}
          >
            {courses.map((course) => (
              <option key={course.course_id} value={course.course_id}>
                {course.course_name || `课程 ${course.course_id}`}
              </option>
            ))}
          </select>
        </label>
      </div>
    ) : null;

  const setView = (next: ViewName) => {
    window.location.hash = `/${next}`;
    setViewState(next);
  };

  const askCurrentPageAI = () => {
    const selection: Record<string, string | number | boolean | null> = {};
    if (view === "calendar") selection.schedule_mode = scheduleMode;
    if (["grades", "roster", "grading"].includes(view) && staffCourseId) {
      selection.course_id = staffCourseId;
    }
    window.localStorage.setItem("ai-chat-pending-prompt", pageAIPrompts[view]);
    window.localStorage.setItem(
      "ai-chat-pending-context",
      JSON.stringify({
        page_id: view,
        source: "page-header",
        title: pageAILabels[view],
        ...(Object.keys(selection).length > 0 ? { selection } : {}),
      }),
    );
    setView("ai-chat");
  };

  const headerActions: ReactNode = (
    <>
      {view === "calendar" && (
        <Tabs
          value={scheduleMode}
          onValueChange={(value) => setScheduleMode(value as ScheduleMode)}
        >
          <TabsList className="calm-seg-list" aria-label="日程视图">
            <TabsTrigger className="calm-seg-trigger" value="calendar">
              月历
            </TabsTrigger>
            <TabsTrigger className="calm-seg-trigger" value="deadlines">
              待处理
            </TabsTrigger>
          </TabsList>
        </Tabs>
      )}
      {view !== "ai-chat" && (
        <Button variant="outline" onClick={askCurrentPageAI}>
          <Sparkles aria-hidden="true" />
          询问 AI
        </Button>
      )}
    </>
  );
  useLayoutEffect(() => applyThemeMode(themeMode), [themeMode]);

  useEffect(() => {
    let active = true;
    void getSettings()
      .then((settings) => {
        if (active) setThemeMode(settings.theme_mode);
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    setCapabilitiesInitialized(false);
    setCapabilitiesFailed(false);
    setCapabilitiesError("");
    void readRequest(
      "capabilities",
      () => invoke<AppCapabilities>("capabilities"),
      controller.signal,
      "课程权限确认超时，请重新检查。",
    )
      .then((result) => {
        if (active) {
          setCapabilities(result);
          setCapabilitiesInitialized(true);
        }
      })
      .catch((reason) => {
        if (active) {
          setCapabilities(null);
          setCapabilitiesFailed(true);
          setCapabilitiesError(
            messageFrom(reason, "暂时无法确认课程视频权限，请重新检查。"),
          );
          setCapabilitiesInitialized(true);
        }
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [dataVersion, readRequest]);

  useEffect(() => {
    if (capabilities && !availableViews.includes(view)) setView("overview");
  }, [availableViews, capabilities, view]);

  useEffect(() => {
    const onHash = () => setViewState(initialView());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const statusRequestPending = useRef(false);
  const loadStatus = useCallback(async () => {
    if (statusRequestPending.current) return;
    statusRequestPending.current = true;
    try {
      const next = await invoke<SyncStatus>("sync_status");
      setSyncStatus((current) =>
        JSON.stringify(current) === JSON.stringify(next) ? current : next,
      );
      if (syncRequested && next.status === "idle") {
        setSyncRequested(false);
        showToast({
          id: "sync-operation",
          kind: next.last_run_status === "success" ? "success" : "error",
          message:
            next.last_run_status === "success"
              ? "同步完成，数据已更新。"
              : "同步失败，请查看最近运行状态。",
        });
        setDataVersion((value) => value + 1);
      }
    } catch {
      setSyncStatus(null);
    } finally {
      statusRequestPending.current = false;
    }
  }, [showToast, syncRequested]);

  useEffect(() => {
    let active = true;
    let pending = false;
    const refresh = async () => {
      if (!active || pending || document.hidden) return;
      pending = true;
      try {
        await loadStatus();
      } finally {
        pending = false;
      }
    };
    void refresh();
    const timer = window.setInterval(
      () => void refresh(),
      syncRequested || syncStatus?.status === "syncing" ? 1200 : 5000,
    );
    document.addEventListener("visibilitychange", refresh);
    return () => {
      active = false;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, [loadStatus, syncRequested, syncStatus?.status]);

  const triggerSync = async () => {
    setSyncRequested(true);
    showToast({
      id: "sync-operation",
      kind: "info",
      message: "正在提交同步请求…",
      duration: 0,
    });
    try {
      const result = await invoke<{ status: string }>("sync_trigger");
      showToast({
        id: "sync-operation",
        kind: "info",
        message:
          result.status === "already_running"
            ? "同步已在运行。"
            : "同步请求已接受，正在后台执行。",
        duration: 0,
      });
    } catch (reason) {
      setSyncRequested(false);
      showToast({
        id: "sync-operation",
        kind: "error",
        message: reason instanceof Error ? reason.message : "同步触发失败",
      });
    }
  };

  const syncing = syncRequested || syncStatus?.status === "syncing";
  return (
    <WorkspaceTransition mode={view === "ai-chat" ? "assistant" : "workbench"}>
      {view === "ai-chat" ? (
        <AIChatView onBack={() => setView("overview")} />
      ) : (
        <AppShell
          view={view}
          availableViews={availableViews}
          setView={setView}
          drawerOpen={drawerOpen}
          setDrawerOpen={setDrawerOpen}
          syncStatus={syncStatus}
          syncing={syncing}
          onSync={() => void triggerSync()}
          headerActions={headerActions}
        >
          <div key={view} className="view-transition">
            {view === "overview" && (
              <OverviewView key={dataVersion} navigate={setView} />
            )}
            {view === "calendar" && (
              <ScheduleAdapter
                key={dataVersion}
                mode={scheduleMode}
                syncStatus={syncStatus}
                onModeChange={setScheduleMode}
                onNavigateAssignments={(target) => {
                  setAssignmentTarget(target ?? null);
                  setView("assignments");
                }}
                onAskAI={(prompt) => {
                  window.localStorage.setItem("ai-chat-pending-prompt", prompt);
                  window.localStorage.setItem(
                    "ai-chat-pending-context",
                    JSON.stringify({
                      page_id: "calendar",
                      source: "calendar-selection",
                      title: "日程",
                      selection: { schedule_mode: scheduleMode },
                    }),
                  );
                  setView("ai-chat");
                }}
              />
            )}
            {view === "messages" && <MessagesView key={dataVersion} />}
            {view === "assignments" && (
              <AssignmentsView
                key={dataVersion}
                initialTarget={assignmentTarget}
                onInitialTargetHandled={() => setAssignmentTarget(null)}
              />
            )}
            {view === "materials" && <MaterialsView key={dataVersion} />}
            {view === "grades" && (
              <div className="section-stack">
                {coursePicker(staffCourses, staffCourseId, setStaffCourseId)}
                <GradesAdapter
                  key={`${dataVersion}:${staffCourseId}`}
                  courseId={staffCourseId}
                />
              </div>
            )}
            {view === "roster" && (
              <div className="section-stack">
                {coursePicker(staffCourses, staffCourseId, setStaffCourseId)}
                <RosterAdapter
                  key={`${dataVersion}:${staffCourseId}`}
                  courseId={staffCourseId}
                />
              </div>
            )}
            {view === "grading" && (
              <div className="section-stack">
                {coursePicker(staffCourses, staffCourseId, setStaffCourseId)}
                <GradingAdapter
                  key={`${dataVersion}:${staffCourseId}`}
                  courseId={staffCourseId}
                />
              </div>
            )}
            {view === "videos" && (
              <VideosAdapter
                courseId={learnerCourseId}
                courseName={
                  learnerCourses.find(
                    (course) => courseNumber(course) === learnerCourseId,
                  )?.course_name || "未命名课程"
                }
                courseOptions={learnerCourses.flatMap((course) => {
                  const id = courseNumber(course);
                  return id
                    ? [
                        {
                          id,
                          name:
                            course.course_name || `课程 ${course.course_id}`,
                        },
                      ]
                    : [];
                })}
                onCourseChange={setSelectedLearnerCourseId}
                readRequest={readRequest}
                courseResolved={capabilitiesInitialized}
                courseResolutionFailed={capabilitiesFailed}
                courseResolutionMessage={capabilitiesError}
                onResolveCourseRetry={() =>
                  setDataVersion((value) => value + 1)
                }
                refreshVersion={dataVersion}
              />
            )}
            {view === "backup" && <BackupView />}
            {view === "local-projects" && (
              <LocalProjectsView
                onCompile={(project) => {
                  window.localStorage.setItem(
                    "knowledge-compiler-source",
                    project.source_root,
                  );
                  window.localStorage.setItem(
                    "knowledge-compiler-target",
                    project.target_root,
                  );
                  setView("knowledge");
                }}
              />
            )}
            {view === "knowledge" && <KnowledgeCompilerView />}
            {view === "settings" && (
              <SettingsView
                onArchiveChanged={() => setDataVersion((value) => value + 1)}
                onThemeModeChange={setThemeMode}
              />
            )}
          </div>
        </AppShell>
      )}
    </WorkspaceTransition>
  );
}
