import { Captions } from "lucide-react";
import { type RefObject, useEffect, useState } from "react";

type Cue = { startMs: number; endMs: number; text: string };
type NativeVideo = HTMLVideoElement & { webkitDisplayingFullscreen?: boolean };

export function syncCaptionTrack(video: HTMLVideoElement, enabled = true) {
  const native =
    Boolean((video as NativeVideo).webkitDisplayingFullscreen) ||
    document.fullscreenElement === video;
  for (const track of Array.from(video.textTracks ?? [])) {
    if (track.kind === "subtitles" || track.kind === "captions")
      track.mode = enabled && native ? "showing" : "hidden";
  }
}

export function captionAt(cues: Cue[], seconds: number) {
  const milliseconds = seconds * 1000;
  return cues
    .filter((cue) => cue.startMs <= milliseconds && milliseconds < cue.endMs)
    .map((cue) =>
      cue.text
        .replace(/&lt;/g, "<")
        .replace(/&gt;/g, ">")
        .replace(/&quot;/g, '"')
        .replace(/&#39;/g, "'")
        .replace(/&amp;/g, "&"),
    )
    .join("\n");
}

export function VideoCaptions({
  videoRef,
  cues,
}: {
  videoRef: RefObject<HTMLVideoElement | null>;
  cues: Cue[];
}) {
  const [text, setText] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [native, setNative] = useState(false);
  useEffect(() => {
    const video = videoRef.current as NativeVideo | null;
    if (!video) return;
    let frame = 0;
    const update = () => setText(captionAt(cues, video.currentTime));
    const tick = () => {
      update();
      if (!video.paused && !video.ended) frame = requestAnimationFrame(tick);
    };
    const play = () => {
      cancelAnimationFrame(frame);
      tick();
    };
    const pause = () => {
      cancelAnimationFrame(frame);
      update();
    };
    const fullscreen = () => {
      setNative(
        Boolean(video.webkitDisplayingFullscreen) ||
          document.fullscreenElement === video,
      );
      syncCaptionTrack(video, enabled);
      update();
    };
    for (const name of ["timeupdate", "seeked", "loadedmetadata", "ratechange"])
      video.addEventListener(name, update);
    video.addEventListener("play", play);
    video.addEventListener("pause", pause);
    video.addEventListener("ended", pause);
    video.addEventListener("webkitbeginfullscreen", fullscreen);
    video.addEventListener("webkitendfullscreen", fullscreen);
    document.addEventListener("fullscreenchange", fullscreen);
    video.textTracks?.addEventListener?.("addtrack", fullscreen);
    fullscreen();
    if (!video.paused) play();
    return () => {
      cancelAnimationFrame(frame);
      for (const name of [
        "timeupdate",
        "seeked",
        "loadedmetadata",
        "ratechange",
      ])
        video.removeEventListener(name, update);
      video.removeEventListener("play", play);
      video.removeEventListener("pause", pause);
      video.removeEventListener("ended", pause);
      video.removeEventListener("webkitbeginfullscreen", fullscreen);
      video.removeEventListener("webkitendfullscreen", fullscreen);
      document.removeEventListener("fullscreenchange", fullscreen);
      video.textTracks?.removeEventListener?.("addtrack", fullscreen);
    };
  }, [videoRef, cues, enabled]);
  if (native || !cues.length) return null;
  return (
    <>
      <button
        type="button"
        className="video-caption-toggle"
        aria-label={enabled ? "隐藏字幕" : "显示字幕"}
        aria-pressed={enabled}
        title={enabled ? "隐藏字幕" : "显示字幕"}
        onClick={() => setEnabled((value) => !value)}
      >
        <Captions size={18} aria-hidden="true" />
      </button>
      {enabled && text && (
        <div
          className="video-caption-overlay"
          data-testid="video-caption-overlay"
          aria-hidden="true"
        >
          <span>{text}</span>
        </div>
      )}
    </>
  );
}
