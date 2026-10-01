import {
  Captions,
  Check,
  ChevronDown,
  CircleAlert,
  CircleCheck,
  CircleDashed,
  Download,
  FileText,
  LoaderCircle,
  MoreHorizontal,
  NotebookPen,
  Play,
  X,
  XCircle,
} from "lucide-react";
import {
  type KeyboardEvent as ReactKeyboardEvent,
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import { useToast } from "@/components/Toast";
import { TranscriptDetailDrawer } from "@/components/TranscriptDetailDrawer";
import { TranscriptQualityWarning } from "@/components/TranscriptPhase1Panels";
import { Button } from "@/components/ui/Button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/Tabs";
import type { TranscriptArtifact, TranscriptJob } from "@/lib/types";

export type VideoSource = "canvas" | "video_space" | "legacy";
export type VideoTaskKind = "video" | "subtitle" | "slides_pdf";
export type VideoTaskStatus =
  | "queued"
  | "running"
  | "cancelling"
  | "completed"
  | "partial"
  | "failed"
  | "interrupted"
  | "cancelled";

export interface CourseVideoItem {
  id: string;
  title: string;
  courseName: string;
  source: VideoSource;
  recordedAt?: string | null;
  duration?: number | null;
  teachingClass?: string | null;
  classroom?: string | null;
  originalTitle?: string | null;
  playable?: boolean;
  downloadable?: boolean;
  playbackUrl?: string | null;
  subtitleUrl?: string | null;
  subtitleId?: string;
  supportsSubtitle?: boolean;
  supportsSlidesPdf?: boolean;
}

export interface VideoTaskItem {
  id: string;
  videoId: string;
  title: string;
  kind: VideoTaskKind;
  status: VideoTaskStatus;
  progress?: number | null;
  message?: string | null;
  createdAt?: string | null;
  updatedAt?: string | null;
}

export interface VideoPlaybackResult {
  url?: string | null;
  subtitleUrl?: string | null;
  subtitleVtt?: string | null;
  subtitleStatus?: "ready" | "empty" | "processing" | "error";
  subtitleMessage?: string | null;
}

export interface SlidesPdfResult {
  url?: string | null;
  fileUrl?: string | null;
  scope?: "video" | "course";
  open?: () => void | Promise<void>;
}

export interface VideosViewProps {
  videos: CourseVideoItem[];
  tasks?: VideoTaskItem[];
  loading?: boolean;
  error?: string | null;
  permissionDenied?: boolean;
  courseId?: number | null;
  courseName?: string;
  courseOptions?: Array<{ id: number; name: string }>;
  onCourseChange?: (courseId: number) => void;
  transcriptJobs?: TranscriptJob[];
  onRetry?: () => void | Promise<void>;
  onPlay?: (
    video: CourseVideoItem,
  ) =>
    | VideoPlaybackResult
    | string
    | void
    | Promise<VideoPlaybackResult | string | void>;
  onLoadSubtitles?: (
    video: CourseVideoItem,
  ) => VideoPlaybackResult | Promise<VideoPlaybackResult>;
  onDownload?: (video: CourseVideoItem) => void | Promise<void>;
  onDownloadSubtitle?: (video: CourseVideoItem) => void | Promise<void>;
  onCreateSlidesPdf?: (
    video: CourseVideoItem,
  ) => void | SlidesPdfResult | Promise<void | SlidesPdfResult>;
  onCancelTask?: (task: VideoTaskItem) => void | Promise<void>;
  onStartTranscript?: (videos: CourseVideoItem[]) => void | Promise<void>;
  onRetryTranscript?: (job: TranscriptJob) => void | Promise<void>;
  onCancelTranscript?: (job: TranscriptJob) => void | Promise<void>;
  onRevealTranscript?: (artifact: TranscriptArtifact) => void | Promise<void>;
}

const sourceLabels: Record<VideoSource, string> = {
  canvas: "Canvas",
  video_space: "视频空间",
  legacy: "旧版课堂",
};
const taskKindLabels: Record<VideoTaskKind, string> = {
  video: "视频下载",
  subtitle: "字幕下载",
  slides_pdf: "课件 PDF",
};
type UnifiedStatus =
  | "completed"
  | "processing"
  | "pending"
  | "queued"
  | "transcribing"
  | "generating"
  | "reviewing"
  | "partial"
  | "failed";
type LearningTab = "transcript" | "summary" | "pdf" | "notes";
type VideoMenuPosition = {
  top: number;
  left: number;
  direction: "up" | "down";
};
type OrganizationFilter =
  | "all"
  | "pending"
  | "processing"
  | "completed"
  | "partial"
  | "failed";
const statusCopy: Record<UnifiedStatus, string> = {
  completed: "已整理",
  processing: "处理中",
  pending: "未整理",
  queued: "排队中",
  transcribing: "正在生成字幕",
  generating: "正在生成讲义",
  reviewing: "正在对照审校",
  partial: "部分完成",
  failed: "失败可重试",
};
const playbackRates = [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2] as const;
type PlaybackRate = (typeof playbackRates)[number];
const temporaryPlaybackRates = [1.5, 2, 3, 4] as const;
type TemporaryPlaybackRate = (typeof temporaryPlaybackRates)[number];
type TemporaryPlaybackDirection = "forward" | "rewind";
type TemporaryPlaybackState = {
  key: "arrowleft" | "arrowright";
  direction: TemporaryPlaybackDirection;
  media: HTMLVideoElement;
  holdTimer: number | null;
  rewindTimer: number | null;
  active: boolean;
  speed: TemporaryPlaybackRate;
  basePlaybackRate: number;
  wasPaused: boolean;
};
const longPressDelay = 350;
const rewindIntervalMs = 50;
type JobCategory = "active" | "attention" | "history";
type TimestampedJob = {
  createdAt?: string | null;
  updatedAt?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
};
type UnifiedJobItem = {
  id: string;
  sourceId: string;
  title: string;
  kind: string;
  status: UnifiedStatus;
  rawStatus: string;
  progress: number;
  category: JobCategory;
  timestamp: number;
  order: number;
  transcript?: TranscriptJob;
  task?: VideoTaskItem;
};

type TranscriptJobWithTime = TranscriptJob & {
  created_at?: string | null;
  updated_at?: string | null;
};

type VideoLearningState = {
  status: UnifiedStatus;
  complete: boolean;
  processing: boolean;
  partial: boolean;
  failed: boolean;
  hasHistoricalComplete: boolean;
  label?: string;
};

function jobTimestamp(job: TimestampedJob) {
  for (const value of [
    job.updatedAt,
    job.updated_at,
    job.createdAt,
    job.created_at,
  ]) {
    if (!value) continue;
    const timestamp = new Date(value).getTime();
    if (!Number.isNaN(timestamp)) return timestamp;
  }
  return 0;
}

function isActiveTranscriptStatus(status: string, progress: number) {
  return (
    progress < 100 &&
    Array.of(
      "queued",
      "fetching",
      "saved",
      "waiting_remote",
      "waiting_for_ai",
      "organizing",
      "reviewing",
    ).includes(status)
  );
}

function jobCategory(status: string, progress = 0): JobCategory {
  if (
    progress < 100 &&
    [
      "queued",
      "fetching",
      "saved",
      "waiting_remote",
      "waiting_for_ai",
      "organizing",
      "reviewing",
      "running",
      "cancelling",
    ].includes(status)
  )
    return "active";
  if (["completed", "cancelled", "canceled", "interrupted"].includes(status))
    return "history";
  if (
    ["failed", "partial", "saved", "completed_with_warnings"].includes(status)
  )
    return "attention";
  return "history";
}
function compareTranscriptJobs(left: TranscriptJob, right: TranscriptJob) {
  const leftTimestamp = jobTimestamp(left as TranscriptJobWithTime);
  const rightTimestamp = jobTimestamp(right as TranscriptJobWithTime);
  if (!leftTimestamp || !rightTimestamp) return 0;
  return rightTimestamp - leftTimestamp;
}

export function latestTranscriptJobs(jobs: TranscriptJob[]) {
  const latest = new Map<string, TranscriptJob>();
  for (const job of [...jobs].sort(compareTranscriptJobs)) {
    if (!latest.has(job.source_id)) latest.set(job.source_id, job);
  }
  return latest;
}

export function transcriptQualityRisk(job?: TranscriptJob) {
  const quality = job?.quality;
  if (!quality) return false;
  const unresolvedRate =
    quality.metrics.unresolved_rate ??
    quality.metrics.pending_confirmation_rate ??
    quality.uncertain_rate;
  return (
    quality.schema_pass === false ||
    quality.critic_pass_rate === 0 ||
    unresolvedRate >= 0.8
  );
}

export function deriveVideoLearningState(
  job?: TranscriptJob,
  history: TranscriptJob[] = job ? [job] : [],
): VideoLearningState {
  const hasHistoricalComplete = history.some(
    (item) => item.id !== job?.id && item.status === "completed",
  );
  if (!job)
    return {
      status: "pending",
      complete: false,
      processing: false,
      partial: false,
      failed: false,
      hasHistoricalComplete,
    };
  const rawStatus = job.status as string;
  const phase1Status = job.phase1_status ?? job.pipeline_status;
  const failed = ["failed", "interrupted"].includes(rawStatus);
  const processing = isActiveTranscriptStatus(rawStatus, job.progress);
  const partial =
    !failed &&
    !processing &&
    (rawStatus === "completed_with_warnings" ||
      rawStatus === "partial" ||
      phase1Status === "completed_with_warnings" ||
      phase1Status === "partial" ||
      phase1Status === "failed" ||
      job.partial_warning === true ||
      transcriptQualityRisk(job));
  const complete = rawStatus === "completed" && !partial;
  return {
    status: failed
      ? "failed"
      : partial
        ? "partial"
        : complete
          ? "completed"
          : processing
            ? transcriptStatus(job)
            : "pending",
    complete,
    processing,
    partial,
    failed,
    hasHistoricalComplete,
    label: failed && hasHistoricalComplete ? "已有材料 · 更新失败" : undefined,
  };
}

function isInteractiveTarget(target: EventTarget | null) {
  if (!(target instanceof Element)) return false;
  if (
    target instanceof HTMLElement &&
    (target.isContentEditable || target.contentEditable === "true")
  )
    return true;
  return Boolean(
    target.closest(
      'input, textarea, select, button, a, summary, [contenteditable], [role="button"], [role="link"], [role="menu"], [role="menuitem"], [role="menuitemradio"], [role="combobox"], [role="listbox"], [role="option"]',
    ),
  );
}

function formatPlaybackRate(rate: PlaybackRate) {
  return `${rate}×`;
}

function compactJobTitle(title: string, courseName: string) {
  const course = courseName.trim();
  if (!course || !title.startsWith(course)) return title;
  const compact = title
    .slice(course.length)
    .replace(/^\s*[·｜|:：\-–—]\s*/, "")
    .trim();
  return compact || title;
}

function isCompletedForBatch(job?: TranscriptJob) {
  return deriveVideoLearningState(job).complete;
}

function transcriptStatus(job?: TranscriptJob): UnifiedStatus {
  if (!job) return "pending";
  if (transcriptQualityRisk(job)) return "partial";
  if (job.status === "completed_with_warnings") return "partial";
  if (job.status === "completed") return "completed";
  if (["partial"].includes(job.status)) return "partial";
  if (["failed", "interrupted"].includes(job.status)) return "failed";
  if (job.status === "queued" || job.stage === "queued") return "queued";
  if (job.stage === "reviewing") return "reviewing";
  if (
    ["transcribing", "fetching", "subtitle", "subtitling"].includes(
      job.stage,
    ) ||
    ["fetching", "saved", "waiting_remote"].includes(job.status)
  )
    return "transcribing";
  if (
    ["generating", "organizing", "summary", "phase1"].includes(job.stage) ||
    ["waiting_for_ai", "organizing"].includes(job.status)
  )
    return "generating";
  return "pending";
}

function transcriptIsProcessing(job?: TranscriptJob) {
  return Boolean(job && isActiveTranscriptStatus(job.status, job.progress));
}

function transcriptIsComplete(job?: TranscriptJob) {
  return isCompletedForBatch(job);
}

function phase1IsReady(job?: TranscriptJob) {
  const status = job?.phase1_status ?? job?.pipeline_status;
  return status === "completed" || status === "completed_with_warnings";
}

function transcriptNeedsRetry(job?: TranscriptJob) {
  if (!job) return false;
  const phase1Status = job.phase1_status ?? job.pipeline_status;
  return (
    ["partial", "failed", "interrupted", "completed_with_warnings"].includes(
      job.status,
    ) ||
    phase1Status === "partial" ||
    phase1Status === "failed" ||
    transcriptQualityRisk(job)
  );
}

function taskPdfResult(task?: VideoTaskItem): SlidesPdfResult | undefined {
  if (!task || task.kind !== "slides_pdf" || task.status !== "completed")
    return undefined;
  const value = task as VideoTaskItem & {
    result?: { url?: string; file_url?: string; scope?: "video" | "course" };
    url?: string;
    fileUrl?: string;
    file_url?: string;
    scope?: "video" | "course";
  };
  return {
    url:
      value.result?.url ??
      value.result?.file_url ??
      value.url ??
      value.fileUrl ??
      value.file_url,
    scope: value.result?.scope ?? value.scope ?? "video",
  };
}

function taskStatus(status: VideoTaskStatus): UnifiedStatus {
  if (status === "completed") return "completed";
  if (status === "partial") return "partial";
  if (status === "failed" || status === "interrupted") return "failed";
  if (status === "running" || status === "cancelling") return "processing";
  return "pending";
}

function StatusBadge({ status }: { status: UnifiedStatus }) {
  const Icon =
    status === "completed"
      ? CircleCheck
      : status === "processing"
        ? LoaderCircle
        : status === "failed"
          ? XCircle
          : status === "partial"
            ? CircleAlert
            : CircleDashed;
  return (
    <span className={`video-status video-status-${status}`}>
      <Icon aria-hidden="true" />
      {statusCopy[status]}
    </span>
  );
}

function formatDuration(seconds?: number | null) {
  if (seconds === null || seconds === undefined) return "时长未知";
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return hours
    ? `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`
    : `${minutes}:${String(seconds % 60).padStart(2, "0")}`;
}

function formatRecordedAt(value?: string | null) {
  if (!value) return "录制时间未知";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

function findScrollContainer(element: HTMLElement): HTMLElement | Window {
  let parent = element.parentElement;
  while (parent) {
    const { overflow, overflowY } = window.getComputedStyle(parent);
    if (/(auto|scroll|overlay)/.test(`${overflow} ${overflowY}`)) return parent;
    parent = parent.parentElement;
  }
  return element.closest<HTMLElement>(".workspace") ?? window;
}

function CoursePicker({
  value,
  options,
  fallback,
  onChange,
}: {
  value?: number | null;
  options: Array<{ id: number; name: string }>;
  fallback: string;
  onChange?: (courseId: number) => void;
}) {
  const id = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const optionRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const selectedIndex = Math.max(
    0,
    options.findIndex((option) => option.id === value),
  );
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(selectedIndex);
  const current = options[selectedIndex];

  useEffect(() => setActiveIndex(selectedIndex), [selectedIndex]);
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [open]);
  useEffect(() => {
    if (!open || !rootRef.current) return;
    const closeOnPositionChange = () => setOpen(false);
    const scrollContainer = findScrollContainer(rootRef.current);
    const visualViewport = window.visualViewport;
    scrollContainer.addEventListener("scroll", closeOnPositionChange, {
      passive: true,
    });
    window.addEventListener("resize", closeOnPositionChange);
    visualViewport?.addEventListener("resize", closeOnPositionChange);
    visualViewport?.addEventListener("scroll", closeOnPositionChange);
    return () => {
      scrollContainer.removeEventListener("scroll", closeOnPositionChange);
      window.removeEventListener("resize", closeOnPositionChange);
      visualViewport?.removeEventListener("resize", closeOnPositionChange);
      visualViewport?.removeEventListener("scroll", closeOnPositionChange);
    };
  }, [open]);
  useEffect(() => {
    if (open) optionRefs.current[activeIndex]?.focus();
  }, [activeIndex, open]);

  const close = (restoreFocus = false) => {
    setOpen(false);
    if (restoreFocus)
      window.requestAnimationFrame(() => triggerRef.current?.focus());
  };
  const select = (index: number) => {
    const option = options[index];
    if (option) onChange?.(option.id);
    close(true);
  };
  const move = (index: number) => {
    if (options.length)
      setActiveIndex((index + options.length) % options.length);
  };
  const onTriggerKeyDown = (event: ReactKeyboardEvent<HTMLButtonElement>) => {
    if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      setActiveIndex(
        event.key === "ArrowUp" || event.key === "End"
          ? options.length - 1
          : event.key === "Home"
            ? 0
            : selectedIndex,
      );
      setOpen(true);
    }
    if (event.key === "Escape" && open) {
      event.preventDefault();
      close(true);
    }
  };
  const onOptionKeyDown = (event: ReactKeyboardEvent<HTMLButtonElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      close(true);
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      move(activeIndex + 1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      move(activeIndex - 1);
    } else if (event.key === "Home") {
      event.preventDefault();
      move(0);
    } else if (event.key === "End") {
      event.preventDefault();
      move(options.length - 1);
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      select(activeIndex);
    }
  };

  if (options.length <= 1)
    return (
      <strong className="video-course-readonly">
        {current?.name ?? fallback}
      </strong>
    );
  return (
    <div className="video-course-picker" ref={rootRef}>
      <button
        ref={triggerRef}
        type="button"
        className="video-course-trigger"
        role="combobox"
        aria-label="选择课程"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={`${id}-options`}
        onClick={() => setOpen((currentOpen) => !currentOpen)}
        onKeyDown={onTriggerKeyDown}
      >
        <span title={current?.name ?? fallback}>
          {current?.name ?? fallback}
        </span>
        <ChevronDown aria-hidden="true" />
      </button>
      {open && (
        <div
          id={`${id}-options`}
          className="video-course-popup"
          role="listbox"
          aria-label="课程列表"
          aria-activedescendant={`${id}-option-${options[activeIndex]?.id}`}
        >
          {options.map((option, index) => (
            <button
              key={option.id}
              ref={(node) => {
                optionRefs.current[index] = node;
              }}
              id={`${id}-option-${option.id}`}
              type="button"
              role="option"
              aria-label={option.name}
              aria-selected={option.id === value}
              tabIndex={index === activeIndex ? 0 : -1}
              className={option.id === value ? "is-selected" : ""}
              title={option.name}
              onFocus={() => setActiveIndex(index)}
              onMouseEnter={() => setActiveIndex(index)}
              onKeyDown={onOptionKeyDown}
              onClick={() => select(index)}
            >
              <span>{option.name}</span>
              {option.id === value && <Check aria-hidden="true" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export function VideosView({
  videos,
  tasks = [],
  loading = false,
  error,
  permissionDenied = false,
  courseId,
  courseName = "未命名课程",
  courseOptions = [],
  onCourseChange,
  transcriptJobs = [],
  onRetry,
  onPlay,
  onLoadSubtitles,
  onDownload,
  onDownloadSubtitle,
  onCreateSlidesPdf,
  onCancelTask,
  onStartTranscript,
  onRetryTranscript,
  onCancelTranscript,
  onRevealTranscript,
}: VideosViewProps) {
  const [source, setSource] = useState<"all" | VideoSource>("all");
  const [selected, setSelected] = useState<CourseVideoItem | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  const [playbackUrl, setPlaybackUrl] = useState<string | null>(null);
  const [playbackError, setPlaybackError] = useState<string | null>(null);
  const [subtitleUrl, setSubtitleUrl] = useState<string | null>(null);
  const [subtitleMessage, setSubtitleMessage] = useState<string | null>(null);
  const [learningTab, setLearningTab] = useState<LearningTab>("summary");
  const [organizationFilter, setOrganizationFilter] =
    useState<OrganizationFilter>("all");
  const [selectionMode, setSelectionMode] = useState(false);
  const [detailJob, setDetailJob] = useState<TranscriptJob | null>(null);
  const [detailInitialTab, setDetailInitialTab] = useState<
    "summary" | "practice" | "cleaned"
  >("summary");
  const [pendingSummarySourceId, setPendingSummarySourceId] = useState<
    string | null
  >(null);
  const [summaryRequestError, setSummaryRequestError] = useState<{
    sourceId: string;
    message: string;
  } | null>(null);
  const [summaryRequestSettled, setSummaryRequestSettled] = useState(0);
  const [openMenuId, setOpenMenuId] = useState<string | null>(null);
  const [menuPosition, setMenuPosition] = useState<VideoMenuPosition | null>(
    null,
  );
  const [busy, setBusy] = useState<string | null>(null);
  const [pdfResults, setPdfResults] = useState<Map<string, SlidesPdfResult>>(
    () => new Map(),
  );
  const [playbackRate, setPlaybackRate] = useState<PlaybackRate>(1);
  const [speedMenuOpen, setSpeedMenuOpen] = useState(false);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [preservedAcrossSource, setPreservedAcrossSource] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [playerFeedback, setPlayerFeedback] = useState<{
    id: number;
    text: string;
  } | null>(null);
  const subtitleBlobUrl = useRef<string | null>(null);
  const playRequest = useRef(0);
  const autoPlaySourceId = useRef<string | null>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const listPanelRef = useRef<HTMLElement>(null);
  const menuTriggerRefs = useRef(new Map<string, HTMLButtonElement>());
  const menuRef = useRef<HTMLDivElement>(null);
  const playerToolsRef = useRef<HTMLDivElement>(null);
  const speedTriggerRef = useRef<HTMLButtonElement>(null);
  const speedOptionRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const feedbackTimer = useRef<number | null>(null);
  const summaryPriorJobIds = useRef<Set<string>>(new Set());
  const summaryRequestedJobId = useRef<string | null>(null);
  const summaryAwaitsNewJob = useRef(false);
  const busyRef = useRef<symbol | null>(null);
  const playerLoadedRef = useRef(false);
  const playbackRateRef = useRef<PlaybackRate>(1);
  const temporaryPlaybackRef = useRef<TemporaryPlaybackState | null>(null);
  const { showToast } = useToast();

  const jobsByVideo = useMemo(
    () => latestTranscriptJobs(transcriptJobs),
    [transcriptJobs],
  );
  const jobsHistoryByVideo = useMemo(() => {
    const result = new Map<string, TranscriptJob[]>();
    for (const job of transcriptJobs) {
      const history = result.get(job.source_id) ?? [];
      history.push(job);
      result.set(job.source_id, history);
    }
    return result;
  }, [transcriptJobs]);
  const stateForVideo = useCallback(
    (videoId: string) =>
      deriveVideoLearningState(
        jobsByVideo.get(videoId),
        jobsHistoryByVideo.get(videoId),
      ),
    [jobsByVideo, jobsHistoryByVideo],
  );
  const matchesOrganizationFilter = useCallback(
    (video: CourseVideoItem) => {
      const state = stateForVideo(video.id);
      if (organizationFilter === "all") return true;
      if (organizationFilter === "pending")
        return (
          !state.complete &&
          !state.processing &&
          !state.partial &&
          !state.failed
        );
      if (organizationFilter === "processing") return state.processing;
      if (organizationFilter === "completed") return state.complete;
      if (organizationFilter === "partial") return state.partial;
      return state.failed;
    },
    [organizationFilter, stateForVideo],
  );
  const sourceVideos = useMemo(
    () =>
      source === "all"
        ? videos
        : videos.filter((video) => video.source === source),
    [source, videos],
  );
  const filtered = useMemo(
    () => sourceVideos.filter(matchesOrganizationFilter),
    [matchesOrganizationFilter, sourceVideos],
  );
  const selectedVideos = useMemo(
    () => videos.filter((video) => selectedIds.has(video.id)),
    [selectedIds, videos],
  );
  const transcriptEligible = selectedVideos.filter((video) => {
    const job = jobsByVideo.get(video.id);
    return !isCompletedForBatch(job) && !transcriptIsProcessing(job);
  });
  const pdfEligible = selectedVideos.filter((video) => video.supportsSlidesPdf);
  const organizationCounts = useMemo(() => {
    let completed = 0;
    let processing = 0;
    let partial = 0;
    let failed = 0;
    for (const video of videos) {
      const state = stateForVideo(video.id);
      if (state.complete) completed += 1;
      else if (state.processing) processing += 1;
      else if (state.failed) failed += 1;
      else if (state.partial) partial += 1;
    }
    return {
      completed,
      processing,
      partial,
      failed,
      pending: videos.length - completed - processing - partial - failed,
    };
  }, [stateForVideo, videos]);
  const sourceOrganizationCounts = useMemo(() => {
    let completed = 0;
    let processing = 0;
    let partial = 0;
    let failed = 0;
    for (const video of sourceVideos) {
      const state = stateForVideo(video.id);
      if (state.complete) completed += 1;
      else if (state.processing) processing += 1;
      else if (state.failed) failed += 1;
      else if (state.partial) partial += 1;
    }
    return {
      completed,
      processing,
      partial,
      failed,
      pending: sourceVideos.length - completed - processing - partial - failed,
    };
  }, [sourceVideos, stateForVideo]);
  const unfinishedVideos = useMemo(
    () =>
      videos.filter((video) => {
        const state = stateForVideo(video.id);
        return !state.complete && !state.processing;
      }),
    [stateForVideo, videos],
  );
  const skippedOrganizationCount = videos.length - unfinishedVideos.length;
  const selectedJob = selected ? jobsByVideo.get(selected.id) : undefined;
  const selectedSummaryPending = Boolean(
    selected && pendingSummarySourceId === selected.id,
  );
  const selectedSummaryProcessing =
    selectedSummaryPending || transcriptIsProcessing(selectedJob);
  const selectedSummaryReady =
    transcriptIsComplete(selectedJob) && phase1IsReady(selectedJob);
  const selectedSummaryFailed = transcriptNeedsRetry(selectedJob);
  const selectedTasks = selected
    ? tasks.filter((task) => task.videoId === selected.id)
    : [];
  const derivedJobs = useMemo(() => {
    const combined: UnifiedJobItem[] = [
      ...[...jobsByVideo.values()].map((job, order) => ({
        id: job.id,
        sourceId: job.source_id,
        title: compactJobTitle(job.title, courseName),
        kind: "字幕与 AI 整理",
        status: deriveVideoLearningState(
          job,
          jobsHistoryByVideo.get(job.source_id),
        ).status,
        rawStatus: job.status,
        progress: job.progress,
        category: jobCategory(job.status, job.progress),
        timestamp: jobTimestamp(job as TranscriptJob & TimestampedJob),
        order,
        transcript: job,
      })),
      ...tasks.map((task, index) => ({
        id: task.id,
        sourceId: task.videoId,
        title: compactJobTitle(task.title, courseName),
        kind: taskKindLabels[task.kind],
        status: taskStatus(task.status),
        rawStatus: task.status,
        progress: task.progress ?? 0,
        category: jobCategory(task.status, task.progress ?? 0),
        timestamp: jobTimestamp(task),
        order: transcriptJobs.length + index,
        task,
      })),
    ].sort(
      (left, right) =>
        right.timestamp - left.timestamp || left.order - right.order,
    );
    const ids = new Set<string>();
    const sourceCategories = new Set<string>();
    return combined.filter((job) => {
      if (ids.has(job.id)) return false;
      ids.add(job.id);
      const sourceCategory = `${job.sourceId}:${job.category}`;
      if (sourceCategories.has(sourceCategory)) return false;
      sourceCategories.add(sourceCategory);
      return true;
    });
  }, [
    courseName,
    jobsByVideo,
    jobsHistoryByVideo,
    tasks,
    transcriptJobs.length,
  ]);
  const activeJobs = derivedJobs.filter((job) => job.category === "active");
  const attentionJobs = derivedJobs
    .filter((job) => job.category === "attention")
    .slice(0, 3);
  const historyJobs = derivedJobs.filter((job) => job.category === "history");
  const attentionJobIds = new Set(attentionJobs.map((job) => job.id));
  const currentJobs = derivedJobs.filter(
    (job) => job.category === "active" || attentionJobIds.has(job.id),
  );

  useEffect(() => {
    const valid = new Set(videos.map((video) => video.id));
    setSelectedIds(
      (current) => new Set([...current].filter((id) => valid.has(id))),
    );
    if (selected && !valid.has(selected.id)) setSelected(null);
  }, [videos, selected]);
  useLayoutEffect(() => {
    playRequest.current += 1;
    autoPlaySourceId.current = null;
    if (subtitleBlobUrl.current) {
      URL.revokeObjectURL(subtitleBlobUrl.current);
      subtitleBlobUrl.current = null;
    }
    const temporaryPlayback = temporaryPlaybackRef.current;
    if (temporaryPlayback) {
      if (temporaryPlayback.holdTimer !== null)
        window.clearTimeout(temporaryPlayback.holdTimer);
      if (temporaryPlayback.rewindTimer !== null)
        window.clearInterval(temporaryPlayback.rewindTimer);
      temporaryPlayback.media.playbackRate = temporaryPlayback.basePlaybackRate;
      temporaryPlaybackRef.current = null;
    }
    playerLoadedRef.current = false;
    setSelected(null);
    setSelectedIds(new Set());
    setPlaybackUrl(null);
    setPlaybackError(null);
    setSubtitleUrl(null);
    setSubtitleMessage(null);
    setPreservedAcrossSource(false);
    setBusy(null);
    busyRef.current = null;
    setLearningTab("summary");
    setOrganizationFilter("all");
    setSelectionMode(false);
    setDetailJob(null);
    setOpenMenuId(null);
    setMenuPosition(null);
    setPendingSummarySourceId(null);
    setSummaryRequestError(null);
    summaryRequestedJobId.current = null;
    summaryAwaitsNewJob.current = false;
  }, [courseId]);
  const positionVideoMenu = useCallback((videoId: string) => {
    const trigger = menuTriggerRefs.current.get(videoId);
    if (!trigger) return;
    const rect = trigger.getBoundingClientRect();
    const edge = 8;
    const gap = 4;
    const width = 210;
    const height = menuRef.current?.getBoundingClientRect().height || 260;
    const availableBelow = window.innerHeight - rect.bottom - edge;
    const direction =
      availableBelow >= Math.min(height, 220) || availableBelow >= rect.top
        ? "down"
        : "up";
    const top =
      direction === "down"
        ? Math.min(rect.bottom + gap, window.innerHeight - height - edge)
        : Math.max(edge, rect.top - height - gap);
    setMenuPosition({
      top: Math.max(edge, top),
      left: Math.max(
        edge,
        Math.min(
          Math.max(edge, rect.right - width),
          window.innerWidth - width - edge,
        ),
      ),
      direction,
    });
  }, []);
  const closeVideoMenu = useCallback(() => {
    setOpenMenuId(null);
    setMenuPosition(null);
  }, []);
  useLayoutEffect(() => {
    if (openMenuId && menuPosition) positionVideoMenu(openMenuId);
  }, [openMenuId, menuPosition?.direction, positionVideoMenu]);
  useEffect(() => {
    if (!openMenuId) return;
    const trigger = menuTriggerRefs.current.get(openMenuId);
    const closeFromOutside = () => closeVideoMenu();
    const closeFromViewportChange = (event: Event) => {
      if (
        event.type === "scroll" &&
        event.target instanceof Node &&
        menuRef.current?.contains(event.target)
      )
        return;
      closeVideoMenu();
    };
    const closeFromKeyboard = (event: globalThis.KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      closeVideoMenu();
      window.queueMicrotask(() => trigger?.focus());
    };
    document.addEventListener("mousedown", closeFromOutside);
    document.addEventListener("keydown", closeFromKeyboard);
    window.addEventListener("resize", closeFromViewportChange);
    window.addEventListener("scroll", closeFromViewportChange, true);
    return () => {
      document.removeEventListener("mousedown", closeFromOutside);
      document.removeEventListener("keydown", closeFromKeyboard);
      window.removeEventListener("resize", closeFromViewportChange);
      window.removeEventListener("scroll", closeFromViewportChange, true);
    };
  }, [closeVideoMenu, openMenuId]);
  useEffect(() => {
    if (!detailJob) return;
    const latest = transcriptJobs.find((job) => job.id === detailJob.id);
    if (latest && latest !== detailJob) setDetailJob(latest);
  }, [detailJob, transcriptJobs]);
  useEffect(() => {
    if (!pendingSummarySourceId || !summaryAwaitsNewJob.current) return;
    const requestedJob = transcriptJobs.find(
      (job) =>
        job.source_id === pendingSummarySourceId &&
        !summaryPriorJobIds.current.has(job.id),
    );
    if (!requestedJob) return;
    summaryRequestedJobId.current = requestedJob.id;
    summaryAwaitsNewJob.current = false;
  }, [pendingSummarySourceId, summaryRequestSettled, transcriptJobs]);
  useEffect(() => {
    if (!pendingSummarySourceId || summaryAwaitsNewJob.current) return;
    const requestedJob = summaryRequestedJobId.current
      ? transcriptJobs.find((job) => job.id === summaryRequestedJobId.current)
      : jobsByVideo.get(pendingSummarySourceId);
    if (!requestedJob) return;
    if (transcriptIsComplete(requestedJob) && phase1IsReady(requestedJob)) {
      setDetailInitialTab("summary");
      setDetailJob(requestedJob);
      setPendingSummarySourceId(null);
      summaryRequestedJobId.current = null;
      return;
    }
    if (transcriptNeedsRetry(requestedJob)) {
      setPendingSummarySourceId(null);
      summaryRequestedJobId.current = null;
    }
  }, [jobsByVideo, pendingSummarySourceId, transcriptJobs]);
  useEffect(() => {
    if (!speedMenuOpen && !shortcutsOpen) return;
    const close = (event: MouseEvent) => {
      if (!playerToolsRef.current?.contains(event.target as Node)) {
        setSpeedMenuOpen(false);
        setShortcutsOpen(false);
      }
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [shortcutsOpen, speedMenuOpen]);
  useEffect(() => {
    if (speedMenuOpen) {
      const selectedIndex = playbackRates.indexOf(playbackRate);
      window.requestAnimationFrame(() =>
        speedOptionRefs.current[selectedIndex]?.focus(),
      );
    }
  }, [playbackRate, speedMenuOpen]);

  const showPlayerFeedback = useCallback((text: string, persistent = false) => {
    if (feedbackTimer.current) window.clearTimeout(feedbackTimer.current);
    feedbackTimer.current = null;
    setPlayerFeedback((current) => ({ id: (current?.id ?? 0) + 1, text }));
    if (!persistent) {
      feedbackTimer.current = window.setTimeout(
        () => setPlayerFeedback(null),
        1300,
      );
    }
  }, []);
  const choosePlaybackRate = useCallback(
    (rate: PlaybackRate) => {
      setPlaybackRate(rate);
      playbackRateRef.current = rate;
      const temporaryPlayback = temporaryPlaybackRef.current;
      if (temporaryPlayback?.active) {
        temporaryPlayback.basePlaybackRate = rate;
        if (temporaryPlayback.direction === "forward") {
          temporaryPlayback.media.playbackRate = temporaryPlayback.speed;
        }
      } else if (videoRef.current) videoRef.current.playbackRate = rate;
      showPlayerFeedback(formatPlaybackRate(rate));
      setSpeedMenuOpen(false);
    },
    [showPlayerFeedback],
  );
  const stepPlaybackRate = useCallback(
    (direction: -1 | 1) => {
      const currentIndex = playbackRates.indexOf(playbackRate);
      const nextIndex = Math.max(
        0,
        Math.min(playbackRates.length - 1, currentIndex + direction),
      );
      choosePlaybackRate(playbackRates[nextIndex]);
    },
    [choosePlaybackRate, playbackRate],
  );
  const seekVideo = useCallback(
    (seconds: number) => {
      const media = videoRef.current;
      if (!media) return;
      const duration = Number.isFinite(media.duration)
        ? Math.max(0, media.duration)
        : Number.POSITIVE_INFINITY;
      media.currentTime = Math.min(
        duration,
        Math.max(0, media.currentTime + seconds),
      );
      showPlayerFeedback(
        `${seconds > 0 ? "前进" : "后退"}${Math.abs(seconds)}秒`,
      );
    },
    [showPlayerFeedback],
  );
  const togglePlayback = useCallback(() => {
    const media = videoRef.current;
    if (!media) return;
    if (!media.paused) {
      media.pause();
      showPlayerFeedback("已暂停");
      return;
    }
    try {
      Promise.resolve(media.play())
        .then(() => showPlayerFeedback("正在播放"))
        .catch(() => showPlayerFeedback("浏览器阻止播放，请点击播放器继续"));
    } catch {
      showPlayerFeedback("浏览器阻止播放，请点击播放器继续");
    }
  }, [showPlayerFeedback]);
  const stopTemporaryPlayback = useCallback(
    (seekOnShortPress = false, clearFeedback = true) => {
      const state = temporaryPlaybackRef.current;
      if (!state) return;
      if (state.holdTimer !== null) window.clearTimeout(state.holdTimer);
      if (state.rewindTimer !== null) window.clearInterval(state.rewindTimer);
      temporaryPlaybackRef.current = null;

      if (state.active) {
        state.media.playbackRate = state.basePlaybackRate;
        if (state.wasPaused) {
          state.media.pause();
        } else if (state.media.paused) {
          try {
            Promise.resolve(state.media.play()).catch(() => undefined);
          } catch {
            // Restoring the rate still leaves the player in a safe state.
          }
        }
      } else if (seekOnShortPress) {
        seekVideo(state.direction === "forward" ? 5 : -5);
      }

      if (clearFeedback && state.active) {
        if (feedbackTimer.current) window.clearTimeout(feedbackTimer.current);
        feedbackTimer.current = null;
        setPlayerFeedback(null);
      }
    },
    [seekVideo],
  );
  const startTemporaryPlayback = useCallback(
    (
      direction: TemporaryPlaybackDirection,
      key: "arrowleft" | "arrowright",
      media: HTMLVideoElement,
    ) => {
      const current = temporaryPlaybackRef.current;
      if (current?.key === key) return;
      if (current) stopTemporaryPlayback();

      const state: TemporaryPlaybackState = {
        key,
        direction,
        media,
        holdTimer: null,
        rewindTimer: null,
        active: false,
        speed: 2,
        basePlaybackRate: playbackRateRef.current,
        wasPaused: media.paused,
      };
      temporaryPlaybackRef.current = state;
      state.holdTimer = window.setTimeout(() => {
        if (temporaryPlaybackRef.current !== state) return;
        state.holdTimer = null;
        state.active = true;
        state.basePlaybackRate = playbackRateRef.current;
        state.wasPaused = media.paused;

        if (direction === "forward") {
          media.playbackRate = state.speed;
          if (media.paused) {
            try {
              Promise.resolve(media.play()).catch(() => undefined);
            } catch {
              // The release path still restores the base player state.
            }
          }
        } else {
          media.pause();
          state.rewindTimer = window.setInterval(() => {
            media.currentTime = Math.max(
              0,
              media.currentTime - state.speed * (rewindIntervalMs / 1000),
            );
          }, rewindIntervalMs);
        }
        showPlayerFeedback(
          (direction === "forward" ? "快进 " : "倒退 ") + state.speed + "×",
          true,
        );
      }, longPressDelay);
    },
    [showPlayerFeedback, stopTemporaryPlayback],
  );
  const stepTemporaryPlaybackRate = useCallback(
    (direction: -1 | 1) => {
      const state = temporaryPlaybackRef.current;
      if (!state?.active) return;
      const currentIndex = temporaryPlaybackRates.indexOf(state.speed);
      const nextIndex = Math.max(
        0,
        Math.min(temporaryPlaybackRates.length - 1, currentIndex + direction),
      );
      state.speed = temporaryPlaybackRates[nextIndex];
      if (state.direction === "forward") state.media.playbackRate = state.speed;
      showPlayerFeedback(
        (state.direction === "forward" ? "快进 " : "倒退 ") + state.speed + "×",
        true,
      );
    },
    [showPlayerFeedback],
  );

  useEffect(() => {
    playbackRateRef.current = playbackRate;
  }, [playbackRate]);
  useEffect(() => {
    stopTemporaryPlayback();
  }, [playbackUrl, selected?.id, stopTemporaryPlayback]);
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const key = event.key.toLowerCase();
      if (key === "escape" && temporaryPlaybackRef.current) {
        event.preventDefault();
        stopTemporaryPlayback();
        return;
      }
      const media = videoRef.current;
      if (
        !selected ||
        !playbackUrl ||
        !media ||
        !playerLoadedRef.current ||
        event.metaKey ||
        event.ctrlKey ||
        event.altKey ||
        isInteractiveTarget(event.target)
      )
        return;

      if (key === "arrowleft" || key === "arrowright") {
        event.preventDefault();
        if (event.repeat) return;
        startTemporaryPlayback(
          key === "arrowright" ? "forward" : "rewind",
          key,
          media,
        );
      } else if (
        (key === "arrowup" || key === "arrowdown") &&
        temporaryPlaybackRef.current?.active
      ) {
        event.preventDefault();
        stepTemporaryPlaybackRate(key === "arrowup" ? 1 : -1);
      } else if (
        key === " " ||
        key === "space" ||
        key === "spacebar" ||
        key === "k"
      ) {
        event.preventDefault();
        togglePlayback();
      } else if (key === "j") {
        event.preventDefault();
        seekVideo(-10);
      } else if (key === "l") {
        event.preventDefault();
        seekVideo(10);
      } else if (
        key === "[" ||
        key === "<" ||
        (event.shiftKey && key === ",")
      ) {
        event.preventDefault();
        stepPlaybackRate(-1);
      } else if (
        key === "]" ||
        key === ">" ||
        (event.shiftKey && key === ".")
      ) {
        event.preventDefault();
        stepPlaybackRate(1);
      }
    };
    const onKeyUp = (event: KeyboardEvent) => {
      const state = temporaryPlaybackRef.current;
      if (!state || event.key.toLowerCase() !== state.key) return;
      event.preventDefault();
      stopTemporaryPlayback(true);
    };
    const onBlur = () => stopTemporaryPlayback();
    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("keyup", onKeyUp);
    window.addEventListener("blur", onBlur);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.removeEventListener("keyup", onKeyUp);
      window.removeEventListener("blur", onBlur);
    };
  }, [
    playbackUrl,
    seekVideo,
    selected,
    startTemporaryPlayback,
    stepPlaybackRate,
    stepTemporaryPlaybackRate,
    stopTemporaryPlayback,
    togglePlayback,
  ]);

  const revokeSubtitleBlob = () => {
    if (subtitleBlobUrl.current) {
      URL.revokeObjectURL(subtitleBlobUrl.current);
      subtitleBlobUrl.current = null;
    }
  };
  const changeSource = (nextSource: "all" | VideoSource) => {
    setSource(nextSource);
    if (!selected || nextSource === "all" || selected.source === nextSource) {
      setPreservedAcrossSource(false);
      return;
    }
    const isPlaying = Boolean(
      playbackUrl && videoRef.current && !videoRef.current.paused,
    );
    if (isPlaying) {
      setPreservedAcrossSource(true);
      return;
    }
    playRequest.current += 1;
    autoPlaySourceId.current = null;
    const invalidatedBusy =
      busy === `play:${selected.id}` || busy === `subtitle:${selected.id}`;
    if (invalidatedBusy) {
      busyRef.current = null;
      setBusy(null);
    }
    revokeSubtitleBlob();
    setSelected(null);
    setPlaybackUrl(null);
    setPlaybackError(null);
    setSubtitleUrl(null);
    setSubtitleMessage(null);
    setPreservedAcrossSource(false);
    setLearningTab("summary");
  };
  useEffect(
    () => () => {
      playRequest.current += 1;
      if (subtitleBlobUrl.current) URL.revokeObjectURL(subtitleBlobUrl.current);
      if (feedbackTimer.current) window.clearTimeout(feedbackTimer.current);
      stopTemporaryPlayback(false, false);
    },
    [stopTemporaryPlayback],
  );

  const run = async (
    key: string,
    label: string,
    action?: () => void | Promise<void>,
    onError?: (message: string) => void,
  ) => {
    if (!action || busyRef.current || busy) return false;
    const operation = Symbol(key);
    busyRef.current = operation;
    setBusy(key);
    try {
      await action();
      showToast({ kind: "success", message: label });
      return true;
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : "视频操作失败";
      onError?.(message);
      showToast({ kind: "error", message });
      return false;
    } finally {
      if (busyRef.current === operation) {
        busyRef.current = null;
        setBusy((current) => (current === key ? null : current));
      }
    }
  };

  const useSubtitleResult = (result: VideoPlaybackResult) => {
    revokeSubtitleBlob();
    if (result.subtitleVtt) {
      const objectUrl = URL.createObjectURL(
        new Blob([result.subtitleVtt], { type: "text/vtt;charset=utf-8" }),
      );
      subtitleBlobUrl.current = objectUrl;
      setSubtitleUrl(objectUrl);
    } else setSubtitleUrl(result.subtitleUrl ?? null);
    setSubtitleMessage(result.subtitleMessage ?? null);
  };

  const loadSubtitles = (video: CourseVideoItem) => {
    const request = playRequest.current;
    return run(`subtitle:${video.id}`, "字幕状态已更新。", async () => {
      setSelected(video);
      setLearningTab("transcript");
      const result = await onLoadSubtitles?.(video);
      if (result && playRequest.current === request) useSubtitleResult(result);
    });
  };

  const play = async (video: CourseVideoItem, shouldAutoPlay = true) => {
    if (video.playable === false || busy) return;
    stopTemporaryPlayback();
    videoRef.current?.pause();
    playerLoadedRef.current = false;
    const request = ++playRequest.current;
    revokeSubtitleBlob();
    setSelected(video);
    setPreservedAcrossSource(false);
    autoPlaySourceId.current = shouldAutoPlay ? video.id : null;
    setPlaybackUrl(null);
    setPlaybackError(null);
    setSubtitleUrl(null);
    setSubtitleMessage(video.supportsSubtitle ? "正在加载字幕…" : null);
    setBusy(`play:${video.id}`);
    try {
      const result = await onPlay?.(video);
      if (playRequest.current !== request) return;
      let nextUrl: string | null = null;
      if (typeof result === "string") {
        nextUrl = result;
        setSubtitleMessage(null);
      } else if (result) {
        nextUrl = result.url ?? video.playbackUrl ?? null;
        useSubtitleResult({
          ...result,
          subtitleUrl: result.subtitleUrl ?? video.subtitleUrl,
        });
      } else {
        nextUrl = video.playbackUrl ?? null;
        setSubtitleUrl(video.subtitleUrl ?? null);
        setSubtitleMessage(null);
      }
      setPlaybackUrl(nextUrl);
      if (!nextUrl) setPlaybackError("该节录像未返回可播放地址。");
      if (
        video.source === "video_space" &&
        video.supportsSubtitle &&
        onLoadSubtitles
      ) {
        const subtitleResult = await onLoadSubtitles(video);
        if (playRequest.current === request) useSubtitleResult(subtitleResult);
      }
    } catch (reason) {
      if (playRequest.current === request) {
        const message =
          reason instanceof Error ? reason.message : "无法播放该节录像";
        setPlaybackUrl(null);
        autoPlaySourceId.current = null;
        setPlaybackError(message);
        showToast({ kind: "error", message });
      }
    } finally {
      if (playRequest.current === request) setBusy(null);
    }
  };

  const viewLearningMaterials = async (video: CourseVideoItem) => {
    setLearningTab("summary");
    await play(video, false);
  };

  const toggleVideo = (videoId: string) =>
    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(videoId)) next.delete(videoId);
      else next.add(videoId);
      return next;
    });
  const startTranscript = (
    items: CourseVideoItem[],
    key = "transcript:batch",
    label = `开始整理 ${items.length} 节。`,
  ) =>
    run(key, label, async () => {
      if (!items.length || !onStartTranscript) return;
      await onStartTranscript(items);
      setSelectedIds(new Set());
    });
  const organizeVideo = (video: CourseVideoItem, job?: TranscriptJob) => {
    setSelected(video);
    setLearningTab("summary");
    if (transcriptIsProcessing(job) || isCompletedForBatch(job)) return;
    if (job && transcriptNeedsRetry(job) && onRetryTranscript)
      return run(`organize:${video.id}`, "已重新开始整理本节学习材料。", () =>
        onRetryTranscript(job),
      );
    return startTranscript(
      [video],
      `organize:${video.id}`,
      "已开始整理本节学习材料。",
    );
  };
  const organizeAllUnfinished = () =>
    startTranscript(
      unfinishedVideos,
      "transcript:all",
      `开始整理 ${unfinishedVideos.length} 节，跳过 ${skippedOrganizationCount} 节已完成/处理中。`,
    );
  const requestSummary = async (
    video: CourseVideoItem,
    job?: TranscriptJob,
  ) => {
    if (transcriptIsProcessing(job) || pendingSummarySourceId === video.id)
      return;
    if (job && transcriptIsComplete(job) && phase1IsReady(job)) {
      setDetailInitialTab("summary");
      setDetailJob(job);
      return;
    }
    const action = transcriptNeedsRetry(job)
      ? onRetryTranscript
        ? () => onRetryTranscript(job as TranscriptJob)
        : undefined
      : onStartTranscript
        ? () => onStartTranscript([video])
        : undefined;
    if (!action) return;

    setSelected(video);
    setLearningTab("summary");
    setSummaryRequestError(null);
    setPendingSummarySourceId(video.id);
    summaryRequestedJobId.current = null;
    summaryPriorJobIds.current = new Set(
      transcriptJobs
        .filter((item) => item.source_id === video.id)
        .map((item) => item.id),
    );
    summaryAwaitsNewJob.current = true;
    const succeeded = await run(
      `summary:${video.id}`,
      "已开始生成 AI 总结。",
      action,
      (message) => setSummaryRequestError({ sourceId: video.id, message }),
    );
    if (!succeeded) {
      summaryAwaitsNewJob.current = false;
      summaryRequestedJobId.current = null;
      setPendingSummarySourceId(null);
      return;
    }
    setSummaryRequestSettled((value) => value + 1);
  };
  const rememberPdfResult = (
    video: CourseVideoItem,
    result: void | SlidesPdfResult,
  ) => {
    const value = result ?? {};
    const key =
      value.scope === "course" ? `course:${courseId ?? courseName}` : video.id;
    setPdfResults((current) => new Map(current).set(key, value));
  };
  const createPdf = (video: CourseVideoItem, key = `pdf:${video.id}`) =>
    run(key, "课件已生成（临时文件）。", async () => {
      const result = await onCreateSlidesPdf?.(video);
      rememberPdfResult(video, result);
    });
  const createPdfs = () =>
    run("pdf:batch", "所选课件已生成（临时文件）。", async () => {
      for (const video of pdfEligible) {
        const result = await onCreateSlidesPdf?.(video);
        rememberPdfResult(video, result);
      }
      setSelectedIds(new Set());
    });

  const renderLearningContent = (tab: LearningTab) => {
    if (!selected)
      return (
        <p className="video-tool-empty">选择右侧录像后，这里会显示学习材料。</p>
      );
    if (
      tab !== "pdf" &&
      tab !== "notes" &&
      !selectedJob &&
      selectedTasks.length === 0 &&
      !subtitleMessage
    )
      return (
        <div className="video-learning-empty">
          <div>
            <strong>这节录像还没有学习材料</strong>
            <span>整理后可获得讲义、字幕、主动练习和可跳转时间戳。</span>
          </div>
          <Button
            variant="outline"
            size="sm"
            loading={busy === `organize:${selected.id}`}
            disabled={!onStartTranscript || Boolean(busy)}
            onClick={() => void organizeVideo(selected)}
          >
            <NotebookPen aria-hidden="true" />
            整理本节学习材料
          </Button>
        </div>
      );
    if (tab === "notes") {
      const canOpenPractice = Boolean(
        selectedJob && phase1IsReady(selectedJob),
      );
      return (
        <div className="video-tool-summary">
          <NotebookPen aria-hidden="true" />
          <span>
            {canOpenPractice
              ? "题目与答案分开呈现，答案默认折叠。来源：【自编练习】"
              : "完成本节讲义后，将根据 summary、study_guide 与 practice_items 生成自测。"}
          </span>
          {canOpenPractice && selectedJob && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                setDetailInitialTab("practice");
                setDetailJob(selectedJob);
              }}
            >
              开始练习
            </Button>
          )}
        </div>
      );
    }
    if (tab === "pdf") {
      const pdfTask = [...selectedTasks]
        .filter((task) => task.kind === "slides_pdf")
        .sort((left, right) => jobTimestamp(right) - jobTimestamp(left))[0];
      const coursePdf = pdfResults.get(`course:${courseId ?? courseName}`);
      const videoPdf = pdfResults.get(selected.id);
      const persistedPdf = taskPdfResult(pdfTask);
      const pdfResult = videoPdf ?? coursePdf ?? persistedPdf;
      const generated = Boolean(pdfResult) || pdfTask?.status === "completed";
      const courseScoped = pdfResult?.scope === "course";
      const openPdf = pdfResult?.open
        ? pdfResult.open
        : pdfResult?.fileUrl || pdfResult?.url
          ? () =>
              window.open(pdfResult.fileUrl ?? pdfResult.url ?? "", "_blank")
          : undefined;
      return (
        <div className="video-tool-summary video-pdf-state">
          <StatusBadge
            status={
              generated
                ? "completed"
                : pdfTask
                  ? taskStatus(pdfTask.status)
                  : "pending"
            }
          />
          <span>
            {generated
              ? `${courseScoped ? "课程课件已生成" : "课件已生成"}（临时文件）`
              : pdfTask?.message ||
                (selected.supportsSlidesPdf
                  ? "可从录像更多操作中生成课件 PDF。"
                  : "该录像暂不支持课件 PDF。")}
            {generated && <small>位于临时目录，需另存长期保留。</small>}
          </span>
          {generated && openPdf && (
            <Button variant="outline" size="sm" onClick={() => void openPdf()}>
              打开
            </Button>
          )}
          {onCreateSlidesPdf && selected.supportsSlidesPdf && (
            <Button
              variant="outline"
              size="sm"
              loading={busy === `pdf:${selected.id}`}
              disabled={Boolean(busy)}
              onClick={() => void createPdf(selected)}
            >
              <FileText aria-hidden="true" />
              {generated ? "重新生成" : "生成课件 PDF"}
            </Button>
          )}
        </div>
      );
    }
    const isSummary = tab === "summary";
    if (isSummary) {
      const requestError =
        summaryRequestError?.sourceId === selected.id
          ? summaryRequestError.message
          : null;
      const waitingForPhase1 =
        transcriptIsComplete(selectedJob) &&
        !phase1IsReady(selectedJob) &&
        !selectedSummaryFailed;
      const summaryMessage = requestError
        ? `${requestError}。可重试生成。`
        : selectedSummaryFailed
          ? selectedJob?.error ||
            selectedJob?.message ||
            "生成未完成，可从失败阶段重试。"
          : waitingForPhase1
            ? "字幕已规整，正在等待 AI 校对完成；完成后才能查看总结。"
            : selectedSummaryProcessing
              ? selectedJob?.message ||
                "正在规整字幕并进行 AI 校对，完成后将自动打开总结。"
              : selectedSummaryReady
                ? "字幕规整和 AI 校对已完成，可查看 AI 总结。"
                : "生成前会先规整字幕并完成 AI 校对，再生成总结。";
      const summaryEligible =
        selected.source === "video_space" && selected.supportsSubtitle;
      const canRequestSummary = selectedSummaryFailed
        ? Boolean(onRetryTranscript)
        : Boolean(summaryEligible && onStartTranscript);
      return (
        <div
          className="video-tool-summary"
          aria-busy={selectedSummaryProcessing || undefined}
          aria-live="polite"
        >
          <StatusBadge
            status={
              requestError || selectedSummaryFailed
                ? "failed"
                : selectedSummaryProcessing || waitingForPhase1
                  ? "processing"
                  : selectedSummaryReady
                    ? "completed"
                    : "pending"
            }
          />
          <span>{summaryMessage}</span>
          {selectedSummaryReady && selectedJob ? (
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                setDetailInitialTab("summary");
                setDetailJob(selectedJob);
              }}
            >
              查看 AI 总结
            </Button>
          ) : canRequestSummary ? (
            <Button
              variant="outline"
              size="sm"
              loading={selectedSummaryProcessing}
              loadingLabel={
                busy === `summary:${selected.id}` ? "正在启动…" : "正在生成…"
              }
              disabled={selectedSummaryProcessing}
              onClick={() => void requestSummary(selected, selectedJob)}
            >
              <Captions aria-hidden="true" />
              {selectedSummaryFailed || requestError
                ? "重试生成 AI 总结"
                : waitingForPhase1
                  ? "继续生成 AI 总结"
                  : "生成 AI 总结"}
            </Button>
          ) : null}
        </div>
      );
    }
    return (
      <div className="video-tool-summary">
        <StatusBadge status={transcriptStatus(selectedJob)} />
        <span>
          {subtitleMessage || selectedJob?.message || "尚无可查看的字幕内容。"}
        </span>
        {selectedJob && onRetryTranscript && onRevealTranscript ? (
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              setDetailInitialTab("cleaned");
              setDetailJob(selectedJob);
            }}
          >
            查看字幕
          </Button>
        ) : onStartTranscript &&
          selected.source === "video_space" &&
          selected.supportsSubtitle ? (
          <Button
            variant="outline"
            size="sm"
            disabled={Boolean(busy)}
            onClick={() =>
              void startTranscript([selected], `transcript:${selected.id}`)
            }
          >
            <Captions aria-hidden="true" />
            生成字幕与 AI 整理
          </Button>
        ) : null}
      </div>
    );
  };

  return (
    <main
      className="video-workbench"
      aria-busy={loading || undefined}
      data-state={
        loading
          ? "loading"
          : permissionDenied
            ? "permission-denied"
            : error
              ? "error"
              : videos.length === 0
                ? "empty"
                : "ready"
      }
    >
      <section className="video-course-toolbar" aria-label="课程与视频来源">
        <div className="video-course-heading">
          <span>当前课程</span>
          {loading && !courseId ? (
            <div className="video-course-placeholder" aria-label="正在确认课程">
              <span className="video-skeleton-line" />
            </div>
          ) : (
            <CoursePicker
              value={courseId}
              options={courseOptions}
              fallback={courseName}
              onChange={onCourseChange}
            />
          )}
          <span className="video-course-count">
            {loading ? (
              "正在加载…"
            ) : (
              <>
                共 <strong>{videos.length}</strong> 节，已整理{" "}
                <strong>{organizationCounts.completed}</strong> 节
              </>
            )}
          </span>
        </div>
        <div className="video-course-progress" aria-label="课程整理进度">
          <span>
            待整理 <strong>{organizationCounts.pending}</strong> 节，处理中{" "}
            <strong>{organizationCounts.processing}</strong> 节；部分完成{" "}
            <strong>{organizationCounts.partial}</strong> 节，失败{" "}
            <strong>{organizationCounts.failed}</strong> 节
          </span>
          <Button
            variant="outline"
            size="sm"
            loading={busy === "transcript:all"}
            disabled={
              loading ||
              !onStartTranscript ||
              unfinishedVideos.length === 0 ||
              Boolean(busy)
            }
            title={
              unfinishedVideos.length === 0
                ? organizationCounts.completed === videos.length &&
                  videos.length > 0
                  ? "全部已整理"
                  : "没有可重复提交的录像"
                : `跳过 ${skippedOrganizationCount} 节已完成/处理中`
            }
            onClick={() => void organizeAllUnfinished()}
          >
            <NotebookPen aria-hidden="true" />
            {unfinishedVideos.length === 0 &&
            organizationCounts.completed === videos.length &&
            videos.length > 0
              ? "全部已整理"
              : `整理未完成 ${unfinishedVideos.length} 节`}
          </Button>
        </div>
        <Tabs
          value={source}
          onValueChange={(value) => changeSource(value as "all" | VideoSource)}
        >
          <TabsList aria-label="视频来源">
            <TabsTrigger value="all">全部</TabsTrigger>
            <TabsTrigger value="canvas">Canvas</TabsTrigger>
            <TabsTrigger value="video_space">视频空间</TabsTrigger>
            <TabsTrigger value="legacy">旧版课堂</TabsTrigger>
          </TabsList>
        </Tabs>
      </section>

      <section className="video-learning-grid" aria-label="课程视频学习工作台">
        <div className="video-player-column">
          <div className="video-player-shell" data-testid="video-player-shell">
            {playbackUrl ? (
              <video
                key={playbackUrl}
                ref={videoRef}
                controls
                preload="metadata"
                aria-label={selected ? `播放 ${selected.title}` : "课程视频"}
                onPlay={() =>
                  setPreservedAcrossSource(
                    Boolean(
                      selected &&
                        source !== "all" &&
                        selected.source !== source,
                    ),
                  )
                }
                onPause={() => setPreservedAcrossSource(false)}
                onEnded={() => setPreservedAcrossSource(false)}
                onLoadedMetadata={(event) => {
                  playerLoadedRef.current = true;
                  event.currentTarget.playbackRate = playbackRateRef.current;
                  if (selected && autoPlaySourceId.current === selected.id) {
                    autoPlaySourceId.current = null;
                    try {
                      Promise.resolve(event.currentTarget.play()).catch(
                        () => undefined,
                      );
                    } catch {
                      // Native controls remain available when autoplay is blocked.
                    }
                  }
                }}
              >
                <source src={playbackUrl} />
                {subtitleUrl && (
                  <track
                    kind="subtitles"
                    src={subtitleUrl}
                    srcLang="zh"
                    label="中文字幕"
                    default
                  />
                )}
              </video>
            ) : loading ? (
              <div
                className="video-player-empty video-player-loading"
                role="status"
              >
                <LoaderCircle aria-hidden="true" />
                <strong>正在准备视频工作台</strong>
                <span>课程与录像会在这里原位载入</span>
              </div>
            ) : permissionDenied ? (
              <div className="video-player-empty" role="status">
                <CircleAlert aria-hidden="true" />
                <strong>需要视频访问权限</strong>
                <span>完成视频平台登录或联系课程教师开通权限</span>
              </div>
            ) : error ? (
              <div className="video-player-empty" role="status">
                <CircleAlert aria-hidden="true" />
                <strong>课程视频暂时无法载入</strong>
                <span>{error}</span>
              </div>
            ) : playbackError && selected ? (
              <div className="video-player-empty" role="alert">
                <CircleAlert aria-hidden="true" />
                <strong>{selected.title} 无法载入</strong>
                <span>{playbackError}</span>
              </div>
            ) : selected ? (
              <div className="video-player-empty" role="status">
                <Play aria-hidden="true" />
                <strong>
                  {busy === `play:${selected.id}`
                    ? "正在准备播放"
                    : "已选择当前录像"}
                </strong>
                <span>
                  {busy === `play:${selected.id}`
                    ? "正在获取视频地址…"
                    : "可继续使用下方学习工具，或再次选择播放"}
                </span>
              </div>
            ) : (
              <div className="video-player-empty">
                <Play aria-hidden="true" />
                <strong>尚未选择播放内容</strong>
                <span>从右侧录像列表选择一节开始学习</span>
              </div>
            )}
            {playerFeedback && (
              <div
                key={playerFeedback.id}
                className="video-player-feedback"
                role="status"
                aria-live="polite"
              >
                {playerFeedback.text}
              </div>
            )}
          </div>
          <Button
            variant="outline"
            size="sm"
            className="video-list-jump"
            onClick={() => {
              const list = listPanelRef.current;
              if (!list) return;
              const reduceMotion =
                window.matchMedia?.("(prefers-reduced-motion: reduce)")
                  .matches ?? false;
              list.scrollIntoView({
                behavior: reduceMotion ? "auto" : "smooth",
                block: "start",
              });
              list.focus({ preventScroll: true });
            }}
          >
            查看录像列表
          </Button>
          {selected && (
            <div className="video-player-meta-row">
              <div className="video-now-playing">
                <strong>{selected.title}</strong>
                <span>
                  {[
                    preservedAcrossSource
                      ? `正在播放 · ${sourceLabels[selected.source]}`
                      : null,
                    selected.classroom,
                    formatRecordedAt(selected.recordedAt),
                    formatDuration(selected.duration),
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </span>
              </div>
              <div className="video-player-tools" ref={playerToolsRef}>
                <div className="video-player-tool">
                  <button
                    ref={speedTriggerRef}
                    type="button"
                    className="video-player-tool-trigger"
                    aria-label={`播放速度 ${formatPlaybackRate(playbackRate)}`}
                    aria-haspopup="menu"
                    aria-expanded={speedMenuOpen}
                    onClick={() => {
                      setShortcutsOpen(false);
                      setSpeedMenuOpen((open) => !open);
                    }}
                  >
                    {formatPlaybackRate(playbackRate)}
                    <ChevronDown aria-hidden="true" />
                  </button>
                  {speedMenuOpen && (
                    <div
                      className="video-player-popover video-speed-menu"
                      role="menu"
                      aria-label="播放速度"
                    >
                      {playbackRates.map((rate, index) => (
                        <button
                          key={rate}
                          ref={(node) => {
                            speedOptionRefs.current[index] = node;
                          }}
                          type="button"
                          role="menuitemradio"
                          aria-checked={rate === playbackRate}
                          onClick={() => choosePlaybackRate(rate)}
                          onKeyDown={(event) => {
                            let nextIndex: number | null = null;
                            if (event.key === "ArrowDown")
                              nextIndex = (index + 1) % playbackRates.length;
                            else if (event.key === "ArrowUp")
                              nextIndex =
                                (index - 1 + playbackRates.length) %
                                playbackRates.length;
                            else if (event.key === "Home") nextIndex = 0;
                            else if (event.key === "End")
                              nextIndex = playbackRates.length - 1;
                            else if (event.key === "Escape") {
                              event.preventDefault();
                              setSpeedMenuOpen(false);
                              speedTriggerRef.current?.focus();
                            }
                            if (nextIndex !== null) {
                              event.preventDefault();
                              speedOptionRefs.current[nextIndex]?.focus();
                            }
                          }}
                        >
                          <span>{formatPlaybackRate(rate)}</span>
                          {rate === playbackRate && (
                            <Check aria-hidden="true" />
                          )}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
                <div className="video-player-tool">
                  <button
                    type="button"
                    className="video-shortcuts-trigger"
                    aria-haspopup="dialog"
                    aria-expanded={shortcutsOpen}
                    onClick={() => {
                      setSpeedMenuOpen(false);
                      setShortcutsOpen((open) => !open);
                    }}
                  >
                    快捷键
                  </button>
                  {shortcutsOpen && (
                    <div
                      className="video-player-popover video-shortcuts-popover"
                      role="dialog"
                      aria-label="播放器快捷键"
                    >
                      <span>
                        <kbd>Space</kbd> / <kbd>K</kbd> 播放或暂停
                      </span>
                      <span>
                        <kbd>J</kbd> / <kbd>L</kbd> 后退或前进 10 秒
                      </span>
                      <span>
                        短按 <kbd>←</kbd> / <kbd>→</kbd> 后退或前进 5 秒
                      </span>
                      <span>
                        长按 <kbd>←</kbd> / <kbd>→</kbd> 临时倒退或快进
                      </span>
                      <span>
                        按住方向键时用 <kbd>↑</kbd> / <kbd>↓</kbd> 调整临时速度
                      </span>
                      <span>
                        <kbd>[</kbd> / <kbd>]</kbd> 降低或提高基础倍速
                      </span>
                      <span>
                        <kbd>Shift</kbd> + <kbd>,</kbd> / <kbd>.</kbd> 调整倍速
                      </span>
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}
          <Tabs
            value={learningTab}
            onValueChange={(value) => setLearningTab(value as LearningTab)}
          >
            <TabsList className="video-learning-tabs" aria-label="学习工具">
              <TabsTrigger value="summary">讲义</TabsTrigger>
              <TabsTrigger value="transcript">字幕</TabsTrigger>
              <TabsTrigger value="notes">主动练习</TabsTrigger>
              <TabsTrigger value="pdf">课件</TabsTrigger>
            </TabsList>
            {selectedJob && (
              <TranscriptQualityWarning quality={selectedJob.quality} />
            )}
            {(["transcript", "summary", "pdf", "notes"] as LearningTab[]).map(
              (tab) => (
                <TabsContent key={tab} value={tab} className="video-tool-panel">
                  {renderLearningContent(tab)}
                </TabsContent>
              ),
            )}
          </Tabs>
        </div>

        <aside
          ref={listPanelRef}
          className="video-list-panel"
          aria-label="课程录像"
          tabIndex={-1}
        >
          <div className="video-list-header">
            <div>
              <strong>课程录像</strong>
              <span>{sourceVideos.length} 节</span>
            </div>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setSelectionMode((current) => !current);
                setSelectedIds(new Set());
              }}
            >
              {selectionMode ? "退出选择" : "批量选择"}
            </Button>
          </div>
          {selectionMode && (
            <section className="video-batch-bar" aria-label="批量操作">
              <strong>已选择 {selectedIds.size} 项</strong>
              <div>
                <Button
                  variant="outline"
                  size="sm"
                  loading={busy === "transcript:batch"}
                  disabled={
                    !onStartTranscript ||
                    transcriptEligible.length === 0 ||
                    Boolean(busy)
                  }
                  onClick={() =>
                    void startTranscript(
                      transcriptEligible,
                      "transcript:batch",
                      `开始整理 ${transcriptEligible.length} 节，跳过 ${selectedVideos.length - transcriptEligible.length} 节已完成/处理中。`,
                    )
                  }
                >
                  <NotebookPen aria-hidden="true" />
                  整理所选学习材料
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  loading={busy === "pdf:batch"}
                  disabled={
                    !onCreateSlidesPdf ||
                    pdfEligible.length === 0 ||
                    Boolean(busy)
                  }
                  onClick={() => void createPdfs()}
                >
                  <FileText aria-hidden="true" />
                  导出所选课件 PDF
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setSelectionMode(false);
                    setSelectedIds(new Set());
                  }}
                >
                  <X aria-hidden="true" />
                  退出选择
                </Button>
              </div>
              <p>已整理和处理中的录像会自动跳过，避免重复创建任务。</p>
            </section>
          )}
          <div
            className="video-status-filters"
            role="group"
            aria-label="整理状态筛选"
          >
            {(
              [
                ["all", "全部", sourceVideos.length],
                ["pending", "未整理", sourceOrganizationCounts.pending],
                ["processing", "处理中", sourceOrganizationCounts.processing],
                ["completed", "已整理", sourceOrganizationCounts.completed],
                ["partial", "部分完成", sourceOrganizationCounts.partial],
                ["failed", "失败", sourceOrganizationCounts.failed],
              ] as const
            ).map(([value, label, count]) => (
              <button
                key={value}
                type="button"
                className={count === 0 ? "is-zero" : undefined}
                aria-pressed={organizationFilter === value}
                onClick={() => setOrganizationFilter(value)}
              >
                {label} <strong>{count}</strong>
              </button>
            ))}
            {selectionMode && filtered.length > 0 && (
              <label className="video-select-all">
                <input
                  type="checkbox"
                  aria-label="选择当前筛选的全部录像"
                  checked={filtered.every((video) => selectedIds.has(video.id))}
                  ref={(node) => {
                    if (node)
                      node.indeterminate =
                        filtered.some((video) => selectedIds.has(video.id)) &&
                        !filtered.every((video) => selectedIds.has(video.id));
                  }}
                  onChange={() =>
                    setSelectedIds((current) => {
                      const next = new Set(current);
                      const allSelected = filtered.every((video) =>
                        next.has(video.id),
                      );
                      for (const video of filtered)
                        allSelected
                          ? next.delete(video.id)
                          : next.add(video.id);
                      return next;
                    })
                  }
                />
                全选
              </label>
            )}
          </div>
          {loading ? (
            <div className="video-list-skeleton" aria-label="正在加载录像列表">
              {[0, 1, 2, 3].map((item) => (
                <div className="video-recording-skeleton" key={item}>
                  <span />
                  <div>
                    <span />
                    <span />
                    <span />
                  </div>
                </div>
              ))}
            </div>
          ) : permissionDenied ? (
            <div className="video-list-empty">
              <strong>当前账号无权查看课程录像</strong>
              <span>请检查视频平台登录状态或课程成员身份。</span>
              {onRetry && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => void onRetry()}
                >
                  重新检查登录
                </Button>
              )}
            </div>
          ) : error ? (
            <div className="video-list-empty">
              <strong>录像列表加载失败</strong>
              <span>{error}</span>
              {onRetry && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => void onRetry()}
                >
                  重新加载
                </Button>
              )}
            </div>
          ) : videos.length === 0 ? (
            <div className="video-list-empty">
              <strong>这门课程还没有录像</strong>
              <span>同步课程后可再次检查，或向教师确认录制状态。</span>
              {onRetry && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => void onRetry()}
                >
                  重新加载
                </Button>
              )}
            </div>
          ) : filtered.length === 0 ? (
            <div className="video-list-empty">
              <span>当前来源没有视频</span>
              <Button variant="link" size="sm" onClick={() => setSource("all")}>
                查看全部来源
              </Button>
            </div>
          ) : (
            <ul className="video-recording-list">
              {filtered.map((video) => {
                const job = jobsByVideo.get(video.id);
                const learningState = stateForVideo(video.id);
                const meta = [
                  video.teachingClass?.trim() &&
                  video.teachingClass.trim() !== video.courseName.trim()
                    ? video.teachingClass.trim()
                    : null,
                  video.classroom,
                  formatRecordedAt(video.recordedAt),
                ].filter(Boolean);
                const isCurrent =
                  selected?.id === video.id && selected.source === video.source;
                const canShowTranscriptDetails = Boolean(
                  job && onRetryTranscript && onRevealTranscript,
                );
                const rowSummaryPending = pendingSummarySourceId === video.id;
                const rowSummaryProcessing =
                  rowSummaryPending || transcriptIsProcessing(job);
                const rowSummaryReady =
                  transcriptIsComplete(job) && phase1IsReady(job);
                const rowSummaryFailed = transcriptNeedsRetry(job);
                const rowSummaryEligible =
                  video.source === "video_space" && video.supportsSubtitle;
                const canRequestRowSummary = rowSummaryFailed
                  ? Boolean(onRetryTranscript)
                  : Boolean(rowSummaryEligible && onStartTranscript);
                const showSummaryAction =
                  rowSummaryReady ||
                  rowSummaryProcessing ||
                  canRequestRowSummary;
                const hasMenu = Boolean(
                  onStartTranscript ||
                    onRetryTranscript ||
                    learningState.complete ||
                    showSummaryAction ||
                    canShowTranscriptDetails ||
                    (video.supportsSubtitle &&
                      (onLoadSubtitles ||
                        onDownloadSubtitle ||
                        onStartTranscript)) ||
                    (onCreateSlidesPdf && video.supportsSlidesPdf) ||
                    (onDownload && video.downloadable !== false),
                );
                return (
                  <li
                    key={`${video.source}:${video.id}`}
                    className={isCurrent ? "is-current" : ""}
                    aria-current={isCurrent ? "true" : undefined}
                  >
                    {selectionMode && (
                      <input
                        type="checkbox"
                        aria-label={`选择 ${video.title}`}
                        checked={selectedIds.has(video.id)}
                        onChange={() => toggleVideo(video.id)}
                      />
                    )}
                    <div className="video-recording-copy">
                      <div className="video-recording-title-row">
                        <strong title={video.originalTitle || video.title}>
                          {video.title}
                        </strong>
                        {busy === `organize:${video.id}` && (
                          <span className="video-row-organizing" role="status">
                            <LoaderCircle aria-hidden="true" />
                            正在整理…
                          </span>
                        )}
                        {hasMenu && (
                          <div className="video-more">
                            <Button
                              ref={(node) => {
                                if (node)
                                  menuTriggerRefs.current.set(video.id, node);
                                else menuTriggerRefs.current.delete(video.id);
                              }}
                              variant="ghost"
                              size="icon"
                              aria-label={`${video.title} 更多操作`}
                              aria-haspopup="menu"
                              aria-expanded={openMenuId === video.id}
                              onMouseDown={(event) => event.stopPropagation()}
                              onKeyDown={(event) => {
                                if (event.key !== "ArrowDown") return;
                                event.preventDefault();
                                setOpenMenuId(video.id);
                                positionVideoMenu(video.id);
                                window.queueMicrotask(() =>
                                  menuRef.current
                                    ?.querySelector<HTMLElement>(
                                      '[role="menuitem"]:not(:disabled)',
                                    )
                                    ?.focus(),
                                );
                              }}
                              onClick={() => {
                                if (openMenuId === video.id) {
                                  closeVideoMenu();
                                  return;
                                }
                                setOpenMenuId(video.id);
                                positionVideoMenu(video.id);
                              }}
                            >
                              <MoreHorizontal aria-hidden="true" />
                            </Button>
                            {openMenuId === video.id &&
                              menuPosition &&
                              createPortal(
                                <div
                                  ref={menuRef}
                                  className={`video-more-menu is-open-${menuPosition.direction}`}
                                  style={{
                                    top: menuPosition.top,
                                    left: menuPosition.left,
                                  }}
                                  data-video-menu={video.id}
                                  role="menu"
                                  aria-label={`${video.title} 操作`}
                                  onMouseDown={(event) =>
                                    event.stopPropagation()
                                  }
                                  onKeyDown={(event) => {
                                    if (
                                      ![
                                        "ArrowDown",
                                        "ArrowUp",
                                        "Home",
                                        "End",
                                      ].includes(event.key)
                                    )
                                      return;
                                    const items = Array.from(
                                      event.currentTarget.querySelectorAll<HTMLButtonElement>(
                                        '[role="menuitem"]:not(:disabled)',
                                      ),
                                    );
                                    if (!items.length) return;
                                    event.preventDefault();
                                    const current = items.indexOf(
                                      document.activeElement as HTMLButtonElement,
                                    );
                                    const next =
                                      event.key === "Home"
                                        ? 0
                                        : event.key === "End"
                                          ? items.length - 1
                                          : event.key === "ArrowUp"
                                            ? (current - 1 + items.length) %
                                              items.length
                                            : (current + 1) % items.length;
                                    items[next]?.focus();
                                  }}
                                >
                                  {(onStartTranscript || onRetryTranscript) && (
                                    <button
                                      type="button"
                                      role="menuitem"
                                      disabled={
                                        learningState.complete ||
                                        rowSummaryProcessing ||
                                        Boolean(busy) ||
                                        !(job && transcriptNeedsRetry(job)
                                          ? onRetryTranscript
                                          : onStartTranscript)
                                      }
                                      onClick={() => {
                                        setOpenMenuId(null);
                                        void organizeVideo(video, job);
                                      }}
                                    >
                                      {busy === `organize:${video.id}` ? (
                                        <LoaderCircle aria-hidden="true" />
                                      ) : (
                                        <NotebookPen aria-hidden="true" />
                                      )}
                                      {busy === `organize:${video.id}`
                                        ? "正在整理…"
                                        : rowSummaryFailed
                                          ? "重试整理学习材料"
                                          : "整理学习材料"}
                                    </button>
                                  )}
                                  {canShowTranscriptDetails && (
                                    <button
                                      type="button"
                                      role="menuitem"
                                      onClick={() => {
                                        setDetailInitialTab("cleaned");
                                        setDetailJob(job || null);
                                        closeVideoMenu();
                                      }}
                                    >
                                      <Captions aria-hidden="true" />
                                      {rowSummaryReady
                                        ? "查看字幕详情"
                                        : "查看处理详情"}
                                    </button>
                                  )}
                                  {video.supportsSubtitle &&
                                    onLoadSubtitles && (
                                      <button
                                        type="button"
                                        role="menuitem"
                                        disabled={Boolean(busy)}
                                        onClick={() => {
                                          closeVideoMenu();
                                          void loadSubtitles(video);
                                        }}
                                      >
                                        <Captions aria-hidden="true" />
                                        载入播放器字幕
                                      </button>
                                    )}
                                  {showSummaryAction && (
                                    <button
                                      type="button"
                                      role="menuitem"
                                      disabled={
                                        Boolean(busy) || rowSummaryProcessing
                                      }
                                      onClick={() => {
                                        setOpenMenuId(null);
                                        setSelected(video);
                                        setLearningTab("summary");
                                        if (rowSummaryReady && job) {
                                          setDetailInitialTab("summary");
                                          setDetailJob(job);
                                        } else void requestSummary(video, job);
                                      }}
                                    >
                                      <FileText aria-hidden="true" />
                                      {rowSummaryReady
                                        ? "查看 AI 总结"
                                        : rowSummaryFailed
                                          ? "重试生成 AI 总结"
                                          : rowSummaryProcessing
                                            ? "AI 总结生成中"
                                            : "生成 AI 总结"}
                                    </button>
                                  )}
                                  {learningState.complete && (
                                    <button
                                      type="button"
                                      role="menuitem"
                                      disabled={Boolean(busy)}
                                      onClick={() => {
                                        setOpenMenuId(null);
                                        void viewLearningMaterials(video);
                                      }}
                                    >
                                      <NotebookPen aria-hidden="true" />
                                      查看学习材料
                                    </button>
                                  )}
                                  {onCreateSlidesPdf &&
                                    video.supportsSlidesPdf && (
                                      <button
                                        type="button"
                                        role="menuitem"
                                        disabled={Boolean(busy)}
                                        onClick={() => {
                                          setOpenMenuId(null);
                                          void createPdf(video);
                                        }}
                                      >
                                        <FileText aria-hidden="true" />
                                        生成课件 PDF
                                      </button>
                                    )}
                                  {video.supportsSubtitle &&
                                    !onLoadSubtitles &&
                                    onDownloadSubtitle &&
                                    video.subtitleId && (
                                      <button
                                        type="button"
                                        role="menuitem"
                                        disabled={Boolean(busy)}
                                        onClick={() => {
                                          setOpenMenuId(null);
                                          void run(
                                            `subtitle:${video.id}`,
                                            "字幕下载任务已创建。",
                                            () => onDownloadSubtitle(video),
                                          );
                                        }}
                                      >
                                        <Download aria-hidden="true" />
                                        下载字幕
                                      </button>
                                    )}
                                  {onDownload &&
                                    video.downloadable !== false && (
                                      <button
                                        type="button"
                                        role="menuitem"
                                        disabled={Boolean(busy)}
                                        onClick={() => {
                                          setOpenMenuId(null);
                                          void run(
                                            `video:${video.id}`,
                                            "视频下载任务已创建。",
                                            () => onDownload(video),
                                          );
                                        }}
                                      >
                                        <Download aria-hidden="true" />
                                        下载视频
                                      </button>
                                    )}
                                </div>,
                                document.body,
                              )}
                          </div>
                        )}
                      </div>
                      <span title={meta.join(" · ")}>
                        {meta.join(" · ") || "录制信息未知"}
                      </span>
                      <div className="video-recording-footer">
                        <div className="video-recording-state">
                          <span className="video-source-badge">
                            {sourceLabels[video.source]}
                          </span>
                          <span>{formatDuration(video.duration)}</span>
                          {learningState.label ? (
                            <span className="video-state-label video-state-label-failed">
                              {learningState.label}
                            </span>
                          ) : (
                            <StatusBadge status={learningState.status} />
                          )}
                        </div>
                        <Button
                          className="video-play-action"
                          variant="link"
                          size="sm"
                          loading={busy === `play:${video.id}`}
                          disabled={
                            video.playable === false ||
                            Boolean(busy && busy !== `play:${video.id}`)
                          }
                          onClick={() => void play(video)}
                        >
                          <Play aria-hidden="true" />
                          播放
                        </Button>
                      </div>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </aside>
      </section>

      <section
        className={`video-jobs ${currentJobs.length === 0 && !historyOpen ? "is-empty" : ""}`}
        aria-labelledby="video-jobs-title"
      >
        <div className="video-jobs-summary">
          <div className="video-jobs-heading">
            <strong id="video-jobs-title">任务状态</strong>
            {currentJobs.length === 0 ? (
              <span>暂无进行中的任务</span>
            ) : (
              <span>
                {activeJobs.length} 项进行中
                {attentionJobs.length > 0
                  ? ` · ${attentionJobs.length} 项需关注`
                  : ""}
              </span>
            )}
          </div>
          <button
            type="button"
            className="video-history-trigger"
            aria-expanded={historyOpen}
            disabled={historyJobs.length === 0}
            onClick={() => setHistoryOpen((open) => !open)}
          >
            历史任务 {historyJobs.length}
            <ChevronDown aria-hidden="true" />
          </button>
        </div>
        {currentJobs.length > 0 && (
          <div className="video-job-list" aria-label="当前任务">
            {currentJobs.map((job) => (
              <article key={job.id} aria-label={`${job.title} ${job.kind}`}>
                <div>
                  <strong>{job.title}</strong>
                  <span>{job.kind}</span>
                </div>
                <div
                  className="video-job-progress"
                  role="progressbar"
                  aria-label={`${job.title}进度`}
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={Math.max(0, Math.min(100, job.progress))}
                >
                  <span
                    style={{
                      width: `${Math.max(0, Math.min(100, job.progress))}%`,
                    }}
                  />
                </div>
                <StatusBadge status={job.status} />
                <div className="video-job-actions">
                  {job.transcript &&
                    onCancelTranscript &&
                    [
                      "queued",
                      "fetching",
                      "waiting_remote",
                      "waiting_for_ai",
                      "organizing",
                      "cancelling",
                    ].includes(job.rawStatus) && (
                      <Button
                        variant="link"
                        size="sm"
                        onClick={() =>
                          void run(
                            `transcript-cancel:${job.id}`,
                            "任务已取消。",
                            () =>
                              onCancelTranscript(
                                job.transcript as TranscriptJob,
                              ),
                          )
                        }
                      >
                        取消
                      </Button>
                    )}
                  {job.task &&
                    onCancelTask &&
                    ["queued", "running", "cancelling"].includes(
                      job.rawStatus,
                    ) && (
                      <Button
                        variant="link"
                        size="sm"
                        onClick={() =>
                          void run(`task:${job.id}`, "任务已取消。", () =>
                            onCancelTask(job.task as VideoTaskItem),
                          )
                        }
                      >
                        取消
                      </Button>
                    )}
                  {job.transcript &&
                    onRetryTranscript &&
                    onRevealTranscript && (
                      <Button
                        variant="link"
                        size="sm"
                        onClick={() => {
                          setDetailInitialTab("summary");
                          setDetailJob(job.transcript || null);
                        }}
                      >
                        详情
                      </Button>
                    )}
                </div>
              </article>
            ))}
          </div>
        )}
        {historyOpen && historyJobs.length > 0 && (
          <div className="video-job-history" aria-label="历史任务列表">
            {historyJobs.slice(0, 10).map((job) => (
              <article key={job.id} aria-label={`${job.title} ${job.kind}`}>
                <div>
                  <strong>{job.title}</strong>
                  <span>{job.kind}</span>
                </div>
                <StatusBadge status={job.status} />
                {job.transcript && onRetryTranscript && onRevealTranscript && (
                  <Button
                    variant="link"
                    size="sm"
                    onClick={() => {
                      setDetailInitialTab("summary");
                      setDetailJob(job.transcript || null);
                    }}
                  >
                    详情
                  </Button>
                )}
              </article>
            ))}
            {historyJobs.length > 10 && <p>仅显示最近10条</p>}
          </div>
        )}
      </section>

      {detailJob && onRetryTranscript && onRevealTranscript && (
        <TranscriptDetailDrawer
          job={detailJob}
          initialTab={detailInitialTab}
          onClose={() => setDetailJob(null)}
          onRetry={onRetryTranscript}
          onCancel={onCancelTranscript || (() => undefined)}
          onReveal={onRevealTranscript}
        />
      )}
    </main>
  );
}
