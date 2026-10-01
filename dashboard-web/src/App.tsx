import { motion, useReducedMotion } from "motion/react";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { AIChatView } from "@/components/AIChatView";
import { AppShell } from "@/components/AppShell";
import { AssignmentsView } from "@/components/AssignmentsView";
import { BackupView } from "@/components/BackupView";
import type { CalendarEventItem } from "@/components/CalendarView";
import { DeadlinesView } from "@/components/DeadlinesView";
import {
  type GradeAssignment,
  type GradeSaveInput,
  GradesView,
  type StudentGradeRow,
} from "@/components/GradesView";
import { type GradingSubmission, GradingView } from "@/components/GradingView";
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/Tabs";
import { type CourseVideoItem, VideosView } from "@/components/VideosView";
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
  getVideoPlayback,
  getVideoSubtitles,
  invoke,
  openExternal,
  retryTranscriptJob,
  revealAcademicExport,
  revealTranscriptArtifact,
  startTranscriptBatch,
  updateGrading,
} from "@/lib/api";
import { motionDuration, motionEase } from "@/lib/motion";
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
  "ai-chat",
  "settings",
];

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

function adaptCalendarEvent(
  event: CalendarEventDto,
  index: number,
): CalendarEventItem | null {
  const startAt = eventDate(event);
  if (!startAt) return null;
  const assignment = event.assignment ?? {};
  const assignmentName = assignment.name;
  const contextCode = event.context_code ?? "Canvas";
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
    url:
      typeof event.html_url === "string"
        ? event.html_url
        : typeof assignment.html_url === "string"
          ? assignment.html_url
          : null,
  };
}

function ScheduleAdapter({
  onNavigateAssignments,
}: {
  onNavigateAssignments: () => void;
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

  return (
    <div className="section-stack">
      <Tabs defaultValue="calendar">
        <div className="message-toolbar">
          <TabsList aria-label="日程视图">
            <TabsTrigger value="calendar">月历</TabsTrigger>
            <TabsTrigger value="deadlines">待处理</TabsTrigger>
          </TabsList>
        </div>
        <TabsContent value="calendar">
          <ScheduleView
            canvasEvents={events}
            canvasLoading={loading}
            canvasError={error}
            month={month}
            onMonthChange={setMonth}
            onRetryCanvas={loadCanvas}
            onOpenCanvasEvent={(event) => {
              if (event.url) void openExternal(event.url);
              else onNavigateAssignments();
            }}
          />
        </TabsContent>
        <TabsContent value="deadlines">
          <DeadlinesView embedded />
        </TabsContent>
      </Tabs>
    </div>
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
    <div className="section-stack">
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
    </div>
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
  onResolveCourseRetry,
  refreshVersion,
}: {
  courseId: number | null;
  courseName: string;
  courseOptions: Array<{ id: number; name: string }>;
  onCourseChange: (courseId: number) => void;
  courseResolved: boolean;
  courseResolutionFailed: boolean;
  onResolveCourseRetry: () => void;
  refreshVersion: number;
}) {
  const [videos, setVideos] = useState<CourseVideoItem[]>([]);
  const [loadedCourseId, setLoadedCourseId] = useState<number | null>(null);
  const [slidesPdfAvailable, setSlidesPdfAvailable] = useState(false);
  const [loading, setLoading] = useState(Boolean(courseId));
  const [error, setError] = useState<string | null>(null);
  const [transcriptJobs, setTranscriptJobs] = useState<TranscriptJob[]>([]);
  const [transcriptCourseId, setTranscriptCourseId] = useState<number | null>(
    null,
  );
  const mediaRequestSequence = useRef(0);
  const activeCourseId = useRef(courseId);
  activeCourseId.current = courseId;
  const load = useCallback(async () => {
    const request = ++mediaRequestSequence.current;
    setVideos([]);
    setSlidesPdfAvailable(false);
    setError(null);
    if (!courseId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const result = await getCourseMedia(courseId);
      if (
        mediaRequestSequence.current !== request ||
        activeCourseId.current !== courseId
      )
        return;
      let canCreateSlides = false;
      try {
        const mediaCapabilities = await getMediaCapabilities();
        if (
          mediaRequestSequence.current !== request ||
          activeCourseId.current !== courseId
        )
          return;
        canCreateSlides = Boolean(
          mediaCapabilities.video_screenshot_pdf?.available,
        );
      } catch {
        // Optional local tooling must not prevent the course video list loading.
      }
      if (
        mediaRequestSequence.current !== request ||
        activeCourseId.current !== courseId
      )
        return;
      setLoadedCourseId(courseId);
      setSlidesPdfAvailable(canCreateSlides);
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
                  : video.source === "canvas" && canCreateSlides,
            };
          }),
      );
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
  }, [courseId, refreshVersion]);
  useEffect(() => void load(), [load]);
  useEffect(() => {
    setTranscriptCourseId(null);
    if (!courseId) {
      setTranscriptJobs([]);
      return;
    }
    let active = true;
    const refresh = async () => {
      try {
        const result = await getTranscriptJobs(courseId);
        if (active) {
          setTranscriptCourseId(courseId);
          setTranscriptJobs(result.items);
        }
      } catch {
        // Video loading and playback stay available if transcript recovery fails.
      }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 2500);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [courseId]);
  const visibleVideos = loadedCourseId === courseId ? videos : [];
  return (
    <VideosView
      videos={visibleVideos}
      courseId={courseId}
      courseName={courseName}
      courseOptions={courseOptions}
      onCourseChange={onCourseChange}
      transcriptJobs={transcriptCourseId === courseId ? transcriptJobs : []}
      loading={
        !courseResolved ||
        (Boolean(courseId) && (loading || loadedCourseId !== courseId))
      }
      error={
        courseResolutionFailed
          ? "暂时无法确认课程视频权限，请重新检查。"
          : error
      }
      permissionDenied={courseResolved && !courseResolutionFailed && !courseId}
      onRetry={courseResolutionFailed ? onResolveCourseRetry : load}
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
        setTranscriptJobs((current) => [...batch.jobs, ...current]);
      }}
      onRetryTranscript={async (job) => {
        const batch = await retryTranscriptJob(job.id);
        setTranscriptJobs((current) => [...batch.jobs, ...current]);
      }}
      onCancelTranscript={async (job) => {
        const batch = await cancelTranscriptJob(job.id);
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
            subtitleMessage: messageFrom(reason, "字幕加载失败，请稍后重试。"),
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
  );
}

