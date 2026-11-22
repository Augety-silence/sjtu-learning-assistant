// @vitest-environment jsdom
import { useRef } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  captionAt,
  syncCaptionTrack,
  VideoCaptions,
} from "@/components/VideoCaptions";
import { VideosView } from "@/components/VideosView";
import { cleanup, fireEvent, render, screen, waitFor } from "@/test/render";

const cues = [
  { startMs: 1000, endMs: 3000, text: "第一句 &amp; 定义" },
  { startMs: 3000, endMs: 5000, text: "第二句" },
];
function Player({ source = "one", input = cues }) {
  const ref = useRef<HTMLVideoElement>(null);
  return (
    <div>
      <video ref={ref} aria-label="测试播放" />
      <VideoCaptions key={source} videoRef={ref} cues={input} />
    </div>
  );
}
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("playback captions", () => {
  it("uses media time, half-open cue intervals, gaps and escaped plain text", () => {
    expect(captionAt(cues, 0)).toBe("");
    expect(captionAt(cues, 1)).toBe("第一句 & 定义");
    expect(captionAt(cues, 3)).toBe("第二句");
    expect(captionAt(cues, 5)).toBe("");
  });
  it("tracks seeking and rate changes, does not inherit text from another recording", () => {
    const view = render(<Player />);
    const video = screen.getByLabelText("测试播放") as HTMLVideoElement;
    video.currentTime = 2;
    fireEvent.timeUpdate(video);
    expect(screen.getByTestId("video-caption-overlay").textContent).toBe(
      "第一句 & 定义",
    );
    video.currentTime = 4;
    video.playbackRate = 2;
    fireEvent.rateChange(video);
    expect(screen.getByTestId("video-caption-overlay").textContent).toBe(
      "第二句",
    );
    video.currentTime = 0;
    fireEvent.seeked(video);
    expect(screen.queryByTestId("video-caption-overlay")).toBeNull();
    video.currentTime = 2;
    fireEvent.seeked(video);
    view.rerender(
      <Player
        source="two"
        input={[{ startMs: 0, endMs: 6000, text: "新录像" }]}
      />,
    );
    expect(screen.getByTestId("video-caption-overlay").textContent).toBe(
      "新录像",
    );
    fireEvent.click(screen.getByRole("button", { name: "隐藏字幕" }));
    expect(screen.queryByTestId("video-caption-overlay")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "显示字幕" }));
    expect(screen.getByTestId("video-caption-overlay").textContent).toBe(
      "新录像",
    );
  });
  it("enables native captions only in video fullscreen, preventing double subtitles", () => {
    const video = document.createElement("video");
    const track = { kind: "subtitles", mode: "disabled" };
    Object.defineProperty(video, "textTracks", { value: [track] });
    Object.defineProperty(video, "webkitDisplayingFullscreen", {
      value: true,
      configurable: true,
    });
    syncCaptionTrack(video);
    expect(track.mode).toBe("showing");
    syncCaptionTrack(video, false);
    expect(track.mode).toBe("hidden");
    Object.defineProperty(video, "webkitDisplayingFullscreen", {
      value: false,
    });
    syncCaptionTrack(video);
    expect(track.mode).toBe("hidden");
  });
  it("can sync all courses without selecting or opening a video", async () => {
    const sync = vi.fn(async () => {});
    render(<VideosView videos={[]} onSyncAllSubtitles={sync} />);
    fireEvent.click(screen.getByRole("button", { name: "同步全部字幕" }));
    await waitFor(() => expect(sync).toHaveBeenCalledOnce());
    expect(screen.queryByText("字幕校对")).toBeNull();
  });
});
