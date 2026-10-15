import { useCallback, useEffect, useRef, useState } from "react";
import { TranscriptTimedContent } from "@/components/TranscriptPhase1Panels";
import type { CourseVideoItem } from "@/components/VideosView";

export interface VideoPlaybackPosition {
  positionSeconds: number;
  playbackRate: number;
}

export interface VideoLearningSegment {
  id: string;
  startMs: number;
  endMs: number;
  title: string;
  content: string;
  kind?: string;
  source?: string;
  provisional?: boolean;
}

export interface VideoLearningSegmentsResult {
  status: "ready" | "processing" | "empty";
  message?: string;
  segments: VideoLearningSegment[];
  availableRanges?: [number, number][];
  sourceVersion?: string;
  isProcessing?: boolean;
  processingDone?: boolean;
  autoRetryable?: boolean;
  stage?: string | null;
  errorCode?: string | null;
  errorMessage?: string | null;
  retryable?: boolean;
  retryAfter?: string | null;
  updatedAt?: string | null;
  sourceRawSha256?: string | null;
  learningRevision?: string | null;
  completedSegments?: number;
  failedSegments?: number;
}

function formatScheduledRetry(retryAfter?: string | null): string | null {
  if (!retryAfter) return null;
  const retryTime = new Date(retryAfter);
  if (
    !Number.isFinite(retryTime.getTime()) ||
    retryTime.getTime() <= Date.now()
  )
    return null;
  return retryTime.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function VideoSynchronizedLearning({
  video,
  courseId,
  playbackTime,
  refreshing,
  revision,
  onLoad,
  onSeek,
  onRetry,
}: {
  video: CourseVideoItem;
  courseId?: number | null;
  playbackTime: number;
  refreshing: boolean;
  revision?: string;
  onLoad: (
    video: CourseVideoItem,
    signal?: AbortSignal,
  ) => Promise<VideoLearningSegmentsResult>;
  onSeek: (seconds: number) => void;
  onRetry?: () => void | Promise<void>;
}) {
  const [result, setResult] = useState<VideoLearningSegmentsResult>();
  const [error, setError] = useState("");
  const [sourceId, setSourceId] = useState("");
  const [attempt, setAttempt] = useState(0);
  const loadRef = useRef(onLoad);
  const refreshingRef = useRef(refreshing);
  const activeRef = useRef<HTMLElement | null>(null);
  const loadedSourceRef = useRef("");
  const generationRef = useRef(0);
  const visibleRef = useRef(document.visibilityState === "visible");
  loadRef.current = onLoad;
  refreshingRef.current = refreshing;
  const refresh = useCallback(() => setAttempt((value) => value + 1), []);
  useEffect(() => {
    const onVisibility = () => {
      visibleRef.current = document.visibilityState === "visible";
      if (visibleRef.current) refresh();
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, [refresh]);
  useEffect(() => {
    const controller = new AbortController();
    let timer: number | undefined;
    let failures = 0;
    const generation = ++generationRef.current;
    let fingerprint = "";
    if (loadedSourceRef.current !== video.id) setResult(undefined);
    loadedSourceRef.current = video.id;
    setSourceId(video.id);
    setError("");
    const load = async () => {
      try {
        const loaded = await loadRef.current(video, controller.signal);
        if (controller.signal.aborted || generation !== generationRef.current)
          return;
        const nextFingerprint = `${loaded.sourceRawSha256 ?? ""}:${loaded.learningRevision ?? ""}`;
        if (fingerprint && fingerprint !== nextFingerprint) return;
        fingerprint = nextFingerprint;
        const segments = loaded.segments
          .filter(
            (segment) =>
              Number.isFinite(segment.startMs) &&
              Number.isFinite(segment.endMs) &&
              segment.startMs >= 0 &&
              segment.endMs >= segment.startMs,
          )
          .sort((left, right) => left.startMs - right.startMs);
        setResult({ ...loaded, segments });
        setError("");
        failures = 0;
        if (
          loaded.isProcessing ??
          (loaded.status === "processing" || refreshingRef.current)
        ) {
          timer = window.setTimeout(() => {
            if (visibleRef.current) void load();
          }, 5000);
        }
      } catch (reason) {
        if (controller.signal.aborted || generation !== generationRef.current)
          return;
        setError(
          reason instanceof Error ? reason.message : "学习材料读取失败。",
        );
        failures += 1;
      }
    };
    void load();
    return () => {
      controller.abort();
      generationRef.current += 1;
      window.clearTimeout(timer);
    };
  }, [attempt, courseId, revision, video.id]);
  const visible = sourceId === video.id ? result : undefined;
  const scheduledRetryTime =
    visible?.status === "ready" &&
    !visible.isProcessing &&
    visible.autoRetryable === true &&
    visible.errorCode !== "content_uncertain"
      ? formatScheduledRetry(visible.retryAfter)
      : null;
  const segments = visible?.segments ?? [];
  const active = segments.find(
    (segment) =>
      segment.startMs <= playbackTime * 1000 &&
      playbackTime * 1000 < segment.endMs,
  );
  useEffect(() => {
    activeRef.current?.scrollIntoView?.({
      block: "nearest",
      behavior: "smooth",
    });
  }, [active?.id]);
  const ranges = (
    visible?.availableRanges ??
    segments.map(
      (segment) => [segment.startMs, segment.endMs] as [number, number],
    )
  )
    .filter(
      ([start, end]) =>
        Number.isFinite(start) &&
        Number.isFinite(end) &&
        start >= 0 &&
        end >= start,
    )
    .sort((left, right) => left[0] - right[0])
    .reduce<[number, number][]>((merged, [start, end]) => {
      const last = merged.at(-1);
      if (last && start <= last[1]) last[1] = Math.max(last[1], end);
      else merged.push([start, end]);
      return merged;
    }, []);
  const currentRange = ranges.find(
    ([start, end]) => start <= playbackTime * 1000 && playbackTime * 1000 < end,
  );
  const clock = (seconds: number) =>
    `${Math.floor(seconds / 60)
      .toString()
      .padStart(2, "0")}:${Math.floor(seconds % 60)
      .toString()
      .padStart(2, "0")}`;
  return (
    <div
      className="video-synchronized-learning learning-flow"
      aria-label="随视频同步的学习材料"
    >
      <p role="status" className="transcript-muted">
        {error ||
          (currentRange
            ? `当前段已准备至 ${clock(currentRange[1] / 1000)}；领先播放 ${Math.floor(currentRange[1] / 1000 - playbackTime)} 秒`
            : refreshing || visible?.status === "processing"
              ? "当前段还在准备，已完成内容会保留在下方。"
              : "当前时间尚无已准备内容。")}
      </p>
      {error && (
        <button type="button" onClick={refresh}>
          重新检查
        </button>
      )}
      {visible?.stage && <p>当前阶段：{visible.stage}</p>}
      {(visible?.errorCode || visible?.errorMessage) && (
        <p role="alert">
          {[visible.errorCode, visible.errorMessage].filter(Boolean).join("：")}
        </p>
      )}
      {visible?.updatedAt && (
        <p>最近更新：{new Date(visible.updatedAt).toLocaleString()}</p>
      )}
      {(visible?.completedSegments != null ||
        visible?.failedSegments != null) && (
        <p>
          已完成 {visible.completedSegments ?? 0} 段 · 失败{" "}
          {visible.failedSegments ?? 0} 段
        </p>
      )}
      {visible?.status === "ready" && (
        <p>
          {visible.isProcessing
            ? "初稿可读，校对仍在进行"
            : visible.processingDone === true &&
                (visible.stage === "needs_review" ||
                  visible.errorCode === "content_uncertain")
              ? "校对已结束，部分内容需核对"
              : (visible.failedSegments ?? 0) > 0
                ? "初稿可读，部分校对未通过；任务已结束"
                : visible.completedSegments != null ||
                    visible.failedSegments != null
                  ? "当前材料处理已结束"
                  : "初稿可读，最终校对状态尚未确认"}
        </p>
      )}
      {visible?.retryable && onRetry && (
        <button type="button" onClick={() => void onRetry()}>
          {visible.errorCode === "content_uncertain" ||
          visible.stage === "needs_review"
            ? "重新校对"
            : "继续失败阶段"}
        </button>
      )}
      {scheduledRetryTime && (
        <p role="status">将自动继续失败阶段（预计 {scheduledRetryTime}）</p>
      )}
      {visible?.message && (
        <p className="transcript-muted">{visible.message}</p>
      )}
      {segments.length === 0 && visible?.status === "empty" && (
        <p>这节录像暂没有可同步的学习材料。</p>
      )}
      {segments.length > 0 && !active && (
        <p className="transcript-muted">
          当前时间尚无讲义段落，已完成的内容会保留在下方。
        </p>
      )}
      <div style={{ maxHeight: 360, overflowY: "auto" }}>
        {segments.map((segment) => (
          <section
            key={segment.id}
            ref={segment.id === active?.id ? activeRef : undefined}
            aria-current={segment.id === active?.id ? "true" : undefined}
            style={{
              padding: "12px 16px",
              borderRadius: 10,
              background:
                segment.id === active?.id ? "var(--surface-hover)" : undefined,
            }}
          >
            <button
              type="button"
              className="aura-button-link"
              aria-label={`跳转到 ${clock(segment.startMs / 1000)} ${segment.title}`}
              onClick={() => onSeek(segment.startMs / 1000)}
            >
              {clock(segment.startMs / 1000)}–{clock(segment.endMs / 1000)} ·{" "}
              {segment.title}
            </button>
            {segment.provisional && (
              <small className="transcript-muted">
                {segment.kind === "transcript"
                  ? "原字幕摘录"
                  : "基于原始字幕，深度校对尚未完成或未通过"}
              </small>
            )}
            <div className="ai-markdown transcript-markdown">
              <TranscriptTimedContent
                content={segment.content}
                onSeek={onSeek}
              />
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}
