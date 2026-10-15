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
  Maximize2,
  Minimize2,
  MoreHorizontal,
  Move,
  NotebookPen,
  Play,
  X,
  XCircle,
} from "lucide-react";
import {
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import { ContentReveal } from "@/components/ContentReveal";
import { useToast } from "@/components/Toast";
import { TranscriptDetailDrawer } from "@/components/TranscriptDetailDrawer";
import { TranscriptQualityWarning } from "@/components/TranscriptPhase1Panels";
import {
  type VideoLearningSegmentsResult,
  type VideoPlaybackPosition,
  VideoSynchronizedLearning,
} from "@/components/VideoSynchronizedLearning";

export type {
  VideoLearningSegment,
  VideoLearningSegmentsResult,
  VideoPlaybackPosition,
} from "@/components/VideoSynchronizedLearning";

import { Button } from "@/components/ui/Button";
import {
  type CompactControlTone,
  StatusBadge as CompactStatusBadge,
  FilterChip,
  MetaTag,
} from "@/components/ui/CompactControls";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/Tabs";
import { createBoundedReadPool } from "@/lib/boundedReadPool";
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
  kind?: "inline" | "external_opened";
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

export interface VideoSlidePage {
  index: number;
  timeMs: number | null;
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
  aiReadiness?: {
    status?: "checking" | "ready" | "blocked" | "unknown";
    configured: boolean;
    message: string;
    model: string;
    connectionVerified?: boolean | null;
  };
  onOpenAISettings?: () => void;
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
  onLoadSlides?: (
    video: CourseVideoItem,
  ) => Promise<{ status: string; message: string; pages: VideoSlidePage[] }>;
  onLoadSlideImage?: (
    video: CourseVideoItem,
    pageIndex: number,
  ) => Promise<string>;
  onPrepareVideo?: (
    video: CourseVideoItem,
    position: VideoPlaybackPosition,
  ) => Promise<void>;
  onUpdatePlayback?: (
    video: CourseVideoItem,
    position: VideoPlaybackPosition,
  ) => Promise<void>;
  onLoadLearningSegments?: (
    video: CourseVideoItem,
    signal?: AbortSignal,
  ) => Promise<VideoLearningSegmentsResult>;
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
type CoursePopupPosition = VideoMenuPosition & {
  width: number;
  maxHeight: number;
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
export function videoOrganizationReason(video: CourseVideoItem) {
  if (video.source !== "video_space")
    return "当前仅支持整理视频空间的课堂字幕；此来源尚未接入字幕整理。";
  if (!video.supportsSubtitle)
    return "此录像没有可用字幕，暂不能生成讲义、练习或 AI 校对。";
  return null;
}

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

type WebkitFullscreenVideo = HTMLVideoElement & {
  webkitDisplayingFullscreen?: boolean;
  webkitEnterFullscreen?: () => void;
  webkitExitFullscreen?: () => void;
};

type SlidePaneRect = {
  left: number;
  top: number;
  width: number;
  height: number;
};
type SlidePanePointer = {
  mode: "drag" | "resize";
  x: number;
  y: number;
  rect: SlidePaneRect;
};

type SubtitleCue = { startMs: number; endMs: number; text: string };

function parseVttTime(value: string) {
  const parts = value.trim().replace(",", ".").split(":");
  if (parts.length < 2 || parts.length > 3) return null;
  const seconds = Number(parts.pop());
  const minutes = Number(parts.pop());
  const hours = parts.length ? Number(parts.pop()) : 0;
  if (![hours, minutes, seconds].every(Number.isFinite)) return null;
  return Math.round(((hours * 60 + minutes) * 60 + seconds) * 1000);
}

function parseVttCues(value: string | null) {
  if (!value) return [] as SubtitleCue[];
  return value
    .replace(/^WEBVTT[^\n]*\n+/i, "")
    .split(/\n\s*\n/)
    .flatMap((block) => {
      const lines = block.trim().split(/\r?\n/);
      const timingIndex = lines.findIndex((line) => line.includes("-->"));
      if (timingIndex < 0) return [];
      const [start, end] = lines[timingIndex].split("-->");
      const startMs = parseVttTime(start);
      const endMs = parseVttTime(end.trim().split(/\s+/)[0]);
      const text = lines
        .slice(timingIndex + 1)
        .join(" ")
        .replace(/<[^>]*>/g, "")
        .trim();
      return startMs === null || endMs === null || !text
        ? []
        : [{ startMs, endMs, text }];
    });
}

function formatVideoClock(seconds: number) {
  const value = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  const remainder = value % 60;
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`
    : `${minutes}:${String(remainder).padStart(2, "0")}`;
}

function clampSlidePaneRect(rect: SlidePaneRect): SlidePaneRect {
  const margin = 12;
  const maxWidth = Math.max(320, window.innerWidth - margin * 2);
  const maxHeight = Math.max(220, window.innerHeight - margin * 2);
  const width = Math.min(maxWidth, Math.max(320, rect.width));
  const height = Math.min(maxHeight, Math.max(220, rect.height));
  return {
    width,
    height,
    left: Math.min(
      Math.max(margin, rect.left),
      window.innerWidth - width - margin,
    ),
    top: Math.min(
      Math.max(margin, rect.top),
      window.innerHeight - height - margin,
    ),
  };
}
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

function isActiveTranscriptStatus(
  status: string,
  progress: number,
  reviewStatus?: string | null,
) {
  return (
    (progress < 100 || reviewStatus === "running") &&
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
  const processing = isActiveTranscriptStatus(
    rawStatus,
    job.progress,
    job.review_status,
  );
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
    label:
      rawStatus === "waiting_remote" && !processing
        ? "字幕未就绪，可重试"
        : failed && hasHistoricalComplete
          ? "已有材料 · 更新失败"
          : undefined,
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
  return Boolean(
    job &&
      isActiveTranscriptStatus(job.status, job.progress, job.review_status),
  );
}

function transcriptHasLearningMaterials(job?: TranscriptJob) {
  return Boolean(
    job &&
      job.progress >= 100 &&
      ["completed", "completed_with_warnings"].includes(job.status),
  );
}

function phase1IsReady(job?: TranscriptJob) {
  const status = job?.phase1_status ?? job?.pipeline_status;
  return status === "completed" || status === "completed_with_warnings";
}

function transcriptNeedsRetry(job?: TranscriptJob) {
  if (!job) return false;
  const phase1Status = job.phase1_status ?? job.pipeline_status;
  return (
    (job.status === "waiting_remote" && job.progress >= 100) ||
    ["partial", "failed", "interrupted", "completed_with_warnings"].includes(
      job.status,
    ) ||
    phase1Status === "partial" ||
    phase1Status === "failed" ||
    transcriptQualityRisk(job) ||
    (job.status === "completed" && !phase1IsReady(job))
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
  const tone: CompactControlTone =
    status === "completed"
      ? "success"
      : status === "failed"
        ? "danger"
        : status === "partial"
          ? "warning"
          : [
                "processing",
                "queued",
                "transcribing",
                "generating",
                "reviewing",
              ].includes(status)
            ? "info"
            : "neutral";
  return (
    <CompactStatusBadge
      className={`video-status video-status-${status}`}
      icon={Icon}
      tone={tone}
    >
      {statusCopy[status]}
    </CompactStatusBadge>
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
  const popupRef = useRef<HTMLDivElement>(null);
  const optionRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const selectedIndex = Math.max(
    0,
    options.findIndex((option) => option.id === value),
  );
  const [open, setOpen] = useState(false);
  const [popupPosition, setPopupPosition] =
    useState<CoursePopupPosition | null>(null);
  const [activeIndex, setActiveIndex] = useState(selectedIndex);
  const current = options[selectedIndex];

  useEffect(() => setActiveIndex(selectedIndex), [selectedIndex]);
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      const target = event.target as Node;
      if (
        !rootRef.current?.contains(target) &&
        !popupRef.current?.contains(target)
      )
        setOpen(false);
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
    setPopupPosition(null);
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
  const positionPopup = () => {
    const trigger = triggerRef.current;
    if (!trigger) return;
    const rect = trigger.getBoundingClientRect();
    const viewport = window.visualViewport;
    const viewportTop = viewport?.offsetTop ?? 0;
    const viewportLeft = viewport?.offsetLeft ?? 0;
    const viewportWidth = viewport?.width ?? window.innerWidth;
    const viewportHeight = viewport?.height ?? window.innerHeight;
    const edge = 8;
    const gap = 6;
    const width = Math.min(Math.max(280, rect.width), viewportWidth - edge * 2);
    const desiredHeight = Math.min(320, options.length * 44 + 10);
    const below = viewportTop + viewportHeight - rect.bottom - edge - gap;
    const above = rect.top - viewportTop - edge - gap;
    const direction =
      below < Math.min(desiredHeight, 220) && above > below ? "up" : "down";
    const maxHeight = Math.max(
      96,
      Math.min(desiredHeight, direction === "down" ? below : above),
    );
    const left = Math.min(
      Math.max(rect.left, viewportLeft + edge),
      viewportLeft + viewportWidth - width - edge,
    );
    setPopupPosition({
      direction,
      width,
      maxHeight,
      left,
      top:
        direction === "down"
          ? rect.bottom + gap
          : Math.max(viewportTop + edge, rect.top - gap - maxHeight),
    });
  };
  const openPopup = () => {
    positionPopup();
    setOpen(true);
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
      openPopup();
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
        onClick={() => (open ? close() : openPopup())}
        onKeyDown={onTriggerKeyDown}
      >
        <span title={current?.name ?? fallback}>
          {current?.name ?? fallback}
        </span>
        <ChevronDown aria-hidden="true" />
      </button>
      {open &&
        popupPosition &&
        createPortal(
          <div
            ref={popupRef}
            id={`${id}-options`}
            className={`video-course-popup is-open-${popupPosition.direction}`}
            style={{
              top: popupPosition.top,
              left: popupPosition.left,
              width: popupPosition.width,
              maxHeight: popupPosition.maxHeight,
            }}
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
          </div>,
          document.body,
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
  onLoadSlides,
  onLoadSlideImage,
  onPrepareVideo,
  onUpdatePlayback,
  onLoadLearningSegments,
  onDownload,
  onDownloadSubtitle,
  onCreateSlidesPdf,
  onCancelTask,
  onStartTranscript,
  onRetryTranscript,
  onCancelTranscript,
  onRevealTranscript,
  aiReadiness,
}: VideosViewProps) {
  const [source, setSource] = useState<"all" | VideoSource>("all");
  const [selected, setSelected] = useState<CourseVideoItem | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  const [playbackUrl, setPlaybackUrl] = useState<string | null>(null);
  const [playbackError, setPlaybackError] = useState<string | null>(null);
  const [externalPlaybackSourceId, setExternalPlaybackSourceId] = useState<
    string | null
  >(null);
  const [subtitleUrl, setSubtitleUrl] = useState<string | null>(null);
  const [subtitleMessage, setSubtitleMessage] = useState<string | null>(null);
  const [subtitleVtt, setSubtitleVtt] = useState<string | null>(null);
  const [subtitleStatus, setSubtitleStatus] =
    useState<VideoPlaybackResult["subtitleStatus"]>();
  const [playbackTime, setPlaybackTime] = useState(0);
  const [playerFullscreen, setPlayerFullscreen] = useState(false);
  const [portalFullscreen, setPortalFullscreen] = useState(false);
  const [splitPaneOpen, setSplitPaneOpen] = useState(false);
  const [slideStatus, setSlideStatus] = useState<
    "idle" | "loading" | "processing" | "ready" | "empty" | "error"
  >("idle");
  const [slideMessage, setSlideMessage] = useState("");
  const [slidePages, setSlidePages] = useState<VideoSlidePage[]>([]);
  const [slideImageUrl, setSlideImageUrl] = useState<string | null>(null);
  const [slideImageLoading, setSlideImageLoading] = useState(false);
  const [slideImageError, setSlideImageError] = useState("");
  const [slidePaneRect, setSlidePaneRect] = useState<SlidePaneRect>(() => ({
    left: Math.max(16, window.innerWidth - 540),
    top: Math.max(16, window.innerHeight - 430),
    width: 520,
    height: 390,
  }));
  const [panePointerActive, setPanePointerActive] = useState(false);
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
  const [preparationBusy, setPreparationBusy] = useState(false);
  const [preparationAttempt, setPreparationAttempt] = useState(0);
  const [preparationError, setPreparationError] = useState<{
    sourceId: string;
    message: string;
  }>();
  const [openMenuId, setOpenMenuId] = useState<string | null>(null);
  const [menuPosition, setMenuPosition] = useState<VideoMenuPosition | null>(
    null,
  );
  const [busy, setBusy] = useState<string | null>(null);
  const [operationKeys, setOperationKeys] = useState<Set<string>>(new Set());
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
  const playerShellRef = useRef<HTMLDivElement>(null);
  const slideMetadataRequestRef = useRef(0);
  const slideImageRequestRef = useRef(0);
  const slideImageCacheRef = useRef(new Map<string, Promise<string>>());
  const slidePanePointerRef = useRef<SlidePanePointer | null>(null);
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
  const operationsRef = useRef(new Map<string, symbol>());
  const operationGenerationRef = useRef(0);
  const playBusyRef = useRef<string | null>(null);
  const [readVideo] = useState(() => createBoundedReadPool());
  const playbackWaitRef = useRef<AbortController | null>(null);
  const subtitleWaitRef = useRef<AbortController | null>(null);
  const subtitleReadRequest = useRef(0);
  const isBusy = (key: string) => operationKeys.has(key);
  const isOrganizing = (id: string) => isBusy(`learning:${id}`);
  const playerLoadedRef = useRef(false);
  const playbackRestoreRef = useRef<{
    currentTime: number;
    paused: boolean;
    playbackRate: number;
  } | null>(null);
  const playbackRateRef = useRef<PlaybackRate>(1);
  const preparedVideosRef = useRef(new Set<string>());
  const selectedVideoRef = useRef(selected);
  selectedVideoRef.current = selected;
  const lastPlaybackUpdateRef = useRef<
    { sourceId: string; at: number } | undefined
  >(undefined);
  const lastSubtitleJobRevisionRef = useRef("");
  const prepareVideoRef = useRef(onPrepareVideo);
  prepareVideoRef.current = onPrepareVideo;
  const hasPrepareVideo = Boolean(onPrepareVideo);
  const subtitleLoaderRef = useRef(onLoadSubtitles);
  subtitleLoaderRef.current = onLoadSubtitles;
  const hasSubtitleLoader = Boolean(onLoadSubtitles);
  const temporaryPlaybackRef = useRef<TemporaryPlaybackState | null>(null);
  const { showToast } = useToast();
  const organizationReason = useCallback(
    (video: CourseVideoItem) =>
      videoOrganizationReason(video) ??
      (aiReadiness?.configured === false
        ? aiReadiness.status === "checking"
          ? "正在检查 AI 配置，请稍候。"
          : aiReadiness.status === "unknown"
            ? "AI 状态尚未确认，请重新检查配置。"
            : aiReadiness.message || "尚未配置 AI，先配置后再生成学习材料。"
        : null),
    [aiReadiness?.configured, aiReadiness?.message, aiReadiness?.status],
  );
  useEffect(() => {
    setPreparationBusy(false);
    const prepare = prepareVideoRef.current;
    if (!selected || !playbackUrl || !prepare || organizationReason(selected))
      return;
    const key = `${courseId ?? "unscoped"}:${selected.id}`;
    if (preparedVideosRef.current.has(key)) return;
    preparedVideosRef.current.add(key);
    let active = true;
    setPreparationBusy(true);
    setPreparationError(undefined);
    void prepare(selected, {
      positionSeconds: 0,
      playbackRate: playbackRateRef.current,
    })
      .catch((reason) => {
        preparedVideosRef.current.delete(key);
        if (active)
          setPreparationError({
            sourceId: selected.id,
            message:
              reason instanceof Error
                ? reason.message
                : "自动整理暂未启动，可稍后重试。",
          });
      })
      .finally(() => {
        if (active) setPreparationBusy(false);
      });
    return () => {
      active = false;
    };
  }, [
    courseId,
    hasPrepareVideo,
    organizationReason,
    playbackUrl,
    preparationAttempt,
    selected?.id,
  ]);
  const notifyPlayback = useCallback(
    (
      video: CourseVideoItem,
      position: VideoPlaybackPosition,
      force = false,
    ) => {
      if (!onUpdatePlayback || organizationReason(video)) return;
      const last = lastPlaybackUpdateRef.current;
      if (!force && last?.sourceId === video.id && Date.now() - last.at < 5000)
        return;
      lastPlaybackUpdateRef.current = { sourceId: video.id, at: Date.now() };
      void onUpdatePlayback(video, position).catch((reason) => {
        if (selectedVideoRef.current?.id === video.id)
          setPreparationError({
            sourceId: video.id,
            message:
              reason instanceof Error
                ? reason.message
                : "播放进度同步失败，正在使用已有学习材料。",
          });
      });
    },
    [onUpdatePlayback, organizationReason],
  );
  const seekToTime = useCallback(
    (seconds: number) => {
      const media = videoRef.current;
      const video = selectedVideoRef.current;
      if (!media || !video || !Number.isFinite(seconds)) return;
      media.currentTime = Math.min(
        Number.isFinite(media.duration) ? media.duration : Infinity,
        Math.max(0, seconds),
      );
      setPlaybackTime(media.currentTime);
      notifyPlayback(
        video,
        {
          positionSeconds: media.currentTime,
          playbackRate: media.playbackRate,
        },
        true,
      );
      setDetailJob(null);
    },
    [notifyPlayback],
  );

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
    return (
      !organizationReason(video) &&
      !isCompletedForBatch(job) &&
      !transcriptIsProcessing(job)
    );
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
        return (
          !organizationReason(video) && !state.complete && !state.processing
        );
      }),
    [organizationReason, stateForVideo, videos],
  );
  const skippedOrganizationCount = videos.length - unfinishedVideos.length;
  const selectedJob = selected ? jobsByVideo.get(selected.id) : undefined;
  const selectedSummaryPending = Boolean(
    selected && pendingSummarySourceId === selected.id,
  );
  const selectedSummaryProcessing =
    selectedSummaryPending || transcriptIsProcessing(selectedJob);
  const selectedSummaryReady = transcriptHasLearningMaterials(selectedJob);
  const selectedSummaryFailed = transcriptNeedsRetry(selectedJob);
  const selectedTasks = selected
    ? tasks.filter((task) => task.videoId === selected.id)
    : [];
  const subtitleCues = useMemo(() => parseVttCues(subtitleVtt), [subtitleVtt]);
  const currentSubtitle = useMemo(
    () =>
      subtitleCues.find(
        (cue) =>
          cue.startMs <= playbackTime * 1000 &&
          cue.endMs >= playbackTime * 1000,
      )?.text ?? "",
    [playbackTime, subtitleCues],
  );
  const timedSlidePages = useMemo(
    () => slidePages.filter((page) => page.timeMs !== null),
    [slidePages],
  );
  const activeSlidePage = useMemo(() => {
    if (timedSlidePages.length === 0) return null;
    let active = timedSlidePages[0];
    for (const page of timedSlidePages) {
      if ((page.timeMs ?? Number.POSITIVE_INFINITY) > playbackTime * 1000)
        break;
      active = page;
    }
    return active;
  }, [playbackTime, timedSlidePages]);
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
        progress:
          job.review_status === "running" && (job.review_total ?? 0) > 0
            ? Math.round(
                ((job.review_completed ?? 0) / (job.review_total ?? 1)) * 100,
              )
            : job.progress,
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
    operationGenerationRef.current += 1;
    preparedVideosRef.current.clear();
    lastPlaybackUpdateRef.current = undefined;
    lastSubtitleJobRevisionRef.current = "";
    setPreparationBusy(false);
    setPreparationError(undefined);
    playbackWaitRef.current?.abort();
    subtitleWaitRef.current?.abort();
    subtitleReadRequest.current += 1;
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
    setExternalPlaybackSourceId(null);
    setSubtitleUrl(null);
    setSubtitleMessage(null);
    setSubtitleVtt(null);
    setSubtitleStatus(undefined);
    setPlaybackTime(0);
    setSplitPaneOpen(false);
    setSlideStatus("idle");
    setSlidePages([]);
    setSlideImageUrl(null);
    slideImageCacheRef.current.clear();
    slideMetadataRequestRef.current += 1;
    slideImageRequestRef.current += 1;
    setPreservedAcrossSource(false);
    setBusy(null);
    playBusyRef.current = null;
    operationsRef.current.clear();
    setOperationKeys(new Set());
    setPdfResults(new Map());
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
    if (transcriptHasLearningMaterials(requestedJob)) {
      if (selected?.id === pendingSummarySourceId) {
        setDetailInitialTab("summary");
        setDetailJob(requestedJob);
      }
      setPendingSummarySourceId(null);
      summaryRequestedJobId.current = null;
      return;
    }
    if (transcriptNeedsRetry(requestedJob)) {
      setPendingSummarySourceId(null);
      summaryRequestedJobId.current = null;
    }
  }, [jobsByVideo, pendingSummarySourceId, selected?.id, transcriptJobs]);
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
  useEffect(() => {
    if (window.pywebview?.api) return;
    const updateFullscreen = () =>
      setPlayerFullscreen(Boolean(document.fullscreenElement));
    const media = videoRef.current as WebkitFullscreenVideo | null;
    const onWebkitFullscreen = () => {
      const active = Boolean(media?.webkitDisplayingFullscreen);
      setPlayerFullscreen(active);
      if (!active) setPortalFullscreen(false);
    };
    document.addEventListener("fullscreenchange", updateFullscreen);
    media?.addEventListener("webkitbeginfullscreen", onWebkitFullscreen);
    media?.addEventListener("webkitendfullscreen", onWebkitFullscreen);
    return () => {
      document.removeEventListener("fullscreenchange", updateFullscreen);
      media?.removeEventListener("webkitbeginfullscreen", onWebkitFullscreen);
      media?.removeEventListener("webkitendfullscreen", onWebkitFullscreen);
    };
  }, [playbackUrl]);
  useEffect(() => {
    if (!splitPaneOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !document.fullscreenElement) {
        setSplitPaneOpen(false);
      }
    };
    const keepPaneInViewport = () =>
      setSlidePaneRect((current) => clampSlidePaneRect(current));
    document.addEventListener("keydown", closeOnEscape);
    window.addEventListener("resize", keepPaneInViewport);
    return () => {
      document.removeEventListener("keydown", closeOnEscape);
      window.removeEventListener("resize", keepPaneInViewport);
    };
  }, [splitPaneOpen]);
  useEffect(() => {
    const pointer = slidePanePointerRef.current;
    if (!pointer) return;
    const onPointerMove = (event: PointerEvent) => {
      const dx = event.clientX - pointer.x;
      const dy = event.clientY - pointer.y;
      const next =
        pointer.mode === "drag"
          ? {
              ...pointer.rect,
              left: pointer.rect.left + dx,
              top: pointer.rect.top + dy,
            }
          : {
              ...pointer.rect,
              width: pointer.rect.width + dx,
              height: pointer.rect.height + dy,
            };
      setSlidePaneRect(clampSlidePaneRect(next));
    };
    const stopPointer = () => {
      slidePanePointerRef.current = null;
      setPanePointerActive(false);
    };
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", stopPointer, { once: true });
    window.addEventListener("pointercancel", stopPointer, { once: true });
    return () => {
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", stopPointer);
      window.removeEventListener("pointercancel", stopPointer);
    };
  }, [panePointerActive]);
  useEffect(() => {
    if (
      !splitPaneOpen ||
      slideStatus !== "ready" ||
      !selected ||
      !activeSlidePage ||
      !onLoadSlideImage
    ) {
      setSlideImageUrl(null);
      setSlideImageLoading(false);
      return;
    }
    let active = true;
    const request = ++slideImageRequestRef.current;
    setSlideImageLoading(true);
    setSlideImageError("");
    const loadCachedPage = (page: VideoSlidePage) => {
      const key = `${selected.id}:${page.index}`;
      let pending = slideImageCacheRef.current.get(key);
      if (!pending) {
        pending = readVideo(
          `slide:${courseId ?? "unscoped"}:${key}`,
          () => onLoadSlideImage(selected, page.index),
          new AbortController().signal,
          "课件页面读取超时，请重新打开 PPT 分屏。",
        ).catch((reason) => {
          slideImageCacheRef.current.delete(key);
          throw reason;
        });
        slideImageCacheRef.current.set(key, pending);
        if (slideImageCacheRef.current.size > 12) {
          const oldest = slideImageCacheRef.current.keys().next().value;
          if (oldest) slideImageCacheRef.current.delete(oldest);
        }
      }
      return pending;
    };
    void loadCachedPage(activeSlidePage)
      .then((url) => {
        if (active && slideImageRequestRef.current === request)
          setSlideImageUrl(url);
      })
      .catch((reason: unknown) => {
        if (active && slideImageRequestRef.current === request) {
          setSlideImageUrl(null);
          setSlideImageError(
            reason instanceof Error ? reason.message : "课件页面读取失败。",
          );
        }
      })
      .finally(() => {
        if (active && slideImageRequestRef.current === request)
          setSlideImageLoading(false);
      });
    const nextPage =
      timedSlidePages[timedSlidePages.indexOf(activeSlidePage) + 1];
    if (nextPage) void loadCachedPage(nextPage).catch(() => undefined);
    return () => {
      active = false;
    };
  }, [
    activeSlidePage,
    courseId,
    onLoadSlideImage,
    readVideo,
    selected,
    slideStatus,
    splitPaneOpen,
    timedSlidePages,
  ]);

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
  const beginSlidePanePointer = useCallback(
    (mode: "drag" | "resize", event: ReactPointerEvent<HTMLElement>) => {
      if (event.button !== 0) return;
      event.preventDefault();
      slidePanePointerRef.current = {
        mode,
        x: event.clientX,
        y: event.clientY,
        rect: slidePaneRect,
      };
      setPanePointerActive(true);
    },
    [slidePaneRect],
  );
  const togglePlayerFullscreen = useCallback(async () => {
    const shell = playerShellRef.current as
      | (HTMLDivElement & {
          webkitRequestFullscreen?: () => Promise<void> | void;
        })
      | null;
    const media = videoRef.current as WebkitFullscreenVideo | null;
    const savePlaybackState = () => {
      playbackRestoreRef.current = media
        ? {
            currentTime: media.currentTime,
            paused: media.paused,
            playbackRate: media.playbackRate,
          }
        : null;
      autoPlaySourceId.current = null;
    };
    const enterPortalFullscreen = () => {
      savePlaybackState();
      setPortalFullscreen(true);
      setPlayerFullscreen(true);
    };
    if (window.pywebview?.api) {
      if (portalFullscreen) {
        savePlaybackState();
        setPortalFullscreen(false);
        setPlayerFullscreen(false);
      } else {
        enterPortalFullscreen();
      }
      return;
    }
    if (
      playerFullscreen &&
      !document.fullscreenElement &&
      !media?.webkitDisplayingFullscreen
    ) {
      savePlaybackState();
      setPortalFullscreen(false);
      setPlayerFullscreen(false);
      return;
    }
    try {
      if (media?.webkitDisplayingFullscreen && media.webkitExitFullscreen) {
        media.webkitExitFullscreen();
      } else if (document.fullscreenElement === shell) {
        await document.exitFullscreen();
      } else if (shell?.requestFullscreen) {
        await shell.requestFullscreen();
        if (document.fullscreenElement !== shell) enterPortalFullscreen();
      } else if (shell?.webkitRequestFullscreen) {
        await shell.webkitRequestFullscreen();
        if (document.fullscreenElement !== shell) enterPortalFullscreen();
      } else if (media?.webkitEnterFullscreen) {
        media.webkitEnterFullscreen();
      } else {
        enterPortalFullscreen();
      }
    } catch {
      if (media?.webkitEnterFullscreen) {
        try {
          media.webkitEnterFullscreen();
          return;
        } catch {
          enterPortalFullscreen();
          return;
        }
      }
      enterPortalFullscreen();
    }
  }, [playerFullscreen, portalFullscreen, showPlayerFeedback]);
  const openSlidePane = useCallback(async () => {
    if (!selected || !onLoadSlides) return;
    const request = ++slideMetadataRequestRef.current;
    setSplitPaneOpen(true);
    setSlideStatus("loading");
    setSlideMessage("正在读取课件时间信息…");
    setSlidePages([]);
    setSlideImageUrl(null);
    setSlideImageError("");
    try {
      const result = await readVideo(
        `slides:${courseId ?? "unscoped"}:${selected.id}`,
        () => onLoadSlides(selected),
        new AbortController().signal,
        "课件时间信息读取超时，请重新打开 PPT 分屏。",
      );
      if (slideMetadataRequestRef.current !== request) return;
      setSlidePages(result.pages);
      setSlideStatus(
        result.status === "ready"
          ? "ready"
          : result.status === "processing"
            ? "processing"
            : "empty",
      );
      setSlideMessage(
        result.status === "ready"
          ? result.pages.some((page) => page.timeMs !== null)
            ? "课件将跟随视频时间自动切换。"
            : "课件已就绪，但服务未提供页码时间，无法自动同步。"
          : result.message || "该录像暂无课件。",
      );
    } catch (reason) {
      if (slideMetadataRequestRef.current !== request) return;
      setSlideStatus("error");
      setSlideMessage(
        reason instanceof Error ? reason.message : "课件时间信息读取失败。",
      );
    }
  }, [courseId, onLoadSlides, readVideo, selected]);
  useEffect(() => {
    if (!splitPaneOpen || slideStatus !== "processing") return;
    const timer = window.setTimeout(() => void openSlidePane(), 5000);
    return () => window.clearTimeout(timer);
  }, [openSlidePane, slideStatus, splitPaneOpen]);
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
    playbackWaitRef.current?.abort();
    subtitleWaitRef.current?.abort();
    subtitleReadRequest.current += 1;
    playRequest.current += 1;
    autoPlaySourceId.current = null;
    const invalidatedBusy =
      busy === `play:${selected.id}` || busy === `subtitle:${selected.id}`;
    if (invalidatedBusy) {
      playBusyRef.current = null;
      setBusy(null);
    }
    revokeSubtitleBlob();
    setSelected(null);
    setPlaybackUrl(null);
    setPlaybackError(null);
    setExternalPlaybackSourceId(null);
    setSubtitleUrl(null);
    setSubtitleMessage(null);
    setSubtitleVtt(null);
    setSubtitleStatus(undefined);
    setPlaybackTime(0);
    setSplitPaneOpen(false);
    setSlideStatus("idle");
    setSlidePages([]);
    setSlideImageUrl(null);
    slideMetadataRequestRef.current += 1;
    slideImageRequestRef.current += 1;
    setPreservedAcrossSource(false);
    setLearningTab("summary");
  };
  useEffect(
    () => () => {
      operationGenerationRef.current += 1;
      operationsRef.current.clear();
      playbackWaitRef.current?.abort();
      subtitleWaitRef.current?.abort();
      subtitleReadRequest.current += 1;
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
    action?: (isCurrent: () => boolean) => void | Promise<void>,
    onError?: (message: string) => void,
    resources: string[] = [key],
    isRelevant: () => boolean = () => true,
  ) => {
    const keys = [...new Set([key, ...resources])];
    if (!action || keys.some((item) => operationsRef.current.has(item)))
      return false;
    const generation = operationGenerationRef.current;
    const isCurrent = () =>
      operationGenerationRef.current === generation && isRelevant();
    const operation = Symbol(key);
    for (const item of keys) operationsRef.current.set(item, operation);
    setOperationKeys(new Set(operationsRef.current.keys()));
    try {
      await action(isCurrent);
      if (!isCurrent()) return false;
      showToast({ kind: "success", message: label });
      return true;
    } catch (reason) {
      if (!isCurrent()) return false;
      const message = reason instanceof Error ? reason.message : "视频操作失败";
      onError?.(message);
      showToast({ kind: "error", message });
      return false;
    } finally {
      for (const item of keys) {
        if (operationsRef.current.get(item) === operation)
          operationsRef.current.delete(item);
      }
      if (operationGenerationRef.current === generation)
        setOperationKeys(new Set(operationsRef.current.keys()));
    }
  };

  const selectLearningVideo = (video: CourseVideoItem) => {
    if (selected?.id !== video.id) {
      stopTemporaryPlayback();
      videoRef.current?.pause();
      playbackWaitRef.current?.abort();
      subtitleWaitRef.current?.abort();
      subtitleReadRequest.current += 1;
      playRequest.current += 1;
      autoPlaySourceId.current = null;
      playBusyRef.current = null;
      setBusy(null);
      revokeSubtitleBlob();
      setPlaybackUrl(null);
      setPlaybackError(null);
      setExternalPlaybackSourceId(null);
      setSubtitleUrl(null);
      setSubtitleMessage(null);
      setSubtitleVtt(null);
      setSubtitleStatus(undefined);
      setPlaybackTime(0);
      setSplitPaneOpen(false);
      setSlideStatus("idle");
      setSlidePages([]);
      setSlideImageUrl(null);
      slideMetadataRequestRef.current += 1;
      slideImageRequestRef.current += 1;
      setPreservedAcrossSource(false);
    }
    setSelected(video);
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
    setSubtitleVtt(result.subtitleVtt ?? null);
    setSubtitleStatus(
      result.subtitleStatus ?? (result.subtitleVtt ? "ready" : undefined),
    );
    setSubtitleMessage(result.subtitleMessage ?? null);
  };

  const beginSubtitleRead = (video: CourseVideoItem) => {
    subtitleWaitRef.current?.abort();
    const controller = new AbortController();
    subtitleWaitRef.current = controller;
    const id = ++subtitleReadRequest.current;
    const promise = readVideo(
      `subtitle:${courseId ?? "unscoped"}:${video.id}`,
      () =>
        Promise.resolve(
          subtitleLoaderRef.current ? subtitleLoaderRef.current(video) : {},
        ),
      controller.signal,
      "字幕读取超时，可从录像更多操作重新载入字幕。",
    );
    return { id, promise };
  };
  useEffect(() => {
    if (
      !selected ||
      !playbackUrl ||
      !onLoadSubtitles ||
      !transcriptHasLearningMaterials(selectedJob)
    )
      return;
    const revision = `${selected.id}:${selectedJob?.id}:${selectedJob?.phase1_status ?? selectedJob?.pipeline_status ?? ""}`;
    if (lastSubtitleJobRevisionRef.current === revision) return;
    lastSubtitleJobRevisionRef.current = revision;
    const request = playRequest.current;
    const reading = beginSubtitleRead(selected);
    void reading.promise
      .then((result) => {
        if (
          playRequest.current === request &&
          subtitleReadRequest.current === reading.id
        )
          useSubtitleResult(result);
      })
      .catch(() => {
        if (lastSubtitleJobRevisionRef.current === revision)
          lastSubtitleJobRevisionRef.current = "";
      });
  }, [
    selected?.id,
    selectedJob?.id,
    selectedJob?.status,
    selectedJob?.phase1_status,
    selectedJob?.pipeline_status,
    selectedJob?.progress,
    playbackUrl,
  ]);

  useEffect(() => {
    if (subtitleStatus !== "processing" || !selected || !hasSubtitleLoader)
      return;
    let active = true;
    let attempts = 0;
    let timer: number;
    const request = playRequest.current;
    const schedule = () => {
      timer = window.setTimeout(async () => {
        if (!active || playRequest.current !== request) return;
        const reading = beginSubtitleRead(selected);
        try {
          const result = await reading.promise;
          if (
            !active ||
            playRequest.current !== request ||
            subtitleReadRequest.current !== reading.id
          )
            return;
          useSubtitleResult(result);
          if (result.subtitleStatus !== "processing") return;
        } catch (reason) {
          if (!active || playRequest.current !== request) return;
          setSubtitleMessage(
            reason instanceof Error
              ? reason.message
              : "字幕暂时读取失败，正在重试。",
          );
        }
        attempts += 1;
        if (attempts < 24) schedule();
        else setSubtitleMessage("字幕仍在生成，可稍后从更多操作重新载入。");
      }, 5000);
    };
    schedule();
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [selected?.id, subtitleStatus, hasSubtitleLoader]);

  const loadSubtitles = (video: CourseVideoItem) => {
    selectLearningVideo(video);
    const request = playRequest.current;
    let subtitleId: number | undefined;
    const isRelevant = () =>
      playRequest.current === request &&
      subtitleReadRequest.current === subtitleId;
    return run(
      `subtitle:${video.id}`,
      "字幕状态已更新。",
      async () => {
        setLearningTab("transcript");
        setSubtitleMessage("正在加载字幕…");
        const reading = beginSubtitleRead(video);
        subtitleId = reading.id;
        const result = await reading.promise;
        if (isRelevant()) useSubtitleResult(result);
      },
      (message) => setSubtitleMessage(message),
      [`subtitle:${video.id}`],
      isRelevant,
    );
  };

  const play = async (video: CourseVideoItem, shouldAutoPlay = true) => {
    if (video.playable === false || playBusyRef.current === video.id) return;
    stopTemporaryPlayback();
    videoRef.current?.pause();
    playerLoadedRef.current = false;
    playbackWaitRef.current?.abort();
    subtitleWaitRef.current?.abort();
    subtitleReadRequest.current += 1;
    const controller = new AbortController();
    playbackWaitRef.current = controller;
    const request = ++playRequest.current;
    revokeSubtitleBlob();
    setSelected(video);
    setPreservedAcrossSource(false);
    autoPlaySourceId.current = shouldAutoPlay ? video.id : null;
    setPlaybackUrl(null);
    setPlaybackError(null);
    setExternalPlaybackSourceId(null);
    setSubtitleUrl(null);
    setSubtitleVtt(null);
    setSubtitleStatus(undefined);
    setSubtitleMessage(video.supportsSubtitle ? "正在加载字幕…" : null);
    setPlaybackTime(0);
    setSplitPaneOpen(false);
    setSlideStatus("idle");
    setSlidePages([]);
    setSlideImageUrl(null);
    slideMetadataRequestRef.current += 1;
    slideImageRequestRef.current += 1;
    playBusyRef.current = video.id;
    setBusy(`play:${video.id}`);
    try {
      const result = await readVideo(
        `play:${courseId ?? "unscoped"}:${video.id}`,
        () => Promise.resolve(onPlay?.(video)),
        controller.signal,
        "播放地址读取超时，可重新选择播放；若仍无响应，请重新打开应用。",
      );
      if (playRequest.current !== request) return;
      if (
        result &&
        typeof result !== "string" &&
        result.kind === "external_opened"
      ) {
        autoPlaySourceId.current = null;
        setPlaybackUrl(null);
        setPlaybackError(null);
        setExternalPlaybackSourceId(video.id);
        setSubtitleUrl(null);
        setSubtitleMessage(null);
        return;
      }
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
        const reading = beginSubtitleRead(video);
        void reading.promise
          .then((subtitleResult) => {
            if (
              playRequest.current === request &&
              subtitleReadRequest.current === reading.id
            )
              useSubtitleResult(subtitleResult);
          })
          .catch((reason) => {
            if (
              playRequest.current === request &&
              subtitleReadRequest.current === reading.id
            )
              setSubtitleMessage(
                reason instanceof Error
                  ? reason.message
                  : "字幕加载失败，可从更多操作重新载入。",
              );
          });
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
      if (playRequest.current === request) {
        playBusyRef.current = null;
        setBusy(null);
      }
    }
  };

  const viewLearningMaterials = (video: CourseVideoItem) => {
    selectLearningVideo(video);
    setLearningTab("summary");
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
    run(
      key,
      label,
      async (isCurrent) => {
        if (!items.length || !onStartTranscript) return;
        const reason = items.map(organizationReason).find(Boolean);
        if (reason) throw new Error(reason);
        await onStartTranscript(items);
        if (isCurrent()) setSelectedIds(new Set());
      },
      undefined,
      items.map((video) => `learning:${video.id}`),
    );
  const organizeVideo = (video: CourseVideoItem, job?: TranscriptJob) => {
    selectLearningVideo(video);
    setLearningTab("summary");
    const reason = organizationReason(video);
    if (reason) {
      showToast({ kind: "error", message: reason });
      return;
    }
    if (
      transcriptIsProcessing(job) ||
      (isCompletedForBatch(job) && !transcriptNeedsRetry(job))
    )
      return;
    if (job && transcriptNeedsRetry(job) && onRetryTranscript)
      return run(
        `organize:${video.id}`,
        "已重新开始整理本节学习材料。",
        () => onRetryTranscript(job),
        undefined,
        [`learning:${video.id}`],
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
      `开始整理 ${unfinishedVideos.length} 节，跳过 ${skippedOrganizationCount} 节已完成、处理中或不支持整理。`,
    );
  const requestSummary = async (
    video: CourseVideoItem,
    job?: TranscriptJob,
  ) => {
    if (transcriptIsProcessing(job) || pendingSummarySourceId === video.id)
      return;
    if (job && transcriptHasLearningMaterials(job)) {
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
    if (
      !action ||
      organizationReason(video) ||
      operationsRef.current.has(`learning:${video.id}`)
    )
      return;

    selectLearningVideo(video);
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
    const generation = operationGenerationRef.current;
    const succeeded = await run(
      `summary:${video.id}`,
      "已开始生成 AI 总结。",
      action,
      (message) => setSummaryRequestError({ sourceId: video.id, message }),
      [`learning:${video.id}`],
    );
    if (operationGenerationRef.current !== generation) return;
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
    run(
      key,
      "课件已生成（临时文件）。",
      async (isCurrent) => {
        const result = await onCreateSlidesPdf?.(video);
        if (isCurrent()) rememberPdfResult(video, result);
      },
      undefined,
      [`pdf:${video.id}`],
    );
  const createPdfs = () =>
    run(
      "pdf:batch",
      "所选课件已生成（临时文件）。",
      async (isCurrent) => {
        for (const video of pdfEligible) {
          const result = await onCreateSlidesPdf?.(video);
          if (isCurrent()) rememberPdfResult(video, result);
        }
        if (isCurrent()) setSelectedIds(new Set());
      },
      undefined,
      pdfEligible.map((video) => `pdf:${video.id}`),
    );

  const renderLearningContent = (tab: LearningTab) => {
    if (!selected)
      return (
        <p className="video-tool-empty">选择右侧录像后，这里会显示学习材料。</p>
      );
    if (
      tab === "summary" &&
      onLoadLearningSegments &&
      !videoOrganizationReason(selected)
    )
      return (
        <>
          {organizationReason(selected) && (
            <p role="status">{organizationReason(selected)}</p>
          )}
          {preparationError?.sourceId === selected.id && (
            <div role="alert">
              <p>{preparationError.message}</p>
              {onPrepareVideo && !organizationReason(selected) && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => {
                    preparedVideosRef.current.delete(
                      `${courseId ?? "unscoped"}:${selected.id}`,
                    );
                    setPreparationAttempt((attempt) => attempt + 1);
                  }}
                >
                  重试自动整理
                </Button>
              )}
            </div>
          )}
          <VideoSynchronizedLearning
            video={selected}
            courseId={courseId}
            playbackTime={playbackTime}
            refreshing={preparationBusy || selectedSummaryProcessing}
            revision={`${selectedJob?.id ?? ""}:${selectedJob?.status ?? ""}:${selectedJob?.phase1_status ?? selectedJob?.pipeline_status ?? ""}:${preparationBusy}`}
            onLoad={onLoadLearningSegments}
            onSeek={seekToTime}
            onRetry={
              selectedJob && onRetryTranscript
                ? () => onRetryTranscript(selectedJob)
                : undefined
            }
          />
        </>
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
            <span>
              {organizationReason(selected) ??
                "整理后可获得讲义、字幕、主动练习和可跳转时间戳。"}
            </span>
          </div>
          <Button
            variant="outline"
            size="sm"
            loading={isBusy(`organize:${selected.id}`)}
            disabled={
              !onStartTranscript ||
              Boolean(organizationReason(selected)) ||
              isOrganizing(selected.id)
            }
            onClick={() => void organizeVideo(selected)}
          >
            <NotebookPen aria-hidden="true" />
            整理本节学习材料
          </Button>
        </div>
      );
    if (tab === "notes") {
      const canOpenPractice = transcriptHasLearningMaterials(selectedJob);
      return (
        <div className="video-tool-summary">
          <NotebookPen aria-hidden="true" />
          <span>
            {canOpenPractice
              ? "题目与答案分开呈现，答案默认折叠。来源：【自编练习】"
              : (organizationReason(selected) ??
                "完成本节讲义后，将显示本节自测；如无题目会明确提示。")}
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
              loading={isBusy(`pdf:${selected.id}`)}
              disabled={isBusy(`pdf:${selected.id}`)}
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
      const summaryMessage = requestError
        ? `${requestError}。可重试生成。`
        : selectedSummaryReady
          ? deriveVideoLearningState(selectedJob).partial
            ? "学习材料可阅读，AI 校对尚未通过；可在详情中补充或重试。"
            : phase1IsReady(selectedJob)
              ? "学习材料与 AI 校对均已完成，可查看总结。"
              : "学习材料已生成，可查看总结；可在详情中补充 AI 校对。"
          : selectedSummaryFailed
            ? selectedJob?.error ||
              selectedJob?.message ||
              "生成未完成，可从失败阶段重试。"
            : selectedSummaryProcessing
              ? selectedJob?.message ||
                "正在规整字幕并生成学习材料，完成后将自动打开总结。"
              : (organizationReason(selected) ??
                "生成前会先规整字幕，再整理总结与主动练习。");
      const summaryEligible = !organizationReason(selected);
      const canRequestSummary = selectedSummaryFailed
        ? Boolean(summaryEligible && onRetryTranscript)
        : Boolean(summaryEligible && onStartTranscript);
      return (
        <div
          className="video-tool-summary"
          aria-busy={selectedSummaryProcessing || undefined}
          aria-live="polite"
        >
          <StatusBadge
            status={
              requestError
                ? "failed"
                : selectedSummaryReady
                  ? deriveVideoLearningState(selectedJob).status
                  : selectedSummaryFailed
                    ? deriveVideoLearningState(selectedJob).status
                    : selectedSummaryProcessing
                      ? "processing"
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
              disabled={selectedSummaryProcessing || isOrganizing(selected.id)}
              onClick={() => void requestSummary(selected, selectedJob)}
            >
              <Captions aria-hidden="true" />
              {selectedSummaryFailed || requestError
                ? "重试生成 AI 总结"
                : "生成 AI 总结"}
            </Button>
          ) : null}
          {selectedJob?.status === "partial" && !selectedSummaryReady && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                setDetailInitialTab("summary");
                setDetailJob(selectedJob);
              }}
            >
              查看已有结果
            </Button>
          )}
        </div>
      );
    }
    return (
      <div className="video-tool-summary">
        <StatusBadge status={transcriptStatus(selectedJob)} />
        <span>
          {subtitleMessage || selectedJob?.message || "尚无可查看的字幕内容。"}
        </span>
        {selectedJob ? (
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
        ) : onStartTranscript && !organizationReason(selected) ? (
          <Button
            variant="outline"
            size="sm"
            disabled={isOrganizing(selected.id)}
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

  const playerShell = (
    <div
      ref={playerShellRef}
      className={`video-player-shell${portalFullscreen ? " video-player-shell--pseudo-fullscreen" : ""}`}
      data-testid="video-player-shell"
    >
      {playbackUrl ? (
        <video
          key={playbackUrl}
          ref={videoRef}
          controls
          preload="metadata"
          aria-label={selected ? `播放 ${selected.title}` : "课程视频"}
          onPlay={(event) => {
            if (selected)
              notifyPlayback(
                selected,
                {
                  positionSeconds: event.currentTarget.currentTime,
                  playbackRate: event.currentTarget.playbackRate,
                },
                true,
              );
            setPreservedAcrossSource(
              Boolean(
                selected && source !== "all" && selected.source !== source,
              ),
            );
          }}
          onPause={() => setPreservedAcrossSource(false)}
          onEnded={() => setPreservedAcrossSource(false)}
          onTimeUpdate={(event) => {
            setPlaybackTime(event.currentTarget.currentTime);
            if (selected)
              notifyPlayback(selected, {
                positionSeconds: event.currentTarget.currentTime,
                playbackRate: event.currentTarget.playbackRate,
              });
          }}
          onSeeked={(event) => {
            setPlaybackTime(event.currentTarget.currentTime);
            if (selected)
              notifyPlayback(
                selected,
                {
                  positionSeconds: event.currentTarget.currentTime,
                  playbackRate: event.currentTarget.playbackRate,
                },
                true,
              );
          }}
          onRateChange={(event) => {
            if (selected)
              notifyPlayback(
                selected,
                {
                  positionSeconds: event.currentTarget.currentTime,
                  playbackRate: event.currentTarget.playbackRate,
                },
                true,
              );
          }}
          onLoadedMetadata={(event) => {
            playerLoadedRef.current = true;
            const playbackRestore = playbackRestoreRef.current;
            if (playbackRestore) {
              event.currentTarget.currentTime = playbackRestore.currentTime;
              event.currentTarget.playbackRate = playbackRestore.playbackRate;
              playbackRateRef.current =
                playbackRestore.playbackRate as PlaybackRate;
              playbackRestoreRef.current = null;
              autoPlaySourceId.current = null;
              setPlaybackTime(event.currentTarget.currentTime);
              if (!playbackRestore.paused) {
                Promise.resolve(event.currentTarget.play()).catch(
                  () => undefined,
                );
              }
              return;
            }
            event.currentTarget.playbackRate = playbackRateRef.current;
            setPlaybackTime(event.currentTarget.currentTime);
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
        <div className="video-player-empty video-player-loading" role="status">
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
              : externalPlaybackSourceId === selected.id
                ? "已在本地播放器或外部应用打开"
                : "已选择当前录像"}
          </strong>
          <span>
            {busy === `play:${selected.id}`
              ? "正在获取视频地址…"
              : externalPlaybackSourceId === selected.id
                ? "可在外部应用观看录像，或继续使用下方学习工具。"
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
      {portalFullscreen && (
        <button
          type="button"
          className="video-player-fullscreen-exit"
          aria-label="退出全屏"
          onClick={() => void togglePlayerFullscreen()}
        >
          <Minimize2 aria-hidden="true" />
          退出全屏
        </button>
      )}
    </div>
  );

  return (
    <ContentReveal
      as="main"
      ready={!loading}
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
            loading={isBusy("transcript:all")}
            disabled={
              loading ||
              !onStartTranscript ||
              unfinishedVideos.length === 0 ||
              unfinishedVideos.some((video) => isOrganizing(video.id))
            }
            title={
              unfinishedVideos.length === 0
                ? organizationCounts.completed === videos.length &&
                  videos.length > 0
                  ? "全部已整理"
                  : "没有可重复提交的录像"
                : `跳过 ${skippedOrganizationCount} 节已完成、处理中或不支持整理`
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
          {portalFullscreen
            ? createPortal(playerShell, document.body)
            : playerShell}
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
                {playbackUrl && (
                  <button
                    type="button"
                    className="video-player-tool-trigger video-player-tool-button"
                    aria-label={
                      portalFullscreen || playerFullscreen
                        ? "退出全屏"
                        : "全屏播放"
                    }
                    aria-pressed={portalFullscreen || playerFullscreen}
                    onClick={() => void togglePlayerFullscreen()}
                  >
                    {portalFullscreen || playerFullscreen ? (
                      <Minimize2 aria-hidden="true" />
                    ) : (
                      <Maximize2 aria-hidden="true" />
                    )}
                    <span>
                      {portalFullscreen || playerFullscreen
                        ? "退出全屏"
                        : "全屏"}
                    </span>
                  </button>
                )}
                {selected.source === "video_space" &&
                  selected.supportsSlidesPdf &&
                  onLoadSlides &&
                  onLoadSlideImage && (
                    <button
                      type="button"
                      className="video-player-tool-trigger video-player-tool-button"
                      aria-label={
                        splitPaneOpen ? "关闭 PPT 分屏" : "打开 PPT 分屏"
                      }
                      aria-pressed={splitPaneOpen}
                      onClick={() => {
                        if (splitPaneOpen) setSplitPaneOpen(false);
                        else void openSlidePane();
                      }}
                    >
                      <FileText aria-hidden="true" />
                      <span>PPT 分屏</span>
                    </button>
                  )}
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
                  loading={isBusy("transcript:batch")}
                  disabled={
                    !onStartTranscript ||
                    transcriptEligible.length === 0 ||
                    transcriptEligible.some((video) => isOrganizing(video.id))
                  }
                  onClick={() =>
                    void startTranscript(
                      transcriptEligible,
                      "transcript:batch",
                      `开始整理 ${transcriptEligible.length} 节，跳过 ${selectedVideos.length - transcriptEligible.length} 节已完成、处理中或不支持整理。`,
                    )
                  }
                >
                  <NotebookPen aria-hidden="true" />
                  整理所选学习材料
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  loading={isBusy("pdf:batch")}
                  disabled={
                    !onCreateSlidesPdf ||
                    pdfEligible.length === 0 ||
                    pdfEligible.some((video) => isBusy(`pdf:${video.id}`))
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
              <p>
                仅整理视频空间且有字幕的录像；已整理和处理中的录像会自动跳过。
              </p>
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
              <FilterChip
                key={value}
                className={count === 0 ? "is-zero" : undefined}
                pressed={organizationFilter === value}
                count={count}
                aria-label={`${label} ${count}`}
                onClick={() => setOrganizationFilter(value)}
              >
                {label}
              </FilterChip>
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
                const canShowTranscriptDetails = Boolean(job);
                const rowSummaryPending = pendingSummarySourceId === video.id;
                const rowSummaryProcessing =
                  rowSummaryPending || transcriptIsProcessing(job);
                const rowSummaryReady = transcriptHasLearningMaterials(job);
                const rowSummaryFailed = transcriptNeedsRetry(job);
                const rowSummaryEligible = !organizationReason(video);
                const canRequestRowSummary = rowSummaryFailed
                  ? Boolean(rowSummaryEligible && onRetryTranscript)
                  : Boolean(rowSummaryEligible && onStartTranscript);
                const showSummaryAction =
                  rowSummaryReady ||
                  rowSummaryProcessing ||
                  canRequestRowSummary;
                const rowIsLoading = busy === `play:${video.id}`;
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
                    {!selectionMode && (
                      <button
                        type="button"
                        className="video-recording-play-target"
                        aria-label={
                          rowIsLoading
                            ? `正在加载 ${video.title}`
                            : video.playable === false
                              ? `不可播放 ${video.title}`
                              : `播放 ${video.title}`
                        }
                        aria-busy={rowIsLoading ? "true" : undefined}
                        disabled={video.playable === false || rowIsLoading}
                        onClick={() => void play(video)}
                        onKeyDown={(event) => {
                          if (event.key !== "Enter" && event.key !== " ")
                            return;
                          event.preventDefault();
                          void play(video);
                        }}
                      />
                    )}
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
                        {isBusy(`organize:${video.id}`) && (
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
                                  {organizationReason(video) && (
                                    <p role="status">
                                      {organizationReason(video)}
                                    </p>
                                  )}
                                  {(onStartTranscript || onRetryTranscript) && (
                                    <button
                                      type="button"
                                      role="menuitem"
                                      disabled={
                                        Boolean(organizationReason(video)) ||
                                        (learningState.complete &&
                                          !rowSummaryFailed) ||
                                        rowSummaryProcessing ||
                                        isOrganizing(video.id) ||
                                        !(job && transcriptNeedsRetry(job)
                                          ? onRetryTranscript
                                          : onStartTranscript)
                                      }
                                      title={
                                        organizationReason(video) ?? undefined
                                      }
                                      onClick={() => {
                                        setOpenMenuId(null);
                                        void organizeVideo(video, job);
                                      }}
                                    >
                                      {isBusy(`organize:${video.id}`) ? (
                                        <LoaderCircle aria-hidden="true" />
                                      ) : (
                                        <NotebookPen aria-hidden="true" />
                                      )}
                                      {isBusy(`organize:${video.id}`)
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
                                        disabled={isBusy(
                                          `subtitle:${video.id}`,
                                        )}
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
                                        isOrganizing(video.id) ||
                                        rowSummaryProcessing
                                      }
                                      onClick={() => {
                                        setOpenMenuId(null);
                                        selectLearningVideo(video);
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
                                  {rowSummaryReady && (
                                    <button
                                      type="button"
                                      role="menuitem"
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
                                        disabled={isBusy(`pdf:${video.id}`)}
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
                                        disabled={isBusy(
                                          `subtitle:${video.id}`,
                                        )}
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
                                        disabled={isBusy(`video:${video.id}`)}
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
                          <MetaTag className="video-source-badge">
                            {sourceLabels[video.source]}
                          </MetaTag>
                          <span>{formatDuration(video.duration)}</span>
                          {learningState.label ? (
                            <CompactStatusBadge
                              className="video-state-label video-state-label-failed"
                              icon={XCircle}
                              tone="danger"
                            >
                              {learningState.label}
                            </CompactStatusBadge>
                          ) : (
                            <StatusBadge status={learningState.status} />
                          )}
                        </div>
                        <span
                          className="video-play-state"
                          role={rowIsLoading ? "status" : undefined}
                          aria-label={
                            rowIsLoading
                              ? "正在加载"
                              : video.playable === false
                                ? "不可播放"
                                : "播放"
                          }
                          aria-live={rowIsLoading ? "polite" : undefined}
                        >
                          {rowIsLoading ? (
                            <LoaderCircle
                              className="is-loading"
                              aria-hidden="true"
                            />
                          ) : (
                            <Play aria-hidden="true" />
                          )}
                        </span>
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
                      "saved",
                      "reviewing",
                      "waiting_remote",
                      "waiting_for_ai",
                      "organizing",
                      "cancelling",
                    ].includes(job.rawStatus) && (
                      <Button
                        variant="link"
                        size="sm"
                        loading={isBusy(`transcript-cancel:${job.id}`)}
                        disabled={isOrganizing(job.sourceId)}
                        onClick={() =>
                          void run(
                            `transcript-cancel:${job.id}`,
                            "任务已取消。",
                            () =>
                              onCancelTranscript(
                                job.transcript as TranscriptJob,
                              ),
                            undefined,
                            [`learning:${job.sourceId}`],
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
                  {job.transcript && (
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
                {job.transcript && (
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

      {detailJob && (
        <TranscriptDetailDrawer
          job={detailJob}
          initialTab={detailInitialTab}
          onClose={() => setDetailJob(null)}
          onRetry={
            videos.some(
              (video) =>
                video.id === detailJob.source_id && !organizationReason(video),
            )
              ? onRetryTranscript &&
                (async (job) => {
                  const succeeded = await run(
                    `transcript-retry:${job.id}`,
                    "已重新开始整理本节学习材料。",
                    () => onRetryTranscript(job),
                    undefined,
                    [`learning:${job.source_id}`],
                  );
                  if (!succeeded)
                    throw new Error(
                      "本节已有整理操作正在进行，或操作失败，请查看任务状态后重试。",
                    );
                })
              : undefined
          }
          onCancel={
            onCancelTranscript &&
            (async (job) => {
              const succeeded = await run(
                `transcript-cancel:${job.id}`,
                "任务已取消。",
                () => onCancelTranscript(job),
                undefined,
                [`learning:${job.source_id}`],
              );
              if (!succeeded)
                throw new Error(
                  "本节已有任务操作正在进行，或取消失败，请查看任务状态后重试。",
                );
            })
          }
          onReveal={onRevealTranscript}
          onSeek={
            selected?.id === detailJob.source_id && playbackUrl
              ? seekToTime
              : undefined
          }
        />
      )}
      {splitPaneOpen &&
        createPortal(
          <aside
            className="video-slide-pane"
            role="dialog"
            aria-label="PPT 同步分屏"
            style={{
              left: slidePaneRect.left,
              top: slidePaneRect.top,
              width: slidePaneRect.width,
              height: slidePaneRect.height,
            }}
          >
            <header className="video-slide-pane-header">
              <button
                type="button"
                className="video-slide-pane-drag"
                aria-label="拖动 PPT 分屏"
                title="拖动分屏"
                onPointerDown={(event) => beginSlidePanePointer("drag", event)}
                onKeyDown={(event) => {
                  if (!event.altKey || !event.key.startsWith("Arrow")) return;
                  event.preventDefault();
                  const delta = event.shiftKey ? 48 : 16;
                  setSlidePaneRect((current) =>
                    clampSlidePaneRect({
                      ...current,
                      left:
                        current.left +
                        (event.key === "ArrowLeft"
                          ? -delta
                          : event.key === "ArrowRight"
                            ? delta
                            : 0),
                      top:
                        current.top +
                        (event.key === "ArrowUp"
                          ? -delta
                          : event.key === "ArrowDown"
                            ? delta
                            : 0),
                    }),
                  );
                }}
              >
                <Move aria-hidden="true" />
              </button>
              <div className="video-slide-pane-title">
                <strong>PPT 同步分屏</strong>
                <span>{selected?.title ?? "当前录像"}</span>
              </div>
              <button
                type="button"
                className="video-slide-pane-close"
                aria-label="关闭 PPT 分屏"
                onClick={() => setSplitPaneOpen(false)}
              >
                <X aria-hidden="true" />
              </button>
            </header>
            <div className="video-slide-pane-content" aria-live="polite">
              {slideStatus === "loading" || slideStatus === "processing" ? (
                <p className="video-slide-pane-message">{slideMessage}</p>
              ) : slideStatus === "error" || slideStatus === "empty" ? (
                <p className="video-slide-pane-message">{slideMessage}</p>
              ) : slideStatus === "ready" && timedSlidePages.length === 0 ? (
                <p className="video-slide-pane-message">{slideMessage}</p>
              ) : slideImageLoading ? (
                <p className="video-slide-pane-message">正在载入当前课件页…</p>
              ) : slideImageError ? (
                <p className="video-slide-pane-message">{slideImageError}</p>
              ) : slideImageUrl && activeSlidePage ? (
                <img
                  src={slideImageUrl}
                  alt={`${selected?.title ?? "课程视频"}，课件第 ${activeSlidePage.index + 1} 页`}
                />
              ) : (
                <p className="video-slide-pane-message">等待视频时间信息…</p>
              )}
            </div>
            <footer className="video-slide-pane-footer">
              <div>
                <span>视频时间</span>
                <time>{formatVideoClock(playbackTime)}</time>
                {activeSlidePage && (
                  <span>第 {activeSlidePage.index + 1} 页</span>
                )}
              </div>
              <p>{currentSubtitle || "当前时间暂无字幕"}</p>
            </footer>
            <div
              className="video-slide-pane-resize"
              role="separator"
              aria-label="调整 PPT 分屏大小"
              aria-orientation="horizontal"
              tabIndex={0}
              onPointerDown={(event) => beginSlidePanePointer("resize", event)}
              onKeyDown={(event) => {
                if (!event.key.startsWith("Arrow")) return;
                event.preventDefault();
                const delta = event.shiftKey ? 48 : 16;
                setSlidePaneRect((current) =>
                  clampSlidePaneRect({
                    ...current,
                    width:
                      current.width +
                      (event.key === "ArrowLeft"
                        ? -delta
                        : event.key === "ArrowRight"
                          ? delta
                          : 0),
                    height:
                      current.height +
                      (event.key === "ArrowUp"
                        ? -delta
                        : event.key === "ArrowDown"
                          ? delta
                          : 0),
                  }),
                );
              }}
            />
          </aside>,
          playerFullscreen && playerShellRef.current
            ? playerShellRef.current
            : document.body,
        )}
    </ContentReveal>
  );
}
