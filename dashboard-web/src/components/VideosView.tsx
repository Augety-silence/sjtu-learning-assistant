import {
  Captions,
  Check,
  ChevronDown,
  CircleAlert,
  Download,
  FileText,
  LoaderCircle,
  Maximize2,
  Minimize2,
  MoreHorizontal,
  Move,
  Play,
  X,
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
import { useVideoPdfSync } from "@/components/useVideoPdfSync";
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
import { MetaTag } from "@/components/ui/CompactControls";
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
  syncIssue?: string | null;
  onRetrySync?: () => void;
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
type LearningTab = "transcript" | "summary" | "pdf";
type VideoMenuPosition = {
  top: number;
  left: number;
  direction: "up" | "down";
};
type CoursePopupPosition = VideoMenuPosition & {
  width: number;
  maxHeight: number;
};
export function videoOrganizationReason(video: CourseVideoItem) {
  if (video.source !== "video_space")
    return "当前仅支持整理视频空间的课堂字幕；此来源尚未接入字幕整理。";
  if (!video.supportsSubtitle)
    return "此录像没有可用字幕，暂不能同步课堂资料。";
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
type TimestampedJob = {
  createdAt?: string | null;
  updatedAt?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
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

function formatDuration(seconds?: number | null) {
  if (
    seconds === null ||
    seconds === undefined ||
    !Number.isFinite(seconds) ||
    seconds < 0
  )
    return "时长未知";
  const total = Math.floor(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  return hours
    ? `${hours}:${String(minutes).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`
    : `${minutes}:${String(total % 60).padStart(2, "0")}`;
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
  const current = options.find((option) => option.id === value);

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
    } else if (event.key === "Tab") {
      event.preventDefault();
      close(true);
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
  syncIssue,
  onRetrySync,
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
  const [detailJob, setDetailJob] = useState<TranscriptJob | null>(null);
  const [detailInitialTab, setDetailInitialTab] = useState<
    "summary" | "practice" | "cleaned"
  >("summary");
  const [preparationBusy, setPreparationBusy] = useState(false);
  const [preparationAttempt, setPreparationAttempt] = useState(0);
  const [syncRetryBusy, setSyncRetryBusy] = useState(false);
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
  const [playbackRate, setPlaybackRate] = useState<PlaybackRate>(1);
  const [speedMenuOpen, setSpeedMenuOpen] = useState(false);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [preservedAcrossSource, setPreservedAcrossSource] = useState(false);
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
  const operationsRef = useRef(new Map<string, symbol>());
  const operationGenerationRef = useRef(0);
  const playBusyRef = useRef<string | null>(null);
  const [readVideo] = useState(() => createBoundedReadPool());
  const playbackWaitRef = useRef<AbortController | null>(null);
  const subtitleWaitRef = useRef<AbortController | null>(null);
  const subtitleReadRequest = useRef(0);
  const isBusy = (key: string) => operationKeys.has(key);
  const playerLoadedRef = useRef(false);
  const playbackRestoreRef = useRef<{
    currentTime: number;
    paused: boolean;
    playbackRate: number;
  } | null>(null);
  const playbackRateRef = useRef<PlaybackRate>(1);
  const preparedVideosRef = useRef(new Set<string>());
  const syncedPdf = useVideoPdfSync(
    selected,
    Boolean(playbackUrl),
    courseId,
    onCreateSlidesPdf,
    preparationAttempt,
  );
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
    if (
      !selected ||
      !playbackUrl ||
      !prepare ||
      videoOrganizationReason(selected)
    )
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
      if (!onUpdatePlayback || videoOrganizationReason(video)) return;
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
  const sourceVideos = useMemo(
    () =>
      source === "all"
        ? videos
        : videos.filter((video) => video.source === source),
    [source, videos],
  );
  const filtered = sourceVideos;
  const selectedJob = selected ? jobsByVideo.get(selected.id) : undefined;
  const selectedSummaryProcessing = transcriptIsProcessing(selectedJob);
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

  useEffect(() => {
    const valid = new Set(videos.map((video) => video.id));
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
    setLearningTab("summary");
    setDetailJob(null);
    setOpenMenuId(null);
    setMenuPosition(null);
    setSpeedMenuOpen(false);
    setShortcutsOpen(false);
    if (feedbackTimer.current) window.clearTimeout(feedbackTimer.current);
    feedbackTimer.current = null;
    setPlayerFeedback(null);
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
    if (!speedMenuOpen && !shortcutsOpen) return;
    const close = (event: MouseEvent) => {
      if (!playerToolsRef.current?.contains(event.target as Node)) {
        setSpeedMenuOpen(false);
        setShortcutsOpen(false);
      }
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      setSpeedMenuOpen(false);
      setShortcutsOpen(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", closeOnEscape);
    };
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
    const updateFullscreen = () => {
      const active = Boolean(document.fullscreenElement);
      setPlayerFullscreen(active);
      if (!active) setPortalFullscreen(false);
    };
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
      const key = `${courseId ?? "unscoped"}:${selected.id}:${page.index}`;
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
        event.isComposing ||
        isInteractiveTarget(event.target) ||
        document.querySelector(
          "[data-modal-layer]:not([aria-hidden='true'])",
        ) !== null
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
        (key === " " || key === "space" || key === "spacebar" || key === "k") &&
        // Holding the key must not fire repeated play/pause toggles.
        !event.repeat
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

  const applySubtitleResult = (result: VideoPlaybackResult) => {
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
          applySubtitleResult(result);
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
          applySubtitleResult(result);
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
        if (isRelevant()) applySubtitleResult(result);
      },
      (message) => setSubtitleMessage(message),
      [`subtitle:${video.id}`],
      isRelevant,
    );
  };

  const failMediaLoad = () => {
    playerLoadedRef.current = false;
    autoPlaySourceId.current = null;
    setPlaybackUrl(null);
    setPlaybackError(
      "录像加载失败：播放地址可能已过期或格式不受支持，请重新播放。",
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
        applySubtitleResult({
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
              applySubtitleResult(subtitleResult);
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

  const renderLearningContent = (tab: LearningTab) => {
    if (!selected)
      return <p className="video-tool-empty">选择录像开始观看。</p>;
    if (
      tab === "summary" &&
      onLoadLearningSegments &&
      !videoOrganizationReason(selected)
    )
      return (
        <VideoSynchronizedLearning
          quiet
          video={selected}
          courseId={courseId}
          playbackTime={playbackTime}
          refreshing={preparationBusy || selectedSummaryProcessing}
          revision={`${selectedJob?.id ?? ""}:${selectedJob?.status ?? ""}:${preparationBusy}:${preparationAttempt}`}
          onLoad={onLoadLearningSegments}
          onSeek={seekToTime}
        />
      );
    if (tab === "pdf") {
      const pdfResult =
        syncedPdf.result ??
        taskPdfResult(
          selectedTasks.find(
            (task) => task.kind === "slides_pdf" && task.status === "completed",
          ),
        );
      const open =
        pdfResult?.open ??
        (pdfResult?.fileUrl || pdfResult?.url
          ? () => {
              window.open(pdfResult.fileUrl ?? pdfResult.url ?? "", "_blank");
            }
          : undefined);
      return open ? (
        <Button
          variant="ghost"
          size="sm"
          onClick={() => {
            void Promise.resolve()
              .then(open)
              .catch((reason) =>
                showToast({
                  kind: "error",
                  message:
                    reason instanceof Error ? reason.message : "无法打开课件。",
                }),
              );
          }}
        >
          打开课件 PDF
        </Button>
      ) : (
        <p className="video-tool-empty">课件可用时会自动同步。</p>
      );
    }
    if (tab === "transcript" && subtitleVtt)
      return (
        <pre className="video-raw-subtitle">
          {subtitleCues.map((cue) => cue.text).join("\n\n")}
        </pre>
      );
    if (selectedJob)
      return (
        <Button
          variant="ghost"
          size="sm"
          onClick={() => {
            setDetailInitialTab(tab === "transcript" ? "cleaned" : "summary");
            setDetailJob(selectedJob);
          }}
        >
          {tab === "transcript" ? "阅读原始字幕" : "阅读课堂总结"}
        </Button>
      );
    return (
      <p className="video-tool-empty">
        {tab === "transcript"
          ? "字幕可用时会自动同步。"
          : "课堂资料可用时会自动同步。"}
      </p>
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
          onError={(event) => {
            // Code 1 is MEDIA_ERR_ABORTED: switching videos / unmounting is not
            // a playback failure and must not tear down the new playback URL.
            if (event.currentTarget.error?.code === 1) return;
            failMediaLoad();
          }}
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
          <source
            src={playbackUrl}
            onError={(event) => {
              // Ignore aborts from switching sources; real network/decode
              // failures surface on the <video> element with a concrete code.
              const mediaError = event.currentTarget
                .parentElement as HTMLVideoElement | null;
              if ((mediaError?.error?.code ?? 2) === 1) return;
              failMediaLoad();
            }}
          />
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
          <Button
            variant="outline"
            size="sm"
            onClick={() => void play(selected)}
          >
            重新播放
          </Button>
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
            {loading ? "正在加载…" : `${videos.length} 节`}
          </span>
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
                            } else if (event.key === "Tab") {
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
            <div className="video-material-toolbar">
              <TabsList className="video-learning-tabs" aria-label="学习工具">
                <TabsTrigger value="summary">讲义</TabsTrigger>
                <TabsTrigger value="transcript">字幕</TabsTrigger>
                <TabsTrigger value="pdf">课件</TabsTrigger>
              </TabsList>
              <Button
                variant="ghost"
                size="sm"
                disabled={
                  (!selected && !onRetrySync) ||
                  preparationBusy ||
                  syncedPdf.busy ||
                  syncRetryBusy
                }
                title={
                  (preparationError &&
                  preparationError.sourceId === selected?.id
                    ? preparationError.message
                    : syncedPdf.error) ||
                  syncIssue ||
                  (subtitleStatus === "error" ? subtitleMessage : null) ||
                  "保留已同步内容，从断点重试字幕与课件"
                }
                onClick={() => {
                  onRetrySync?.();
                  if (!selected) return;
                  preparedVideosRef.current.delete(
                    `${courseId ?? "unscoped"}:${selected.id}`,
                  );
                  if (
                    selectedJob &&
                    transcriptNeedsRetry(selectedJob) &&
                    onRetryTranscript
                  ) {
                    const sourceId = selected.id;
                    const generation = operationGenerationRef.current;
                    setSyncRetryBusy(true);
                    void Promise.resolve()
                      .then(() => onRetryTranscript(selectedJob))
                      .catch((reason) => {
                        if (
                          operationGenerationRef.current === generation &&
                          selectedVideoRef.current?.id === sourceId
                        )
                          setPreparationError({
                            sourceId,
                            message:
                              reason instanceof Error
                                ? reason.message
                                : "同步重试失败。",
                          });
                      })
                      .finally(() => {
                        if (operationGenerationRef.current !== generation)
                          return;
                        setSyncRetryBusy(false);
                        setPreparationAttempt((value) => value + 1);
                      });
                  } else setPreparationAttempt((value) => value + 1);
                }}
              >
                重试同步
              </Button>
            </div>
            {(["transcript", "summary", "pdf"] as LearningTab[]).map((tab) => (
              <TabsContent key={tab} value={tab} className="video-tool-panel">
                {renderLearningContent(tab)}
              </TabsContent>
            ))}
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
              <Button
                variant="link"
                size="sm"
                onClick={() => {
                  changeSource("all");
                }}
              >
                {source === "all" ? "清除状态筛选" : "查看全部来源"}
              </Button>
            </div>
          ) : (
            <ul className="video-recording-list">
              {filtered.map((video) => {
                const job = jobsByVideo.get(video.id);
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
                const rowSummaryReady = transcriptHasLearningMaterials(job);
                const rowIsLoading = busy === `play:${video.id}`;
                const hasMenu = Boolean(
                  canShowTranscriptDetails ||
                    (video.supportsSubtitle &&
                      (onLoadSubtitles || onDownloadSubtitle)) ||
                    (onDownload && video.downloadable !== false),
                );
                return (
                  <li
                    key={`${video.source}:${video.id}`}
                    className={isCurrent ? "is-current" : ""}
                    aria-current={isCurrent ? "true" : undefined}
                  >
                    {
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
                    }
                    <div className="video-recording-copy">
                      <div className="video-recording-title-row">
                        <strong title={video.originalTitle || video.title}>
                          {video.title}
                        </strong>

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
                                    if (event.key === "Tab") {
                                      event.preventDefault();
                                      closeVideoMenu();
                                      menuTriggerRefs.current
                                        .get(video.id)
                                        ?.focus();
                                      return;
                                    }
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
                                  {job && (
                                    <button
                                      type="button"
                                      role="menuitem"
                                      onClick={() => {
                                        closeVideoMenu();
                                        selectLearningVideo(video);
                                        setLearningTab("summary");
                                      }}
                                    >
                                      <FileText aria-hidden="true" />
                                      查看学习材料
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
