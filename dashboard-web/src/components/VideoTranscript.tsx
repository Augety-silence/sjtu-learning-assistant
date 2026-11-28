import { useEffect, useRef } from "react";
import {
  ContentSearch,
  HighlightedText,
  useContentSearch,
} from "@/components/ContentSearch";
import { captionAt } from "@/components/VideoCaptions";
import { findTextMatches } from "@/lib/contentSearch";

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
  sourceId,
}: {
  cues: Cue[];
  playbackTime: number;
  onSeek: (seconds: number) => void;
  sourceId?: string;
}) {
  return (
    <ContentSearch
      label="搜索字幕"
      resetKey={sourceId}
      className="video-transcript-search"
    >
      <TranscriptSegments
        cues={cues}
        playbackTime={playbackTime}
        onSeek={onSeek}
      />
    </ContentSearch>
  );
}

function TranscriptSegments({
  cues,
  playbackTime,
  onSeek,
}: Parameters<typeof VideoTranscript>[0]) {
  const query = useContentSearch();
  const listRef = useRef<HTMLDivElement>(null);
  const activeIndex = cues.findIndex(
    (c) => c.startMs <= playbackTime * 1000 && playbackTime * 1000 < c.endMs,
  );
  useEffect(() => {
    const list = listRef.current;
    const active = list?.querySelector<HTMLElement>('[aria-current="true"]');
    const scroll = list?.closest<HTMLElement>(".video-tool-panel");
    if (!active || !scroll || query) return;
    const a = active.getBoundingClientRect(),
      s = scroll.getBoundingClientRect();
    if (a.top < s.top || a.bottom > s.bottom) scroll.scrollTop += a.top - s.top;
  }, [activeIndex, query]);
  const filtered = cues
    .map((cue, index) => ({
      cue,
      index,
      text: captionAt([cue], cue.startMs / 1000),
    }))
    .filter(({ text }) => !query || findTextMatches(text, query).length > 0);
  return (
    <div ref={listRef} className="video-transcript" aria-label="字幕时间轴">
      {query && !filtered.length && (
        <p className="content-search-empty">
          没有匹配的字幕，请尝试其他关键词。
        </p>
      )}
      {filtered.map(({ cue, index, text }) => (
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
          <span>
            <HighlightedText text={text} />
          </span>
        </button>
      ))}
    </div>
  );
}
