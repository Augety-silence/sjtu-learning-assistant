// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { TranscriptTimedContent } from "@/components/TranscriptPhase1Panels";
import { type CourseVideoItem, VideosView } from "@/components/VideosView";
import type { TranscriptJob } from "@/lib/types";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@/test/render";

vi.mock("@/lib/api", () => ({
  getTranscriptArtifacts: vi.fn(async () => ({
    items: [
      {
        id: "lesson_summary",
        kind: "lesson_summary",
        label: "讲义",
        content_type: "text/markdown",
      },
    ],
  })),
  getTranscriptV2Artifacts: vi.fn(async () => ({
    available: false,
    items: [],
  })),
  readTranscriptArtifact: vi.fn(async () => ({ content: "可阅读的原有讲义" })),
  readTranscriptV2Artifact: vi.fn(),
}));
const lesson: CourseVideoItem = {
  id: "a",
  title: "甲课",
  courseName: "课程",
  source: "video_space",
  supportsSubtitle: true,
  playable: true,
  supportsSlidesPdf: true,
};
const other = { ...lesson, id: "b", title: "乙课" };
const completed: TranscriptJob = {
  id: "job",
  batch_id: "batch",
  source_id: "b",
  title: "乙课",
  status: "completed",
  stage: "completed",
  progress: 100,
  attempts: 1,
};
function menu(title: string, action: string) {
  fireEvent.click(screen.getByRole("button", { name: `${title} 更多操作` }));
  fireEvent.click(screen.getByRole("menuitem", { name: action }));
}
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("video action capabilities and isolation", () => {
  it("links time ranges only in ordinary Markdown text", () => {
    const seek = vi.fn();
    render(
      <TranscriptTimedContent
        content={
          "普通 0:05-0:10，代码 `0:15-0:20`，[已有 0:25-0:30](https://example.test)"
        }
        onSeek={seek}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "跳转到 0:05–0:10" }));
    expect(seek).toHaveBeenCalledWith(5);
    expect(screen.getByText("0:15-0:20").tagName).toBe("CODE");
    expect(screen.getByRole("link", { name: "已有 0:25-0:30" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /0:15|0:25/ })).toBeNull();
  });

  it("does not start automatic AI if obtaining the playback address fails", async () => {
    const prepare = vi.fn(async () => undefined);
    render(
      <VideosView
        videos={[lesson]}
        onPrepareVideo={prepare}
        onPlay={async () => {
          throw new Error("录像地址暂不可用");
        }}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "播放 甲课" }));
    expect(await screen.findByText("甲课 无法载入")).toBeTruthy();
    expect(prepare).not.toHaveBeenCalled();
  });

  it("loads existing learning materials without starting AI or playback", async () => {
    const prepare = vi.fn(async () => undefined);
    const play = vi.fn(async () => ({ url: "https://example.test/a.mp4" }));
    const load = vi.fn(async () => ({
      status: "ready" as const,
      segments: [],
    }));
    render(
      <VideosView
        videos={[lesson]}
        onPrepareVideo={prepare}
        onPlay={play}
        onLoadLearningSegments={load}
        transcriptJobs={[
          { ...completed, source_id: lesson.id, phase1_status: "completed" },
        ]}
      />,
    );
    menu(lesson.title, "查看学习材料");
    await waitFor(() =>
      expect(load).toHaveBeenCalledWith(lesson, expect.any(AbortSignal)),
    );
    expect(prepare).not.toHaveBeenCalled();
    expect(play).not.toHaveBeenCalled();
  });

  it("offers recovery when automatic preparation fails", async () => {
    const prepare = vi
      .fn()
      .mockRejectedValueOnce(new Error("服务暂不可用"))
      .mockResolvedValueOnce(undefined);
    render(
      <VideosView
        videos={[lesson]}
        onPrepareVideo={prepare}
        onPlay={async () => ({ url: "https://example.test/a.mp4" })}
        onLoadLearningSegments={async () => ({ status: "empty", segments: [] })}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "播放 甲课" }));
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "重试同步" }).getAttribute("title"),
      ).toBe("服务暂不可用"),
    );
    fireEvent.click(screen.getByRole("button", { name: "重试同步" }));
    await waitFor(() => expect(prepare).toHaveBeenCalledTimes(2));
  });

  it("prepares a selected video automatically and follows playback with inline learning and seekable evidence", async () => {
    const prepare = vi.fn(async () => undefined);
    const update = vi.fn(async () => undefined);
    const segments = vi.fn(async () => ({
      status: "ready" as const,
      segments: [
        {
          id: "first",
          startMs: 0,
          endMs: 20000,
          title: "第一知识点",
          content: "当前知识解释。字幕证据：cue-1 · 00:05–00:10",
        },
        {
          id: "second",
          startMs: 20000,
          endMs: 40000,
          title: "第二知识点",
          content: "后续知识解释。",
        },
      ],
    }));
    render(
      <VideosView
        videos={[lesson]}
        onPrepareVideo={prepare}
        onUpdatePlayback={update}
        onLoadLearningSegments={segments}
        onPlay={async () => ({ url: "https://example.test/a.mp4" })}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "播放 甲课" }));
    await waitFor(() =>
      expect(prepare).toHaveBeenCalledWith(lesson, {
        positionSeconds: 0,
        playbackRate: 1,
      }),
    );
    expect(await screen.findByText(/当前知识解释/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "生成 AI 总结" })).toBeNull();
    const player = document.querySelector("video") as HTMLVideoElement;
    player.currentTime = 22;
    fireEvent.timeUpdate(player);
    expect(
      document.querySelector(
        ".video-synchronized-learning [aria-current='true']",
      )?.textContent,
    ).toContain("第二知识点");
    player.currentTime = 23;
    fireEvent.timeUpdate(player);
    expect(update).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole("button", { name: "跳转到 00:05–00:10" }));
    expect(player.currentTime).toBe(5);
    expect(update).toHaveBeenLastCalledWith(lesson, {
      positionSeconds: 5,
      playbackRate: 1,
    });
    expect(prepare).toHaveBeenCalledOnce();
    player.playbackRate = 2;
    fireEvent.rateChange(player);
    expect(update).toHaveBeenLastCalledWith(lesson, {
      positionSeconds: 5,
      playbackRate: 2,
    });
  });

  it("refreshes processing subtitles in the background without another click", async () => {
    vi.useFakeTimers();
    const subtitles = vi
      .fn()
      .mockResolvedValueOnce({
        subtitleStatus: "processing",
        subtitleMessage: "字幕尚在生成",
      })
      .mockResolvedValueOnce({
        subtitleStatus: "ready",
        subtitleMessage: "后台字幕已就绪",
      });
    const view = render(
      <VideosView
        videos={[lesson]}
        onPlay={async () => ({ url: "https://example.test/a.mp4" })}
        onLoadSubtitles={(video) => subtitles(video)}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "播放 甲课" }));
    await act(async () => undefined);
    expect(subtitles).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });
    view.rerender(
      <VideosView
        videos={[lesson]}
        onPlay={async () => ({ url: "https://example.test/a.mp4" })}
        onLoadSubtitles={(video) => subtitles(video)}
      />,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });
    expect(subtitles).toHaveBeenCalledTimes(2);
    fireEvent.mouseDown(screen.getByRole("tab", { name: "字幕" }), {
      button: 0,
      ctrlKey: false,
    });
    expect(screen.queryByText("后台字幕已就绪")).toBeNull();
  });

  it("reloads the player subtitle after AI review completes", async () => {
    const subtitles = vi
      .fn()
      .mockResolvedValueOnce({
        subtitleStatus: "ready",
        subtitleMessage: "原始字幕",
      })
      .mockResolvedValueOnce({
        subtitleStatus: "ready",
        subtitleMessage: "AI校对字幕已载入",
      });
    const props = {
      videos: [lesson],
      onPlay: async () => ({ url: "https://example.test/a.mp4" }),
      onLoadSubtitles: subtitles,
    };
    const view = render(<VideosView {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "播放 甲课" }));
    await waitFor(() => expect(subtitles).toHaveBeenCalledOnce());
    view.rerender(
      <VideosView
        {...props}
        transcriptJobs={[
          { ...completed, source_id: lesson.id, phase1_status: "completed" },
        ]}
      />,
    );
    await waitFor(() => expect(subtitles).toHaveBeenCalledTimes(2));
    fireEvent.mouseDown(screen.getByRole("tab", { name: "字幕" }), {
      button: 0,
      ctrlKey: false,
    });
    expect(screen.queryByText("AI校对字幕已载入")).toBeNull();
  });

  it("rechecks processing slides and preloads the next page once", async () => {
    vi.useFakeTimers();
    const metadata = vi
      .fn()
      .mockResolvedValueOnce({
        status: "processing",
        message: "课件正在生成",
        pages: [],
      })
      .mockResolvedValueOnce({
        status: "ready",
        message: "",
        pages: [
          { index: 0, timeMs: 0 },
          { index: 1, timeMs: 30000 },
        ],
      });
    const images = vi.fn(
      async (_video, index) => `data:image/png;base64,page${index}`,
    );
    render(
      <VideosView
        videos={[lesson]}
        onPlay={async () => ({ url: "https://example.test/a.mp4" })}
        onLoadSlides={metadata}
        onLoadSlideImage={images}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "播放 甲课" }));
    await act(async () => undefined);
    fireEvent.click(screen.getByRole("button", { name: "打开 PPT 分屏" }));
    await act(async () => undefined);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(metadata).toHaveBeenCalledTimes(2);
    expect(images.mock.calls.map((call) => call[1])).toEqual([0, 1]);
    const player = document.querySelector("video") as HTMLVideoElement;
    player.currentTime = 30;
    fireEvent.seeked(player);
    await act(async () => undefined);
    expect(images).toHaveBeenCalledTimes(2);
    expect(screen.getByAltText("甲课，课件第 2 页")).toBeTruthy();
  });

  it("loads subtitles for a newly selected video without losing the request to selection changes", async () => {
    const url = URL.createObjectURL;
    const revoke = URL.revokeObjectURL;
    URL.createObjectURL = vi.fn(() => "blob:subtitle-new");
    URL.revokeObjectURL = vi.fn();
    const load = vi.fn(async () => ({
      subtitleVtt: "WEBVTT\n\n00:00.000 --> 00:01.000\n字幕",
      subtitleMessage: "字幕已准备",
    }));
    render(<VideosView videos={[lesson]} onLoadSubtitles={load} />);
    menu("甲课", "载入播放器字幕");
    await waitFor(() => expect(URL.createObjectURL).toHaveBeenCalledOnce());
    expect(screen.queryByText("字幕已准备")).toBeNull();
    cleanup();
    URL.createObjectURL = url;
    URL.revokeObjectURL = revoke;
  });

  it("shows external-open success, skips remote subtitles and releases playback busy", async () => {
    const subtitles = vi.fn(async () => ({ subtitleMessage: "远程字幕" }));
    const playback = vi.fn(async () => ({ kind: "external_opened" as const }));
    render(
      <VideosView
        videos={[lesson]}
        onPlay={playback}
        onLoadSubtitles={subtitles}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "播放 甲课" }));
    expect(
      await screen.findByText("已在本地播放器或外部应用打开"),
    ).toBeTruthy();
    expect(
      screen.getByText("可在外部应用观看录像，或继续使用下方学习工具。"),
    ).toBeTruthy();
    expect(screen.queryByText("该节录像未返回可播放地址。")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(subtitles).not.toHaveBeenCalled();
    const play = screen.getByRole("button", {
      name: "播放 甲课",
    }) as HTMLButtonElement;
    expect(play.disabled).toBe(false);
    expect(play.getAttribute("aria-busy")).toBeNull();
    fireEvent.click(play);
    await waitFor(() => expect(playback).toHaveBeenCalledTimes(2));
  });
  it("bounds a stuck playback read, allows a retry and ignores its late old address", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(0);
    let finishOld: ((value: { url: string }) => void) | undefined;
    const playback = vi
      .fn()
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finishOld = resolve;
          }),
      )
      .mockResolvedValueOnce({ url: "https://example.test/recovered.mp4" });
    render(<VideosView videos={[lesson]} onPlay={playback} />);
    fireEvent.click(screen.getByRole("button", { name: "播放 甲课" }));
    await act(async () => vi.advanceTimersByTimeAsync(30_000));
    expect(
      document.querySelector('.video-player-empty[role="alert"]')?.textContent,
    ).toContain("播放地址读取超时，可重新选择播放");
    const retry = screen.getByRole("button", {
      name: "播放 甲课",
    }) as HTMLButtonElement;
    expect(retry.disabled).toBe(false);
    fireEvent.click(retry);
    await act(async () => vi.advanceTimersByTimeAsync(0));
    expect(playback).toHaveBeenCalledTimes(2);
    expect(document.querySelector("video source")?.getAttribute("src")).toBe(
      "https://example.test/recovered.mp4",
    );
    await act(async () => {
      finishOld?.({ url: "https://example.test/stale.mp4" });
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(document.querySelector("video source")?.getAttribute("src")).toBe(
      "https://example.test/recovered.mp4",
    );
  });

  it("bounds a stuck manual subtitle read, shows recovery and ignores late old subtitles", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(0);
    let finishOld: ((value: { subtitleMessage: string }) => void) | undefined;
    const subtitles = vi
      .fn()
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finishOld = resolve;
          }),
      )
      .mockResolvedValueOnce({ subtitleMessage: "新字幕已准备" });
    render(<VideosView videos={[lesson]} onLoadSubtitles={subtitles} />);
    menu(lesson.title, "载入播放器字幕");
    await act(async () => vi.advanceTimersByTimeAsync(30_000));
    expect(
      screen.getAllByText("字幕读取超时，可从录像更多操作重新载入字幕。")
        .length,
    ).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: "甲课 更多操作" }));
    expect(
      (
        screen.getByRole("menuitem", {
          name: "载入播放器字幕",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(false);
    fireEvent.click(screen.getByRole("menuitem", { name: "载入播放器字幕" }));
    await act(async () => vi.advanceTimersByTimeAsync(0));
    expect(screen.queryByText("新字幕已准备")).toBeNull();
    await act(async () => {
      finishOld?.({ subtitleMessage: "旧字幕迟到" });
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.queryByText("旧字幕迟到")).toBeNull();
    expect(screen.queryByText("新字幕已准备")).toBeNull();
    expect(subtitles).toHaveBeenCalledTimes(2);
  });

  it("keeps playback free while background subtitles time out and lets manual subtitles recover", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(0);
    const subtitles = vi
      .fn()
      .mockImplementationOnce(() => new Promise(() => undefined))
      .mockResolvedValueOnce({ subtitleMessage: "重新载入成功" });
    render(
      <VideosView
        videos={[lesson]}
        onPlay={async () => ({ url: "https://example.test/lesson.mp4" })}
        onLoadSubtitles={subtitles}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "播放 甲课" }));
    await act(async () => vi.advanceTimersByTimeAsync(0));
    expect(
      (screen.getByRole("button", { name: "播放 甲课" }) as HTMLButtonElement)
        .disabled,
    ).toBe(false);
    await act(async () => vi.advanceTimersByTimeAsync(30_000));
    fireEvent.mouseDown(screen.getByRole("tab", { name: "字幕" }), {
      button: 0,
      ctrlKey: false,
    });
    expect(
      screen.queryByText("字幕读取超时，可从录像更多操作重新载入字幕。"),
    ).toBeNull();
    expect(document.querySelector("video source")?.getAttribute("src")).toBe(
      "https://example.test/lesson.mp4",
    );
    menu(lesson.title, "载入播放器字幕");
    await act(async () => vi.advanceTimersByTimeAsync(0));
    expect(screen.queryByText("重新载入成功")).toBeNull();
    expect(subtitles).toHaveBeenCalledTimes(2);
  });

  it("aborts only stale UI waits on course change and keeps late playback and subtitle results out", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(0);
    let finishPlay: ((value: { url: string }) => void) | undefined;
    let finishSubtitle:
      | ((value: { subtitleMessage: string }) => void)
      | undefined;
    const playback = vi
      .fn()
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finishPlay = resolve;
          }),
      )
      .mockResolvedValueOnce({ url: "https://example.test/new-course.mp4" });
    const subtitles = vi
      .fn()
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finishSubtitle = resolve;
          }),
      )
      .mockResolvedValueOnce({ subtitleMessage: "新课程字幕" });
    const { rerender } = render(
      <VideosView
        courseId={12}
        videos={[lesson]}
        onPlay={playback}
        onLoadSubtitles={subtitles}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "播放 甲课" }));
    await act(async () => vi.advanceTimersByTimeAsync(0));
    menu(lesson.title, "载入播放器字幕");
    await act(async () => vi.advanceTimersByTimeAsync(0));
    rerender(
      <VideosView
        courseId={13}
        videos={[other]}
        onPlay={playback}
        onLoadSubtitles={subtitles}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "播放 乙课" }));
    await act(async () => vi.advanceTimersByTimeAsync(0));
    await act(async () => {
      finishPlay?.({ url: "https://example.test/old-course.mp4" });
      finishSubtitle?.({ subtitleMessage: "旧课程字幕" });
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(document.querySelector("video source")?.getAttribute("src")).toBe(
      "https://example.test/new-course.mp4",
    );
    expect(screen.queryByText("旧课程字幕")).toBeNull();
    expect(screen.queryByText("读取已取消。")).toBeNull();
    expect(
      (screen.getByRole("button", { name: "播放 乙课" }) as HTMLButtonElement)
        .disabled,
    ).toBe(false);
  });
});
