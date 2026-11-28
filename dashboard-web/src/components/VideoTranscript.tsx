import { useEffect, useRef } from "react";
import { captionAt } from "@/components/VideoCaptions";

type Cue = { startMs: number; endMs: number; text: string };
function timestamp(ms: number) {
  const seconds = Math.floor(ms / 1000);
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return `${hours ? `${hours}:` : ""}${String(minutes).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}
export function VideoTranscript({
  cues,
  playbackTime,
  onSeek,
}: {
  cues: Cue[];
  playbackTime: number;
  onSeek: (seconds: number) => void;
}) {
  const listRef = useRef<HTMLDivElement>(null);
  const activeIndex = cues.findIndex(
    (c) => c.startMs <= playbackTime * 1000 && playbackTime * 1000 < c.endMs,
  );
  useEffect(() => {
    const list = listRef.current;
    const active = list?.querySelector<HTMLElement>('[aria-current="true"]');
    const scroll = list?.closest<HTMLElement>(".video-tool-panel");
    if (!active || !scroll) return;
    const a = active.getBoundingClientRect(),
      s = scroll.getBoundingClientRect();
    if (a.top < s.top || a.bottom > s.bottom) scroll.scrollTop += a.top - s.top;
  }, [activeIndex]);
  return (
    <div ref={listRef} className="video-transcript" aria-label="字幕时间轴">
      {cues.map((cue, index) => (
        <button
          key={`${cue.startMs}:${index}`}
          type="button"
          className="video-transcript-segment"
          aria-current={
            cue.startMs <= playbackTime * 1000 &&
            cue.endMs > playbackTime * 1000
              ? "true"
              : undefined
          }
          aria-label={`跳转到 ${timestamp(cue.startMs)}：${captionAt([cue], cue.startMs / 1000)}`}
          onClick={() => onSeek(cue.startMs / 1000)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              e.stopPropagation();
              onSeek(cue.startMs / 1000);
            }
          }}
        >
          <time className="video-transcript-time">
            {timestamp(cue.startMs)}
          </time>
          <span>{captionAt([cue], cue.startMs / 1000)}</span>
        </button>
      ))}
    </div>
  );
}
