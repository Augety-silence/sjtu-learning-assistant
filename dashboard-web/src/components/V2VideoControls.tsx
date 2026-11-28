import {
  Maximize2,
  Minimize2,
  Pause,
  Play,
  RotateCcw,
  RotateCw,
  Volume2,
  VolumeX,
} from "lucide-react";
import { type RefObject, useEffect, useState } from "react";

function clock(seconds: number) {
  const value = Math.floor(Math.max(0, seconds));
  const hours = Math.floor(value / 3600);
  return `${hours ? `${hours}:` : ""}${String(Math.floor(value / 60) % 60).padStart(2, "0")}:${String(value % 60).padStart(2, "0")}`;
}

export function V2VideoControls({
  mediaRef,
  source,
  fullscreen,
  onFullscreen,
  onSeek,
  onToggle,
  speedControl,
}: {
  mediaRef: RefObject<HTMLVideoElement | null>;
  source: string;
  fullscreen: boolean;
  onFullscreen: () => void;
  onSeek: (seconds: number) => void;
  onToggle: () => void;
  speedControl: React.ReactNode;
}) {
  const [state, setState] = useState({
    paused: true,
    time: 0,
    duration: 0,
    volume: 1,
    muted: false,
    buffered: 0,
  });
  useEffect(() => {
    const media = mediaRef.current;
    if (!media || !source) return;
    const update = () => {
      let buffered = 0;
      for (let index = 0; index < media.buffered.length; index++) {
        if (media.buffered.start(index) <= media.currentTime)
          buffered = media.buffered.end(index);
      }
      setState({
        paused: media.paused,
        time: media.currentTime,
        duration: Number.isFinite(media.duration) ? media.duration : 0,
        volume: media.volume,
        muted: media.muted,
        buffered,
      });
    };
    const events = [
      "loadedmetadata",
      "durationchange",
      "timeupdate",
      "seeked",
      "progress",
      "play",
      "pause",
      "ended",
      "volumechange",
    ];
    for (const event of events) media.addEventListener(event, update);
    update();
    return () => {
      for (const event of events) media.removeEventListener(event, update);
    };
  }, [mediaRef, source]);
  return (
    <div
      className="v2-playback-controls"
      aria-label="播放控制"
      onDoubleClick={(event) => event.stopPropagation()}
    >
      <div
        className="v2-playback-progress"
        style={
          {
            "--buffered": `${state.duration ? (state.buffered / state.duration) * 100 : 0}%`,
            "--played": `${state.duration ? (state.time / state.duration) * 100 : 0}%`,
          } as React.CSSProperties
        }
      >
        <input
          type="range"
          aria-label="视频播放进度"
          aria-valuetext={`${clock(state.time)} / ${clock(state.duration)}`}
          min={0}
          max={state.duration || 1}
          step={0.1}
          value={Math.min(state.time, state.duration || 1)}
          disabled={!state.duration}
          onChange={(event) => onSeek(Number(event.target.value))}
        />
      </div>
      <div className="v2-playback-row">
        <button
          type="button"
          aria-label={state.paused ? "播放视频" : "暂停视频"}
          onClick={onToggle}
        >
          {state.paused ? <Play /> : <Pause />}
        </button>
        <button
          type="button"
          aria-label="后退10秒"
          onClick={() => onSeek(Math.max(0, state.time - 10))}
        >
          <RotateCcw />
          <small>10</small>
        </button>
        <button
          type="button"
          aria-label="前进10秒"
          onClick={() =>
            onSeek(Math.min(state.duration || state.time + 10, state.time + 10))
          }
        >
          <RotateCw />
          <small>10</small>
        </button>
        <time>
          {clock(state.time)} <span>/ {clock(state.duration)}</span>
        </time>
        <div className="v2-playback-right">
          <button
            type="button"
            aria-label={state.muted ? "取消静音" : "静音"}
            onClick={() => {
              if (mediaRef.current) mediaRef.current.muted = !state.muted;
            }}
          >
            {state.muted || !state.volume ? <VolumeX /> : <Volume2 />}
          </button>
          <input
            className="v2-playback-volume"
            type="range"
            aria-label="音量"
            min={0}
            max={1}
            step={0.05}
            value={state.muted ? 0 : state.volume}
            onChange={(event) => {
              if (mediaRef.current) {
                mediaRef.current.volume = Number(event.target.value);
                mediaRef.current.muted = false;
              }
            }}
          />
          {speedControl}
          <button
            type="button"
            aria-label={fullscreen ? "退出全屏" : "全屏播放"}
            onClick={onFullscreen}
          >
            {fullscreen ? <Minimize2 /> : <Maximize2 />}
          </button>
        </div>
      </div>
    </div>
  );
}
