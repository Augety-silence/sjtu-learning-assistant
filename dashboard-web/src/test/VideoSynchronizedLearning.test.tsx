// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  type VideoLearningSegmentsResult,
  VideoSynchronizedLearning,
} from "@/components/VideoSynchronizedLearning";
import type { CourseVideoItem } from "@/components/VideosView";
import { act, cleanup, render, screen, waitFor } from "@/test/render";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});
const first: CourseVideoItem = {
  id: "a",
  title: "A",
  courseName: "课程",
  source: "video_space",
};
const second = { ...first, id: "b", title: "B" };
const segment = (id: string, content: string) => ({
  id,
  startMs: 0,
  endMs: 10000,
  title: "知识点",
  content,
});
describe("video synchronized learning", () => {
  it("keeps a late result from an old video out of the current video", async () => {
    let finishOld: ((result: VideoLearningSegmentsResult) => void) | undefined;
    const load = vi
      .fn()
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finishOld = resolve;
          }),
      )
      .mockResolvedValueOnce({
        status: "ready",
        segments: [segment("b", "当前B讲义")],
      });
    const props = {
      playbackTime: 0,
      refreshing: false,
      onLoad: load,
      onSeek: vi.fn(),
    };
    const view = render(<VideoSynchronizedLearning video={first} {...props} />);
    await waitFor(() => expect(load).toHaveBeenCalledOnce());
    view.rerender(<VideoSynchronizedLearning video={second} {...props} />);
    expect(await screen.findByText("当前B讲义")).toBeTruthy();
    await act(async () =>
      finishOld?.({ status: "ready", segments: [segment("a", "迟到A讲义")] }),
    );
    expect(screen.queryByText("迟到A讲义")).toBeNull();
  });
  it("polls provisional content until the final learning segment arrives", async () => {
    vi.useFakeTimers();
    const load = vi
      .fn()
      .mockResolvedValueOnce({
        status: "processing",
        segments: [
          { ...segment("preview", "原始字幕预览"), provisional: true },
        ],
      })
      .mockResolvedValueOnce({
        status: "ready",
        segments: [segment("final", "最终AI讲义")],
      });
    render(
      <VideoSynchronizedLearning
        video={first}
        revision="active-work"
        playbackTime={0}
        refreshing={false}
        onLoad={load}
        onSeek={vi.fn()}
      />,
    );
    await act(async () => undefined);
    expect(screen.getByText("原始字幕预览")).toBeTruthy();
    expect(
      screen.getByText("基于原始字幕，深度校对尚未完成或未通过"),
    ).toBeTruthy();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(screen.getByText("最终AI讲义")).toBeTruthy();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(load).toHaveBeenCalledTimes(2);
  });

  it("accepts new learning revisions and keeps polling while processing", async () => {
    vi.useFakeTimers();
    const load = vi
      .fn()
      .mockResolvedValueOnce({
        status: "processing",
        isProcessing: true,
        sourceRawSha256: "raw-hash",
        learningRevision: "revision-1",
        segments: [{ ...segment("draft-1", "首版内容"), provisional: true }],
      })
      .mockResolvedValueOnce({
        status: "processing",
        isProcessing: true,
        sourceRawSha256: "raw-hash",
        learningRevision: "revision-2",
        segments: [
          { ...segment("draft-2", "更新后的内容"), provisional: true },
        ],
      })
      .mockResolvedValueOnce({
        status: "ready",
        isProcessing: false,
        sourceRawSha256: "raw-hash",
        learningRevision: "revision-3",
        segments: [segment("final", "最终校订内容")],
      });

    render(
      <VideoSynchronizedLearning
        video={first}
        playbackTime={0}
        refreshing={false}
        onLoad={load}
        onSeek={vi.fn()}
      />,
    );

    await act(async () => undefined);
    expect(screen.getByText("首版内容")).toBeTruthy();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(screen.getByText("更新后的内容")).toBeTruthy();
    expect(screen.queryByText("首版内容")).toBeNull();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(screen.getByText("最终校订内容")).toBeTruthy();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(load).toHaveBeenCalledTimes(3);
  });

  it("uses the continuous range around playback and preserves later material across a gap", async () => {
    const load = vi.fn(async () => ({
      status: "ready" as const,
      availableRanges: [
        [0, 10000],
        [30000, 60000],
      ] as [number, number][],
      segments: [
        segment("first", "前段讲义"),
        { ...segment("later", "离散后段讲义"), startMs: 30000, endMs: 60000 },
      ],
    }));
    const props = {
      video: first,
      refreshing: false,
      onLoad: load,
      onSeek: vi.fn(),
    };
    const view = render(
      <VideoSynchronizedLearning {...props} playbackTime={5} />,
    );
    expect(
      await screen.findByText("当前段已准备至 00:10；领先播放 5 秒"),
    ).toBeTruthy();
    expect(screen.queryByText(/当前段已准备至 01:00/)).toBeNull();
    view.rerender(<VideoSynchronizedLearning {...props} playbackTime={10} />);
    expect(screen.getByText("当前时间尚无已准备内容。")).toBeTruthy();
    expect(screen.getByText("离散后段讲义")).toBeTruthy();
    expect(document.querySelector("[aria-current='true']")).toBeNull();
    expect(load).toHaveBeenCalledOnce();
  });

  it("merges adjacent available ranges when measuring the lead", async () => {
    const load = vi.fn(async () => ({
      status: "ready" as const,
      availableRanges: [
        [10000, 20000],
        [0, 10000],
      ] as [number, number][],
      segments: [segment("first", "内容")],
    }));
    render(
      <VideoSynchronizedLearning
        video={first}
        playbackTime={5}
        refreshing={false}
        onLoad={load}
        onSeek={vi.fn()}
      />,
    );
    expect(
      await screen.findByText("当前段已准备至 00:20；领先播放 15 秒"),
    ).toBeTruthy();
  });

  it("labels terminal original excerpts honestly and stops polling them", async () => {
    vi.useFakeTimers();
    const load = vi.fn().mockResolvedValue({
      status: "ready",
      segments: [
        {
          ...segment("original", "原始字幕内容"),
          kind: "transcript",
          provisional: true,
        },
      ],
    });
    render(
      <VideoSynchronizedLearning
        video={first}
        playbackTime={0}
        refreshing={false}
        onLoad={load}
        onSeek={vi.fn()}
      />,
    );
    await act(async () => undefined);
    expect(screen.getByText("原字幕摘录")).toBeTruthy();
    expect(screen.queryByText(/AI 讲义正在整理/)).toBeNull();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(20000);
    });
    expect(load).toHaveBeenCalledOnce();
  });

  it("shows the real terminal stage and offers an explicit read retry", async () => {
    const onRetry = vi.fn();
    const load = vi.fn().mockResolvedValue({
      status: "ready",
      isProcessing: false,
      stage: "deep_review",
      errorCode: "ai_unavailable",
      errorMessage: "AI 服务连接失败",
      retryable: true,
      updatedAt: "2026-10-14T10:00:00Z",
      completedSegments: 3,
      failedSegments: 6,
      segments: [segment("draft", "可读初稿")],
    });
    render(
      <VideoSynchronizedLearning
        video={first}
        playbackTime={0}
        refreshing={false}
        onLoad={load}
        onSeek={vi.fn()}
        onRetry={onRetry}
      />,
    );
    expect(await screen.findByText(/当前阶段：deep_review/)).toBeTruthy();
    expect(screen.getByText(/ai_unavailable：AI 服务连接失败/)).toBeTruthy();
    expect(screen.getByText(/已完成 3 段 · 失败 6 段/)).toBeTruthy();
    expect(
      screen.getByText(/初稿可读，部分校对未通过；任务已结束/),
    ).toBeTruthy();
    await act(async () =>
      screen.getByRole("button", { name: "继续失败阶段" }).click(),
    );
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("labels completed uncertain review for re-check without hiding active work", async () => {
    const onRetry = vi.fn();
    const load = vi.fn().mockResolvedValue({
      status: "ready" as const,
      isProcessing: false,
      processingDone: true,
      autoRetryable: false,
      stage: "needs_review",
      errorCode: "content_uncertain",
      retryable: true,
      completedSegments: 49,
      failedSegments: 0,
      segments: [segment("draft", "可读初稿")],
    });
    const view = render(
      <VideoSynchronizedLearning
        video={first}
        playbackTime={0}
        refreshing={false}
        onLoad={load}
        onSeek={vi.fn()}
        onRetry={onRetry}
      />,
    );
    expect(await screen.findByText("校对已结束，部分内容需核对")).toBeTruthy();
    await act(async () =>
      screen.getByRole("button", { name: "重新校对" }).click(),
    );
    expect(onRetry).toHaveBeenCalledOnce();

    view.rerender(
      <VideoSynchronizedLearning
        video={first}
        revision="active-work"
        playbackTime={0}
        refreshing={false}
        onLoad={vi.fn().mockResolvedValue({
          status: "ready" as const,
          isProcessing: true,
          processingDone: true,
          stage: "needs_review",
          errorCode: "content_uncertain",
          segments: [segment("draft", "可读初稿")],
        })}
        onSeek={vi.fn()}
        onRetry={onRetry}
      />,
    );
    expect(await screen.findByText("初稿可读，校对仍在进行")).toBeTruthy();
    expect(screen.queryByText("校对已结束，部分内容需核对")).toBeNull();
  });

  it("shows a scheduled retry time and keeps the manual retry available", async () => {
    const onRetry = vi.fn();
    const retryAfter = new Date(Date.now() + 60 * 60 * 1000).toISOString();
    const expectedTime = new Date(retryAfter).toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
    });
    render(
      <VideoSynchronizedLearning
        video={first}
        playbackTime={0}
        refreshing={false}
        onLoad={vi.fn().mockResolvedValue({
          status: "ready" as const,
          isProcessing: false,
          autoRetryable: true,
          retryable: true,
          errorCode: "ai_unavailable",
          retryAfter,
          segments: [segment("draft", "可读初稿")],
        })}
        onSeek={vi.fn()}
        onRetry={onRetry}
      />,
    );
    expect(
      await screen.findByText(`将自动继续失败阶段（预计 ${expectedTime}）`),
    ).toBeTruthy();
    await act(async () =>
      screen.getByRole("button", { name: "继续失败阶段" }).click(),
    );
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("never schedules an automatic retry for uncertain content", async () => {
    const retryAfter = new Date(Date.now() + 60 * 60 * 1000).toISOString();
    render(
      <VideoSynchronizedLearning
        video={first}
        playbackTime={0}
        refreshing={false}
        onLoad={vi.fn().mockResolvedValue({
          status: "ready" as const,
          isProcessing: false,
          autoRetryable: true,
          retryable: true,
          stage: "needs_review",
          errorCode: "content_uncertain",
          retryAfter,
          segments: [segment("draft", "可读初稿")],
        })}
        onSeek={vi.fn()}
        onRetry={vi.fn()}
      />,
    );
    await screen.findByRole("button", { name: "重新校对" });
    expect(screen.queryByText(/将自动继续失败阶段/)).toBeNull();
  });

  it("does not promise an automatic retry after its budget is exhausted", async () => {
    render(
      <VideoSynchronizedLearning
        video={first}
        playbackTime={0}
        refreshing={false}
        onLoad={vi.fn().mockResolvedValue({
          status: "ready" as const,
          isProcessing: false,
          autoRetryable: false,
          retryable: true,
          errorCode: "ai_unavailable",
          retryAfter: null,
          segments: [segment("draft", "可读初稿")],
        })}
        onSeek={vi.fn()}
        onRetry={vi.fn()}
      />,
    );
    await screen.findByRole("button", { name: "继续失败阶段" });
    expect(screen.queryByText(/将自动继续失败阶段/)).toBeNull();
  });

  it("passes an abort signal and ignores the response after the source changes", async () => {
    let signal: AbortSignal | undefined;
    const load = vi.fn((video: CourseVideoItem, nextSignal?: AbortSignal) => {
      if (video.id === "a") {
        signal = nextSignal;
        return new Promise<VideoLearningSegmentsResult>(() => undefined);
      }
      return Promise.resolve<VideoLearningSegmentsResult>({
        status: "ready",
        segments: [segment("b", "B材料")],
      });
    });
    const props = {
      playbackTime: 0,
      refreshing: false,
      onLoad: load,
      onSeek: vi.fn(),
    };
    const view = render(<VideoSynchronizedLearning video={first} {...props} />);
    view.rerender(<VideoSynchronizedLearning video={second} {...props} />);
    await screen.findByText("B材料");
    expect(signal?.aborted).toBe(true);
  });

  it("offers a manual recheck after a read error without polling repeatedly", async () => {
    const load = vi
      .fn()
      .mockRejectedValueOnce(new Error("暂时无法读取学习材料"))
      .mockResolvedValueOnce({
        status: "ready",
        segments: [segment("ok", "重查成功")],
      });
    render(
      <VideoSynchronizedLearning
        video={first}
        playbackTime={0}
        refreshing={false}
        onLoad={load}
        onSeek={vi.fn()}
      />,
    );
    expect(await screen.findByText("暂时无法读取学习材料")).toBeTruthy();
    await act(async () =>
      screen.getByRole("button", { name: "重新检查" }).click(),
    );
    expect(await screen.findByText("重查成功")).toBeTruthy();
    expect(load).toHaveBeenCalledTimes(2);
  });

  describe("processing_state contract", () => {
    const future = () => new Date(Date.now() + 60 * 60 * 1000).toISOString();
    const renderState = (result: Partial<VideoLearningSegmentsResult>) => {
      const onRetry = vi.fn();
      const load = vi.fn().mockResolvedValue({
        status: "ready" as const,
        isProcessing: false,
        segments: [segment("draft", "可读初稿")],
        ...result,
      });
      render(
        <VideoSynchronizedLearning
          video={first}
          playbackTime={0}
          refreshing={false}
          onLoad={load}
          onSeek={vi.fn()}
          onRetry={onRetry}
        />,
      );
      return { load, onRetry };
    };

    it("finished review keeps material, shows no countdown and never retries by itself", async () => {
      vi.useFakeTimers();
      const { load, onRetry } = renderState({
        processingState: "finished_needs_review",
        processingDone: true,
        autoRetryable: true,
        retryable: true,
        stage: "needs_review",
        errorCode: "content_uncertain",
        retryAfter: future(),
      });
      await act(async () => undefined);
      expect(screen.getByText("校对已结束，部分内容需核对")).toBeTruthy();
      expect(screen.getByText("可读初稿")).toBeTruthy();
      expect(screen.queryByText(/将自动继续失败阶段/)).toBeNull();
      expect(screen.queryByText("初稿可读，校对仍在进行")).toBeNull();
      expect(screen.getByRole("button", { name: "重新校对" })).toBeTruthy();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(20000);
      });
      expect(load).toHaveBeenCalledOnce();
      expect(onRetry).not.toHaveBeenCalled();
    });

    it("recovering names the failed stage and shows the real retry time", async () => {
      const retryAfter = future();
      const expected = new Date(retryAfter).toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
      });
      renderState({
        processingState: "recovering",
        autoRetryable: true,
        retryable: true,
        stage: "guide_generation",
        errorCode: "v1_guide_schema",
        retryAfter,
      });
      expect(
        await screen.findByText("初稿可读，学习讲义生成未完成，等待自动恢复"),
      ).toBeTruthy();
      expect(screen.getByText("失败阶段：学习讲义生成")).toBeTruthy();
      expect(
        screen.getByText(`将自动继续失败阶段（预计 ${expected}）`),
      ).toBeTruthy();
    });

    it("recovering without a future retry time promises no countdown", async () => {
      renderState({
        processingState: "recovering",
        autoRetryable: true,
        retryable: true,
        stage: "audit",
        retryAfter: new Date(Date.now() - 60_000).toISOString(),
      });
      await screen.findByText("失败阶段：讲义审校");
      expect(screen.queryByText(/将自动继续失败阶段/)).toBeNull();
    });

    it("exhausted recovery says so and only offers a manual retry", async () => {
      const { onRetry } = renderState({
        processingState: "recovery_exhausted",
        autoRetryable: true,
        retryable: true,
        stage: "map",
        errorCode: "v1_map_fallback",
        retryAfter: future(),
      });
      expect(
        await screen.findByText("自动恢复已用尽，可手动重试"),
      ).toBeTruthy();
      expect(screen.getByText("失败阶段：字幕分块整理")).toBeTruthy();
      expect(screen.queryByText(/将自动继续失败阶段/)).toBeNull();
      expect(onRetry).not.toHaveBeenCalled();
      await act(async () =>
        screen.getByRole("button", { name: "继续失败阶段" }).click(),
      );
      expect(onRetry).toHaveBeenCalledOnce();
    });

    it.each([
      ["completed", "学习材料与校对均已完成"],
      ["cancelled", "整理已取消，已生成的内容仍保留"],
      ["waiting_source", "等待课程字幕就绪"],
      ["ai_unconfigured", "未配置 AI 服务，当前显示未经 AI 校对的内容"],
      ["failed", "初稿可读，字幕整理未完成；任务已结束，可手动重试"],
    ] as const)("labels %s accurately", async (state, copy) => {
      renderState({
        processingState: state,
        stage: "v1",
        retryAfter: future(),
      });
      expect(await screen.findByText(copy)).toBeTruthy();
      expect(screen.queryByText(/将自动继续失败阶段/)).toBeNull();
      expect(screen.queryByText("校对已结束，部分内容需核对")).toBeNull();
    });

    it("processing shows real progress instead of a finished state", async () => {
      renderState({
        processingState: "processing",
        isProcessing: true,
        completedSegments: 2,
        failedSegments: 0,
      });
      expect(await screen.findByText("初稿可读，校对仍在进行")).toBeTruthy();
      expect(screen.getByText(/已完成 2 段 · 失败 0 段/)).toBeTruthy();
    });

    it("ignores an unknown state and falls back to legacy flags", async () => {
      renderState({
        processingState: "future_state" as never,
        processingDone: true,
        stage: "needs_review",
        errorCode: "content_uncertain",
      });
      expect(
        await screen.findByText("校对已结束，部分内容需核对"),
      ).toBeTruthy();
    });

    it("legacy responses without flags never claim success", async () => {
      renderState({});
      expect(
        await screen.findByText("初稿可读，最终校对状态尚未确认"),
      ).toBeTruthy();
      expect(screen.queryByText("学习材料与校对均已完成")).toBeNull();
    });
  });
});
