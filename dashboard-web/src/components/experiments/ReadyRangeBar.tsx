import { Check, Clock3, X } from "lucide-react";
import { motion as m } from "motion/react";
import {
  type KeyboardEvent,
  type PointerEvent,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";
import { Button } from "@/components/ui/Button";
import { MetaTag } from "@/components/ui/CompactControls";
import { easeOutTransition, motionDuration } from "@/lib/motion";
import { cn } from "@/lib/utils";
import {
  formatClockMs,
  isFiniteNonNegative,
  useWidgetMotion,
  type WidgetMotionMode,
} from "./widgetMotion";
import "./ready-range-bar.css";

export type RangeMs = readonly [number, number];

export interface ReadyRangeBarProps {
  /** Total media duration in ms. `null`/invalid/<=0 means unknown. */
  durationMs?: number | null;
  /** Current playback position in ms. `null`/invalid means unknown. */
  currentTimeMs?: number | null;
  /** Time ranges `[startMs, endMs]` that already have learning material. */
  availableRangesMs?: ReadonlyArray<RangeMs> | null;
  /** Called only after the user explicitly confirms a jump. */
  onSeekMs?: (ms: number) => void;
  /** Keyboard step for arrow keys (Shift = ×6). */
  stepMs?: number;
  motion?: WidgetMotionMode;
  label?: string;
  className?: string;
}

/**
 * Clean ranges: drop non-finite / inverted / empty ones, clamp into
 * `[0, durationMs]` when the duration is known, sort and merge overlapping or
 * touching ranges. Pure, exported for tests and hosts.
 */
export function normalizeRangesMs(
  ranges: ReadonlyArray<RangeMs> | null | undefined,
  durationMs?: number | null,
): [number, number][] {
  if (!Array.isArray(ranges)) return [];
  const limit =
    isFiniteNonNegative(durationMs) && durationMs > 0 ? durationMs : null;
  const cleaned: [number, number][] = [];
  for (const range of ranges) {
    if (!Array.isArray(range) || range.length < 2) continue;
    const [rawStart, rawEnd] = range;
    if (!Number.isFinite(rawStart) || !Number.isFinite(rawEnd)) continue;
    let start = Math.max(0, rawStart);
    let end = rawEnd;
    if (limit !== null) {
      start = Math.min(start, limit);
      end = Math.min(end, limit);
    }
    if (end <= start) continue;
    cleaned.push([start, end]);
  }
  cleaned.sort((left, right) => left[0] - right[0] || left[1] - right[1]);
  const merged: [number, number][] = [];
  for (const [start, end] of cleaned) {
    const last = merged.at(-1);
    if (last && start <= last[1]) last[1] = Math.max(last[1], end);
    else merged.push([start, end]);
  }
  return merged;
}

function containingRange(ranges: [number, number][], ms: number) {
  return ranges.find(([start, end]) => start <= ms && ms < end);
}

const clamp = (value: number, min: number, max: number) =>
  Math.min(max, Math.max(min, value));

export function ReadyRangeBar({
  durationMs,
  currentTimeMs,
  availableRangesMs,
  onSeekMs,
  stepMs = 5000,
  motion = "auto",
  label = "学习材料可用时间带",
  className,
}: ReadyRangeBarProps) {
  const animate = useWidgetMotion(motion);
  const id = useId();
  const hasDuration = isFiniteNonNegative(durationMs) && durationMs > 0;
  const duration = hasDuration ? durationMs : 0;
  const hasCurrent = isFiniteNonNegative(currentTimeMs);
  const current = hasCurrent
    ? hasDuration
      ? clamp(currentTimeMs, 0, duration)
      : currentTimeMs
    : null;
  const ranges = useMemo(
    () => normalizeRangesMs(availableRangesMs, durationMs),
    [availableRangesMs, durationMs],
  );
  const interactive = Boolean(onSeekMs);
  const [pendingMs, setPendingMs] = useState<number | null>(null);
  // Keep a pending candidate valid if the duration shrinks; never seek here.
  const pending =
    pendingMs === null
      ? null
      : hasDuration
        ? clamp(pendingMs, 0, duration)
        : pendingMs;

  // Segments present on first paint render statically; only ranges that
  // appear later animate in (incremental reveal).
  const mountedRef = useRef(false);
  useEffect(() => {
    mountedRef.current = true;
  }, []);

  const trackRef = useRef<HTMLDivElement>(null);
  const percent = (ms: number) => (hasDuration ? (ms / duration) * 100 : 0);

  const pickFromPointer = (event: PointerEvent<HTMLDivElement>) => {
    if (!interactive || !hasDuration) return;
    const rect = event.currentTarget.getBoundingClientRect();
    if (rect.width <= 0) return;
    const ratio = clamp((event.clientX - rect.left) / rect.width, 0, 1);
    setPendingMs(Math.round(ratio * duration));
  };

  const confirm = () => {
    if (pending === null || !onSeekMs) return;
    onSeekMs(pending);
    setPendingMs(null);
  };

  const onTrackKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!interactive || !hasDuration) return;
    const base = pending ?? current ?? 0;
    const step = event.shiftKey ? stepMs * 6 : stepMs;
    let next: number | null = null;
    switch (event.key) {
      case "ArrowRight":
      case "ArrowUp":
        next = base + step;
        break;
      case "ArrowLeft":
      case "ArrowDown":
        next = base - step;
        break;
      case "PageUp":
        next = base + stepMs * 6;
        break;
      case "PageDown":
        next = base - stepMs * 6;
        break;
      case "Home":
        next = 0;
        break;
      case "End":
        next = duration;
        break;
      case "Enter":
        if (pending !== null) {
          event.preventDefault();
          confirm();
        }
        return;
      case "Escape":
        if (pending !== null) {
          event.preventDefault();
          setPendingMs(null);
        }
        return;
      default:
        return;
    }
    event.preventDefault();
    setPendingMs(clamp(next, 0, duration));
  };

  const currentCovered = current !== null && containingRange(ranges, current);
  const pendingCovered = pending !== null && containingRange(ranges, pending);
  const positionText =
    current === null
      ? "播放位置未知"
      : `当前 ${formatClockMs(current)}${hasDuration ? ` / ${formatClockMs(duration)}` : ""}`;
  const coverageText =
    ranges.length === 0
      ? "暂无已整理的学习材料区间"
      : `已有材料覆盖 ${ranges.length} 段`;
  const currentText =
    current === null || ranges.length === 0
      ? null
      : currentCovered
        ? "当前位置有学习材料"
        : "当前位置暂无学习材料";
  const valueMs = pending ?? current ?? 0;
  const valueText =
    pending !== null
      ? `候选跳转位置 ${formatClockMs(pending)}，${pendingCovered ? "有学习材料" : "暂无学习材料"}，按 Enter 确认，Escape 取消`
      : current !== null
        ? `${positionText}，${currentText ?? coverageText}`
        : positionText;

  const transition = easeOutTransition(motionDuration.enter);

  return (
    <div
      className={cn("slw-range", className)}
      data-motion={animate ? "light" : "static"}
      data-duration={hasDuration ? "known" : "unknown"}
    >
      <div className="slw-range-head">
        <span className="slw-range-title" id={`${id}-label`}>
          {label}
        </span>
        <span className="slw-range-position">
          <Clock3 aria-hidden="true" />
          {positionText}
        </span>
      </div>

      {hasDuration ? (
        <div
          ref={trackRef}
          className="slw-range-track"
          data-interactive={interactive || undefined}
          role={interactive ? "slider" : "img"}
          tabIndex={interactive ? 0 : undefined}
          aria-labelledby={`${id}-label`}
          aria-describedby={`${id}-note`}
          aria-valuemin={interactive ? 0 : undefined}
          aria-valuemax={interactive ? duration : undefined}
          aria-valuenow={interactive ? Math.round(valueMs) : undefined}
          aria-valuetext={interactive ? valueText : undefined}
          aria-label={interactive ? undefined : `${label}：${valueText}`}
          onPointerDown={pickFromPointer}
          onKeyDown={onTrackKeyDown}
        >
          <div className="slw-range-rail" aria-hidden="true" />
          {ranges.map(([start, end]) => {
            const style = {
              left: `${percent(start)}%`,
              width: `${percent(end) - percent(start)}%`,
            };
            return animate ? (
              <m.div
                key={start}
                className="slw-range-segment"
                aria-hidden="true"
                data-range={`${start}-${end}`}
                initial={
                  mountedRef.current ? { opacity: 0, scaleX: 0.6 } : false
                }
                animate={{ opacity: 1, scaleX: 1, ...style }}
                transition={transition}
                style={{ originX: 0 }}
              />
            ) : (
              <div
                key={start}
                className="slw-range-segment"
                aria-hidden="true"
                data-range={`${start}-${end}`}
                style={style}
              />
            );
          })}
          {current !== null &&
            (animate ? (
              <m.div
                className="slw-range-playhead"
                aria-hidden="true"
                initial={false}
                animate={{ left: `${percent(current)}%` }}
                transition={easeOutTransition(motionDuration.control)}
              />
            ) : (
              <div
                className="slw-range-playhead"
                aria-hidden="true"
                style={{ left: `${percent(current)}%` }}
              />
            ))}
          {pending !== null && (
            <div
              className="slw-range-pending"
              aria-hidden="true"
              style={{ left: `${percent(pending)}%` }}
            />
          )}
        </div>
      ) : (
        <p className="slw-range-unknown">
          视频时长未知，暂不绘制时间带；下方按时间列出已有材料区间。
        </p>
      )}

      <p className="slw-range-note" id={`${id}-note`}>
        <span>{coverageText}</span>
        {currentText && (
          <MetaTag tone={currentCovered ? "info" : "neutral"}>
            {currentText}
          </MetaTag>
        )}
        <span className="slw-range-caveat">
          覆盖只表示已有材料，不代表已校对或质量通过。
        </span>
      </p>

      {!hasDuration && ranges.length > 0 && (
        <ul className="slw-range-list" aria-label="已有材料区间">
          {ranges.map(([start, end]) => (
            <li key={start}>
              {interactive ? (
                <button
                  type="button"
                  className="slw-range-chip"
                  aria-pressed={pending === start}
                  onClick={() => setPendingMs(start)}
                >
                  {formatClockMs(start)}–{formatClockMs(end)}
                </button>
              ) : (
                <span className="slw-range-chip">
                  {formatClockMs(start)}–{formatClockMs(end)}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}

      {interactive && pending !== null && (
        <div className="slw-range-confirm" role="group" aria-label="确认跳转">
          <span>
            跳转到 <strong>{formatClockMs(pending)}</strong>
            {pendingCovered ? "（有学习材料）" : "（此处暂无学习材料）"}
          </span>
          <Button size="sm" onClick={confirm}>
            <Check aria-hidden="true" />
            确认跳转
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setPendingMs(null)}>
            <X aria-hidden="true" />
            取消
          </Button>
        </div>
      )}
    </div>
  );
}