export default function App() {
  const [view, setViewState] = useState<ViewName>(initialView);
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
  const { showToast } = useToast();
  const shouldReduceMotion = useReducedMotion();
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
    setCapabilitiesInitialized(false);
    setCapabilitiesFailed(false);
    void invoke<AppCapabilities>("capabilities")
      .then((result) => {
        if (active) {
          setCapabilities(result);
          setCapabilitiesInitialized(true);
        }
      })
      .catch(() => {
        if (active) {
          setCapabilities(null);
          setCapabilitiesFailed(true);
          setCapabilitiesInitialized(true);
        }
      });
    return () => {
      active = false;
    };
  }, [dataVersion]);

  useEffect(() => {
    if (capabilities && !availableViews.includes(view)) setView("overview");
  }, [availableViews, capabilities, view]);

  useEffect(() => {
    const onHash = () => setViewState(initialView());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const loadStatus = useCallback(async () => {
    try {
      const next = await invoke<SyncStatus>("sync_status");
      setSyncStatus(next);
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
    }
  }, [showToast, syncRequested]);

  useEffect(() => {
    void loadStatus();
    const timer = window.setInterval(
      () => void loadStatus(),
      syncRequested || syncStatus?.status === "syncing" ? 1200 : 5000,
    );
    return () => window.clearInterval(timer);
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
  if (view === "ai-chat") {
    return <AIChatView onBack={() => setView("overview")} />;
  }
  return (
    <AppShell
      view={view}
      availableViews={availableViews}
      setView={setView}
      drawerOpen={drawerOpen}
      setDrawerOpen={setDrawerOpen}
      syncStatus={syncStatus}
      syncing={syncing}
      onSync={() => void triggerSync()}
    >
      <motion.div
        key={view}
        className="view-transition"
        initial={shouldReduceMotion ? false : { opacity: 0.72, y: 4 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{
          duration: motionDuration.enter,
          ease: motionEase.out,
        }}
      >
        {view === "overview" && (
          <OverviewView key={dataVersion} navigate={setView} />
        )}
        {view === "calendar" && (
          <ScheduleAdapter
            key={dataVersion}
            onNavigateAssignments={() => setView("assignments")}
          />
        )}
        {view === "messages" && <MessagesView key={dataVersion} />}
        {view === "assignments" && <AssignmentsView key={dataVersion} />}
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
                      name: course.course_name || `课程 ${course.course_id}`,
                    },
                  ]
                : [];
            })}
            onCourseChange={setSelectedLearnerCourseId}
            courseResolved={capabilitiesInitialized}
            courseResolutionFailed={capabilitiesFailed}
            onResolveCourseRetry={() => setDataVersion((value) => value + 1)}
            refreshVersion={dataVersion}
          />
        )}
        {view === "backup" && <BackupView />}
        {view === "settings" && (
          <SettingsView
            onArchiveChanged={() => setDataVersion((value) => value + 1)}
            onThemeModeChange={setThemeMode}
          />
        )}
      </motion.div>
    </AppShell>
  );
}
