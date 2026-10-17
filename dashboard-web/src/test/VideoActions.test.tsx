// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { type CourseVideoItem, VideosView } from "@/components/VideosView";
import { TranscriptTimedContent } from "@/components/TranscriptPhase1Panels";
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
        id: "summary",
        kind: "summary",
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
        content={"普通 0:05-0:10，代码 `0:15-0:20`，[已有 0:25-0:30](https://example.test)"}
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
    expect(await screen.findByText("服务暂不可用")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "重试自动整理" }));
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

  it("does not open a completed summary over another video in the same course", async () => {
    const start = vi.fn(async () => undefined);
    const props = {
      videos: [lesson, other],
      courseId: 12,
      onStartTranscript: start,
    };
    const { rerender } = render(<VideosView {...props} />);
    menu(lesson.title, "生成 AI 总结");
    await waitFor(() => expect(start).toHaveBeenCalledOnce());
    fireEvent.click(screen.getByRole("button", { name: "播放 乙课" }));
    rerender(
      <VideosView
        {...props}
        transcriptJobs={[
          {
            ...completed,
            id: "new-a-summary",
            source_id: lesson.id,
            title: lesson.title,
            phase1_status: "completed",
          },
        ]}
      />,
    );
    await act(async () => undefined);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(
      document.querySelector(".video-now-playing strong")?.textContent,
    ).toBe("乙课");
  });

  it("allows retry after the remote subtitle wait has ended", async () => {
    const retry = vi.fn(async () => undefined);
    render(
      <VideosView
        videos={[lesson]}
        onRetryTranscript={retry}
        transcriptJobs={[
          {
            ...completed,
            source_id: lesson.id,
            title: lesson.title,
            status: "waiting_remote",
            stage: "waiting_remote",
            progress: 100,
          },
        ]}
      />,
    );
    menu(lesson.title, "重试整理学习材料");
    await waitFor(() => expect(retry).toHaveBeenCalledOnce());
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
    expect(screen.getByText("后台字幕已就绪")).toBeTruthy();
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
    expect(screen.getByText("AI校对字幕已载入")).toBeTruthy();
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

  it("keeps a partial job partial and opens existing results without starting a new request", async () => {
    const retry = vi.fn();
    render(
      <VideosView
        videos={[other]}
        transcriptJobs={[{ ...completed, status: "partial", progress: 64 }]}
        onPlay={async () => ({ url: "https://example.test/video.mp4" })}
        onRetryTranscript={retry}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "播放 乙课" }));
    const open = await screen.findByRole("button", { name: "查看已有结果" });
    expect(screen.queryByText("失败可重试")).toBeNull();
    expect(screen.getAllByText("部分完成").length).toBeGreaterThan(0);
    fireEvent.click(open);
    expect(await screen.findByText("可阅读的原有讲义")).toBeTruthy();
    expect(retry).not.toHaveBeenCalled();
  });

  it("keeps completed legacy learning material readable without labeling it failed", () => {
    render(<VideosView videos={[other]} transcriptJobs={[completed]} />);
    menu("乙课", "查看学习材料");
    expect(
      screen.getByText("学习材料已生成，可查看总结；可在详情中补充 AI 校对。"),
    ).toBeTruthy();
    expect(screen.queryByText("失败可重试")).toBeNull();
    expect(screen.getByRole("button", { name: "查看 AI 总结" })).toBeTruthy();
  });

  it("labels usable learning material with incomplete AI review as partial", () => {
    render(
      <VideosView
        videos={[other]}
        transcriptJobs={[{ ...completed, phase1_status: "partial" }]}
      />,
    );
    menu("乙课", "查看学习材料");
    expect(
      screen.getByText(
        "学习材料可阅读，AI 校对尚未通过；可在详情中补充或重试。",
      ),
    ).toBeTruthy();
    expect(screen.queryByText("失败可重试")).toBeNull();
    expect(screen.getByRole("button", { name: "查看 AI 总结" })).toBeTruthy();
  });

  it("labels a finished content-only review as finished without auto retry", () => {
    const retry = vi.fn();
    render(
      <VideosView
        videos={[other]}
        transcriptJobs={[
          {
            ...completed,
            phase1_status: "completed_with_warnings",
            processing_state: "finished_needs_review",
          } as TranscriptJob,
        ]}
        onRetryTranscript={retry}
      />,
    );
    menu("乙课", "查看学习材料");
    expect(
      screen.getByText(
        "校对已结束，部分内容需核对；学习材料可阅读，可在详情中查看待确认项。",
      ),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "查看 AI 总结" })).toBeTruthy();
    expect(retry).not.toHaveBeenCalled();
  });

  it("labels exhausted automatic recovery as needing a manual retry", () => {
    const retry = vi.fn();
    render(
      <VideosView
        videos={[other]}
        transcriptJobs={[
          {
            ...completed,
            status: "completed_with_warnings",
            auto_retry_exhausted: true,
          },
        ]}
        onRetryTranscript={retry}
      />,
    );
    menu("乙课", "查看学习材料");
    expect(
      screen.getByText(
        "自动恢复已用尽，可手动重试；已生成的学习材料仍可阅读。",
      ),
    ).toBeTruthy();
    expect(retry).not.toHaveBeenCalled();
  });

  it("does not keep playback busy while subtitles are pending and allows AI organization", async () => {
    let finishSubtitle:
      | ((value: { subtitleMessage: string }) => void)
      | undefined;
    const start = vi.fn(async () => undefined);
    render(
      <VideosView
        videos={[lesson]}
        onPlay={async () => ({ url: "https://example.test/video.mp4" })}
        onLoadSubtitles={() =>
          new Promise((resolve) => {
            finishSubtitle = resolve;
          })
        }
        onStartTranscript={start}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "播放 甲课" }));
    await waitFor(() =>
      expect(
        (screen.getByRole("button", { name: "播放 甲课" }) as HTMLButtonElement)
          .disabled,
      ).toBe(false),
    );
    menu("甲课", "整理学习材料");
    await waitFor(() => expect(start).toHaveBeenCalledWith([lesson]));
    await act(async () => finishSubtitle?.({ subtitleMessage: "字幕已载入" }));
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
    expect(await screen.findByText("字幕已准备")).toBeTruthy();
    expect(URL.createObjectURL).toHaveBeenCalledOnce();
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

  it("deduplicates the same learning resource while allowing another resource", async () => {
    let finish: (() => void) | undefined;
    const start = vi.fn((items: CourseVideoItem[]) =>
      items[0].id === "a"
        ? new Promise<void>((resolve) => {
            finish = resolve;
          })
        : Promise.resolve(),
    );
    render(<VideosView videos={[lesson, other]} onStartTranscript={start} />);
    menu("甲课", "整理学习材料");
    fireEvent.click(screen.getByRole("button", { name: "甲课 更多操作" }));
    expect(
      (screen.getByRole("menuitem", { name: "正在整理…" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    fireEvent.click(screen.getByRole("menuitem", { name: "生成 AI 总结" }));
    expect(start).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" });
    menu("乙课", "整理学习材料");
    expect(start).toHaveBeenCalledTimes(2);
    await act(async () => finish?.());
  });

  it("keeps reading existing materials available during an export without fetching playback", async () => {
    let finish: (() => void) | undefined;
    const playback = vi.fn();
    render(
      <VideosView
        videos={[lesson, other]}
        transcriptJobs={[completed]}
        onPlay={playback}
        onCreateSlidesPdf={() =>
          new Promise<void>((resolve) => {
            finish = resolve;
          })
        }
      />,
    );
    menu("甲课", "生成课件 PDF");
    menu("乙课", "查看学习材料");
    expect(playback).not.toHaveBeenCalled();
    expect(screen.getByText("已选择当前录像")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "查看 AI 总结" }));
    expect(await screen.findByText("可阅读的原有讲义")).toBeTruthy();
    await act(async () => finish?.());
  });

  it("excludes unsupported sources and missing subtitles consistently from bulk submission", async () => {
    const canvas = {
      ...lesson,
      id: "canvas",
      title: "Canvas课",
      source: "canvas" as const,
    };
    const noSubtitle = {
      ...lesson,
      id: "empty",
      title: "无字幕课",
      supportsSubtitle: false,
    };
    const start = vi.fn(async () => undefined);
    render(
      <VideosView
        videos={[lesson, canvas, noSubtitle]}
        onStartTranscript={start}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Canvas课 更多操作" }));
    expect(
      (
        screen.getByRole("menuitem", {
          name: "整理学习材料",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
    expect(screen.getByText(/此来源尚未接入字幕整理/)).toBeTruthy();
    fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" });
    fireEvent.click(screen.getByRole("button", { name: "整理未完成 1 节" }));
    await waitFor(() => expect(start).toHaveBeenCalledWith([lesson]));
  });

  it("blocks new AI tasks with an actionable reason and preserves reading without a status banner", async () => {
    const start = vi.fn();
    const settings = vi.fn();
    render(
      <VideosView
        videos={[lesson, other]}
        transcriptJobs={[completed]}
        onStartTranscript={start}
        aiReadiness={{ configured: false, message: "尚未配置模型", model: "" }}
        onOpenAISettings={settings}
      />,
    );
    menu("甲课", "整理学习材料");
    expect(start).not.toHaveBeenCalled();
    expect(
      screen
        .getByRole("menuitem", { name: "整理学习材料" })
        .getAttribute("title"),
    ).toBe("尚未配置模型");
    expect(screen.queryByRole("button", { name: "配置 AI" })).toBeNull();
    expect(document.querySelector(".video-ai-readiness")).toBeNull();
    expect(settings).not.toHaveBeenCalled();
    menu("乙课", "查看 AI 总结");
    expect(await screen.findByText("可阅读的原有讲义")).toBeTruthy();
  });
  it.each(["organize", "retry", "cancel", "export"] as const)(
    "ignores late %s UI effects after switching courses while the callback completes",
    async (kind) => {
      let finish: (() => void) | undefined;
      const action = vi.fn(
        () =>
          new Promise<void>((resolve) => {
            finish = resolve;
          }),
      );
      const oldJob: TranscriptJob = {
        ...completed,
        source_id: lesson.id,
        title: lesson.title,
        status: kind === "cancel" ? "queued" : "failed",
        progress: kind === "cancel" ? 10 : 100,
      };
      const callbacks = {
        onStartTranscript: action,
        onRetryTranscript: action,
        onCancelTranscript: action,
        onCreateSlidesPdf: action,
        onPlay: async () => ({ url: "https://example.test/lesson.mp4" }),
      };
      const { rerender } = render(
        <VideosView
          courseId={12}
          videos={[lesson]}
          transcriptJobs={kind === "retry" || kind === "cancel" ? [oldJob] : []}
          {...callbacks}
        />,
      );
      if (kind === "cancel")
        fireEvent.click(screen.getByRole("button", { name: "取消" }));
      else
        menu(
          lesson.title,
          kind === "retry"
            ? "重试整理学习材料"
            : kind === "export"
              ? "生成课件 PDF"
              : "整理学习材料",
        );
      expect(action).toHaveBeenCalledOnce();
      rerender(<VideosView courseId={13} videos={[other]} {...callbacks} />);
      fireEvent.click(screen.getByRole("button", { name: "批量选择" }));
      const selection = screen.getByRole("checkbox", {
        name: "选择 乙课",
      }) as HTMLInputElement;
      fireEvent.click(selection);
      await act(async () => finish?.());
      expect(action).toHaveBeenCalledOnce();
      expect(selection.checked).toBe(true);
      expect(screen.getByLabelText("批量操作").textContent).toContain(
        "已选择 1 项",
      );
      for (const text of [
        "已开始整理本节学习材料。",
        "已重新开始整理本节学习材料。",
        "任务已取消。",
        "课件已生成（临时文件）。",
      ])
        expect(screen.queryByText(text)).toBeNull();
      expect(screen.queryByRole("dialog")).toBeNull();
    },
  );

  it("does not let an old summary completion clear the new course pending summary or open old results", async () => {
    let finishOld: (() => void) | undefined;
    let finishNew: (() => void) | undefined;
    const start = vi.fn(
      (items: CourseVideoItem[]) =>
        new Promise<void>((resolve) => {
          if (items[0].id === lesson.id) finishOld = resolve;
          else finishNew = resolve;
        }),
    );
    const { rerender } = render(
      <VideosView courseId={12} videos={[lesson]} onStartTranscript={start} />,
    );
    menu(lesson.title, "生成 AI 总结");
    rerender(
      <VideosView courseId={13} videos={[other]} onStartTranscript={start} />,
    );
    menu(other.title, "生成 AI 总结");
    await act(async () => finishOld?.());
    expect(screen.queryByText("已开始生成 AI 总结。")).toBeNull();
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "乙课 更多操作" }));
    expect(
      (
        screen.getByRole("menuitem", {
          name: "AI 总结生成中",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
    fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" });
    await act(async () => finishNew?.());
    const newJob = { ...completed, phase1_status: "completed" as const };
    rerender(
      <VideosView
        courseId={13}
        videos={[other]}
        transcriptJobs={[newJob]}
        onStartTranscript={start}
      />,
    );
    expect(await screen.findByText("可阅读的原有讲义")).toBeTruthy();
  });

  it("does not show an old course failure or cache its late PDF as a current artifact", async () => {
    let failOld: ((reason: Error) => void) | undefined;
    let finishPdf:
      | ((value: { scope: "video"; url: string }) => void)
      | undefined;
    const start = vi.fn(
      () =>
        new Promise<void>((_, reject) => {
          failOld = reject;
        }),
    );
    const pdf = vi.fn(
      () =>
        new Promise<{ scope: "video"; url: string }>((resolve) => {
          finishPdf = resolve;
        }),
    );
    const play = async () => ({ url: "https://example.test/video.mp4" });
    // Reused provider ID exposes a stale artifact even when the new course has no export.
    const newCourseLesson = { ...other, id: lesson.id };
    const { rerender } = render(
      <VideosView
        courseId={12}
        videos={[lesson]}
        onStartTranscript={start}
        onCreateSlidesPdf={pdf}
        onPlay={play}
      />,
    );
    menu(lesson.title, "整理学习材料");
    menu(lesson.title, "生成课件 PDF");
    rerender(
      <VideosView
        courseId={13}
        videos={[newCourseLesson]}
        onStartTranscript={start}
        onCreateSlidesPdf={pdf}
        onPlay={play}
      />,
    );
    await act(async () => {
      failOld?.(new Error("旧课程服务异常"));
      finishPdf?.({ scope: "video", url: "https://example.test/old.pdf" });
    });
    expect(screen.queryByText("旧课程服务异常")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "播放 乙课" }));
    await waitFor(() =>
      expect(
        (screen.getByRole("button", { name: "播放 乙课" }) as HTMLButtonElement)
          .disabled,
      ).toBe(false),
    );
    fireEvent.mouseDown(screen.getByRole("tab", { name: "课件" }), {
      button: 0,
      ctrlKey: false,
    });
    expect(screen.queryByText("课件已生成（临时文件）")).toBeNull();
    expect(screen.queryByText("课程课件已生成（临时文件）")).toBeNull();
    expect(screen.queryByRole("button", { name: "打开" })).toBeNull();
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
    expect(screen.getByText("新字幕已准备")).toBeTruthy();
    await act(async () => {
      finishOld?.({ subtitleMessage: "旧字幕迟到" });
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.queryByText("旧字幕迟到")).toBeNull();
    expect(screen.getByText("新字幕已准备")).toBeTruthy();
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
      screen.getByText("字幕读取超时，可从录像更多操作重新载入字幕。"),
    ).toBeTruthy();
    expect(document.querySelector("video source")?.getAttribute("src")).toBe(
      "https://example.test/lesson.mp4",
    );
    menu(lesson.title, "载入播放器字幕");
    await act(async () => vi.advanceTimersByTimeAsync(0));
    expect(screen.getByText("重新载入成功")).toBeTruthy();
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
