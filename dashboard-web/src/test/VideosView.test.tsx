// @vitest-environment jsdom

import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";
import { type CourseVideoItem, VideosView } from "@/components/VideosView";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@/test/render";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const videos: CourseVideoItem[] = [
  {
    id: "v1",
    title: "第一讲",
    courseName: "数据结构",
    source: "canvas",
    classroom: "东上院 101",
    recordedAt: "2026-09-28T10:15:00+08:00",
    duration: 3661,
    playable: true,
    playbackUrl: "https://example.test/video.mp4",
    subtitleId: "subtitle-1",
    supportsSubtitle: true,
    supportsSlidesPdf: true,
  },
  {
    id: "v2",
    title: "旧版录像",
    courseName: "数据结构",
    source: "legacy",
    playable: false,
  },
  {
    id: "v3",
    title: "第三讲",
    courseName: "数据结构",
    source: "video_space",
    playable: true,
    supportsSubtitle: true,
  },
];

describe("VideosView", () => {
  it("uses an accessible custom course picker and supports pointer selection", () => {
    const change = vi.fn();
    render(
      <VideosView
        videos={videos}
        courseId={1}
        courseName="数据结构"
        courseOptions={[
          { id: 1, name: "数据结构" },
          { id: 2, name: "文本分析与大语言模型（长课程名称）" },
        ]}
        onCourseChange={change}
      />,
    );

    expect(document.querySelector("select")).toBeNull();
    const trigger = screen.getByRole("combobox", { name: "选择课程" });
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(trigger);
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    const option = screen.getByRole("option", {
      name: "文本分析与大语言模型（长课程名称）",
    });
    fireEvent.click(option);
    expect(change).toHaveBeenCalledWith(2);
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
  });

  it("supports Arrow/Home/End/Enter/Space and Escape in the course picker", async () => {
    const change = vi.fn();
    const courseOptions =
      "数据结构|文本分析与大语言模型|操作系统|计算机网络|软件工程|管理会计|大学英语|高等数学"
        .split("|")
        .map((name, index) => ({ id: index + 1, name }));
    render(
      <VideosView
        videos={videos}
        courseId={1}
        courseOptions={courseOptions}
        onCourseChange={change}
      />,
    );
    const trigger = screen.getByRole("combobox", { name: "选择课程" });
    fireEvent.keyDown(trigger, { key: "End" });
    expect(screen.getAllByRole("option")).toHaveLength(courseOptions.length);
    for (const course of courseOptions) {
      const option = screen.getByRole("option", { name: course.name });
      expect(option.getAttribute("aria-label")).toBe(course.name);
    }
    await waitFor(() =>
      expect(document.activeElement).toBe(
        screen.getByRole("option", { name: "高等数学" }),
      ),
    );
    fireEvent.keyDown(document.activeElement as HTMLElement, { key: "Home" });
    expect(document.activeElement).toBe(
      screen.getByRole("option", { name: "数据结构" }),
    );
    fireEvent.keyDown(document.activeElement as HTMLElement, {
      key: "ArrowDown",
    });
    fireEvent.keyDown(document.activeElement as HTMLElement, { key: " " });
    expect(change).toHaveBeenCalledWith(2);

    fireEvent.keyDown(trigger, { key: "ArrowUp" });
    await waitFor(() =>
      expect(document.activeElement).toBe(
        screen.getByRole("option", { name: "高等数学" }),
      ),
    );
    fireEvent.keyDown(document.activeElement as HTMLElement, { key: "Escape" });
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    await waitFor(() => expect(document.activeElement).toBe(trigger));
  });

  it("filters sources and marks the playing row as selected", async () => {
    const play = vi.fn(async () => ({ url: "https://example.test/play.mp4" }));
    render(<VideosView videos={videos} onPlay={play} />);
    fireEvent.mouseDown(screen.getByRole("tab", { name: "Canvas" }), {
      button: 0,
      ctrlKey: false,
    });
    expect(screen.getByText("第一讲")).toBeTruthy();
    expect(screen.queryByText("旧版录像")).toBeNull();
    fireEvent.click(screen.getAllByRole("button", { name: "播放" })[0]);
    await waitFor(() => expect(play).toHaveBeenCalledWith(videos[0]));
    expect(screen.getByLabelText("播放 第一讲")).toBeTruthy();
    expect(
      screen
        .getAllByText("第一讲")
        .find((node) => node.closest("li"))
        ?.closest("li")
        ?.getAttribute("aria-current"),
    ).toBe("true");
  });

  it("shows selection-only batch actions and preserves the real combined transcript capability", async () => {
    const start = vi.fn(async () => undefined);
    render(<VideosView videos={videos} onStartTranscript={start} />);
    expect(screen.queryByLabelText("批量操作")).toBeNull();
    fireEvent.click(screen.getByRole("checkbox", { name: "选择 第三讲" }));
    const batch = screen.getByLabelText("批量操作");
    expect(batch.textContent).toContain("已选择 1 项");
    expect(
      (screen.getByRole("button", { name: "AI 整理" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "生成字幕" }));
    await waitFor(() => expect(start).toHaveBeenCalledWith([videos[2]]));
    expect(screen.queryByLabelText("批量操作")).toBeNull();
  });

  it("generates an AI summary through the transcript flow and opens it after Phase1", async () => {
    let finishStart: (() => void) | undefined;
    const start = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          finishStart = resolve;
        }),
    );
    const callbacks = {
      onStartTranscript: start,
      onRetryTranscript: vi.fn(async () => undefined),
      onRevealTranscript: vi.fn(async () => undefined),
    };
    const view = render(<VideosView videos={[videos[2]]} {...callbacks} />);

    fireEvent.click(screen.getByRole("button", { name: "播放" }));
    fireEvent.mouseDown(screen.getByRole("tab", { name: "AI 总结" }), {
      button: 0,
      ctrlKey: false,
    });
    expect(
      screen.getByText("生成前会先规整字幕并完成 AI 校对，再生成总结。"),
    ).toBeTruthy();

    const generate = screen.getByRole("button", { name: "生成 AI 总结" });
    await waitFor(() => expect(generate).toHaveProperty("disabled", false));
    fireEvent.click(generate);
    fireEvent.click(generate);
    expect(start).toHaveBeenCalledTimes(1);
    expect(start).toHaveBeenCalledWith([videos[2]]);
    expect(generate.getAttribute("aria-busy")).toBe("true");

    await act(async () => finishStart?.());
    const processingJob = {
      id: "summary-job",
      batch_id: "summary-batch",
      source_id: "v3",
      title: "第三讲",
      status: "organizing" as const,
      stage: "phase1",
      progress: 80,
      attempts: 1,
      message: "正在进行 AI 校对",
    };
    view.rerender(
      <VideosView
        videos={[videos[2]]}
        transcriptJobs={[processingJob]}
        {...callbacks}
      />,
    );
    expect(screen.getByText("正在进行 AI 校对")).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "第三讲 更多操作" }),
    ).toBeTruthy();
    expect(screen.queryByRole("menu")).toBeNull();

    view.rerender(
      <VideosView
        videos={[videos[2]]}
        transcriptJobs={[
          {
            ...processingJob,
            status: "completed",
            stage: "completed",
            progress: 100,
            message: "字幕规整完成",
          },
        ]}
        {...callbacks}
      />,
    );
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(
      screen.getByText(
        "字幕已规整，正在等待 AI 校对完成；完成后才能查看总结。",
      ),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: "查看 AI 总结" })).toBeNull();

    view.rerender(
      <VideosView
        videos={[videos[2]]}
        transcriptJobs={[
          {
            ...processingJob,
            status: "completed",
            stage: "completed",
            progress: 100,
            phase1_status: "completed",
          },
        ]}
        {...callbacks}
      />,
    );
    const drawer = await screen.findByRole("dialog");
    expect(drawer).toBeTruthy();
    expect(
      screen
        .getByRole("tab", { name: "本节要点" })
        .getAttribute("aria-selected"),
    ).toBe("true");
    await waitFor(() =>
      expect(document.activeElement).toBe(
        drawer.querySelector(
          '.transcript-drawer-header button[aria-label="关闭字幕详情"]',
        ),
      ),
    );
  });

  it("shows a retryable summary action after start or job failure", async () => {
    const start = vi
      .fn<() => Promise<void>>()
      .mockRejectedValueOnce(new Error("AI 服务暂时不可用"))
      .mockResolvedValueOnce(undefined);
    const retry = vi.fn(async () => undefined);
    const view = render(
      <VideosView
        videos={[videos[2]]}
        onStartTranscript={start}
        onRetryTranscript={retry}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "播放" }));
    fireEvent.mouseDown(screen.getByRole("tab", { name: "AI 总结" }), {
      button: 0,
      ctrlKey: false,
    });
    const generate = screen.getByRole("button", { name: "生成 AI 总结" });
    await waitFor(() => expect(generate).toHaveProperty("disabled", false));
    fireEvent.click(generate);
    expect(
      await screen.findByText(/AI 服务暂时不可用.*可重试生成/),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "重试生成 AI 总结" }));
    await waitFor(() => expect(start).toHaveBeenCalledTimes(2));

    view.rerender(
      <VideosView
        videos={[videos[2]]}
        transcriptJobs={[
          {
            id: "failed-summary",
            batch_id: "summary-batch",
            source_id: "v3",
            title: "第三讲",
            status: "failed",
            stage: "phase1",
            progress: 70,
            attempts: 1,
            error: "AI 校对失败",
          },
        ]}
        onStartTranscript={start}
        onRetryTranscript={retry}
      />,
    );
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "重试生成 AI 总结" }),
      ).toBeTruthy(),
    );
    fireEvent.click(screen.getByRole("button", { name: "重试生成 AI 总结" }));
    await waitFor(() => expect(retry).toHaveBeenCalledTimes(1));
  });

  it("renders unified text statuses and learning tool tabs", () => {
    render(
      <VideosView
        videos={videos}
        transcriptJobs={[
          {
            id: "j1",
            batch_id: "b1",
            source_id: "v1",
            title: "第一讲",
            status: "completed",
            stage: "completed",
            progress: 100,
            attempts: 1,
          },
          {
            id: "j2",
            batch_id: "b1",
            source_id: "v3",
            title: "第三讲",
            status: "partial",
            stage: "partial",
            progress: 80,
            attempts: 1,
          },
        ]}
        tasks={[
          {
            id: "t1",
            videoId: "v1",
            title: "第一讲.pdf",
            kind: "slides_pdf",
            status: "failed",
            progress: 40,
          },
        ]}
      />,
    );
    expect(screen.getAllByText("已完成").length).toBeGreaterThan(0);
    expect(screen.getAllByText("部分完成").length).toBeGreaterThan(0);
    expect(screen.getAllByText("失败").length).toBeGreaterThan(0);
    expect(screen.getAllByText("待处理").length).toBeGreaterThan(0);
    expect(screen.getByRole("tab", { name: "字幕" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "AI 总结" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "课件 PDF" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "学习笔记" })).toBeTruthy();
  });

  it("keeps a failed transcript at 100% failed without artifact evidence", () => {
    const { container } = render(
      <VideosView
        videos={[videos[2]]}
        transcriptJobs={[
          {
            id: "contradictory-job",
            batch_id: "batch",
            source_id: "v3",
            title: "第三讲",
            status: "failed",
            stage: "phase1",
            progress: 100,
            attempts: 1,
          },
        ]}
      />,
    );

    expect(container.querySelector(".video-status-failed")).toBeTruthy();
    expect(container.querySelector(".video-status-partial")).toBeNull();
  });

  it("keeps low-frequency recording actions behind progressive disclosure", () => {
    render(
      <VideosView
        videos={[videos[2]]}
        onStartTranscript={vi.fn(async () => undefined)}
      />,
    );

    expect(screen.queryByRole("button", { name: "生成 AI 总结" })).toBeNull();
    const more = screen.getByRole("button", { name: "第三讲 更多操作" });
    expect(screen.queryByRole("menu")).toBeNull();
    fireEvent.click(more);
    expect(screen.getByRole("menuitem", { name: "生成 AI 总结" })).toBeTruthy();
  });

  it("keeps the no-task state compact and honest about local notes", () => {
    const { container } = render(<VideosView videos={videos} />);
    expect(container.querySelector(".video-jobs.is-empty")).toBeTruthy();
    expect(screen.getByText("暂无进行中的任务")).toBeTruthy();
    fireEvent.mouseDown(screen.getByRole("tab", { name: "学习笔记" }), {
      button: 0,
      ctrlKey: false,
    });
    expect(screen.getByText(/本地笔记能力稍后支持/)).toBeTruthy();
  });

  it("creates and revokes subtitle Blob URLs when switching videos and unmounting", async () => {
    const createObjectURL = vi
      .fn()
      .mockReturnValueOnce("blob:subtitle-1")
      .mockReturnValueOnce("blob:subtitle-2");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", { ...URL, createObjectURL, revokeObjectURL });
    const remoteVideos = [
      { ...videos[0], source: "video_space" as const },
      { ...videos[2], playbackUrl: "https://example.test/video-2.mp4" },
    ];
    const view = render(
      <VideosView
        videos={remoteVideos}
        onPlay={async (video) => ({ url: video.playbackUrl })}
        onLoadSubtitles={async () => ({
          subtitleVtt: "WEBVTT\n",
          subtitleMessage: "字幕已加载。",
        })}
      />,
    );
    fireEvent.click(screen.getAllByRole("button", { name: "播放" })[0]);
    await waitFor(() => expect(createObjectURL).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getAllByRole("button", { name: "播放" })[1]);
    await waitFor(() => expect(createObjectURL).toHaveBeenCalledTimes(2));
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:subtitle-1");
    view.unmount();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:subtitle-2");
  });

  it("does not repeat the course name in row metadata and exposes key aria labels", () => {
    render(
      <VideosView
        courseId={12}
        courseName="管理会计"
        videos={[
          {
            id: "sjtu-video:12:99",
            title: "第3周 · 周一 · 第10节",
            courseName: "管理会计",
            teachingClass: "管理会计",
            classroom: "上院202",
            source: "video_space",
            recordedAt: "2026-09-28T10:15:00+08:00",
            playable: true,
            supportsSubtitle: true,
          },
        ]}
      />,
    );
    expect(screen.getAllByText("管理会计")).toHaveLength(1);
    expect(screen.getByLabelText("课程视频学习工作台")).toBeTruthy();
    expect(screen.getByLabelText("课程录像")).toBeTruthy();
    expect(screen.getByLabelText("选择当前来源的全部录像")).toBeTruthy();
    expect(screen.getByText(/上院202/)).toBeTruthy();
  });

  it("supports every learning shortcut, case-insensitive keys, clamp and transient feedback", async () => {
    render(<VideosView videos={videos} />);
    fireEvent.click(screen.getAllByRole("button", { name: "播放" })[0]);
    const media = (await screen.findByLabelText(
      "播放 第一讲",
    )) as HTMLVideoElement;
    let paused = true;
    Object.defineProperties(media, {
      paused: { configurable: true, get: () => paused },
      duration: { configurable: true, value: 100 },
    });
    fireEvent.loadedMetadata(media);
    const play = vi.fn(async () => {
      paused = false;
    });
    const pause = vi.fn(() => {
      paused = true;
    });
    Object.defineProperties(media, {
      play: { configurable: true, value: play },
      pause: { configurable: true, value: pause },
    });

    fireEvent.keyDown(document.body, { key: " " });
    await waitFor(() => expect(play).toHaveBeenCalledTimes(1));
    expect(screen.getByText("正在播放")).toBeTruthy();
    fireEvent.keyDown(document.body, { key: "K" });
    expect(pause).toHaveBeenCalledTimes(1);
    expect(screen.getByText("已暂停")).toBeTruthy();
    fireEvent.keyDown(document.body, { key: "k" });
    await waitFor(() => expect(play).toHaveBeenCalledTimes(2));

    media.currentTime = 50;
    fireEvent.keyDown(document.body, { key: "J" });
    expect(media.currentTime).toBe(40);
    expect(screen.getByText("后退10秒")).toBeTruthy();
    fireEvent.keyDown(document.body, { key: "l" });
    expect(media.currentTime).toBe(50);
    expect(screen.getByText("前进10秒")).toBeTruthy();
    fireEvent.keyDown(document.body, { key: "ArrowLeft" });
    fireEvent.keyUp(document.body, { key: "ArrowLeft" });
    expect(media.currentTime).toBe(45);
    fireEvent.keyDown(document.body, { key: "ArrowRight" });
    fireEvent.keyUp(document.body, { key: "ArrowRight" });
    expect(media.currentTime).toBe(50);

    media.currentTime = 3;
    fireEvent.keyDown(document.body, { key: "j" });
    expect(media.currentTime).toBe(0);
    media.currentTime = 97;
    fireEvent.keyDown(document.body, { key: "L" });
    expect(media.currentTime).toBe(100);

    fireEvent.keyDown(document.body, { key: "[" });
    expect(media.playbackRate).toBe(0.75);
    fireEvent.keyDown(document.body, { key: "]" });
    expect(media.playbackRate).toBe(1);
    fireEvent.keyDown(document.body, { key: "<", shiftKey: true });
    expect(media.playbackRate).toBe(0.75);
    fireEvent.keyDown(document.body, { key: ">", shiftKey: true });
    expect(media.playbackRate).toBe(1);
    fireEvent.keyDown(document.body, { key: ",", shiftKey: true });
    expect(media.playbackRate).toBe(0.75);
    fireEvent.keyDown(document.body, { key: ".", shiftKey: true });
    expect(media.playbackRate).toBe(1);
  });

  it("treats 349ms as one short seek and ignores key repeat", async () => {
    render(<VideosView videos={videos} />);
    fireEvent.click(screen.getAllByRole("button", { name: "播放" })[0]);
    const media = (await screen.findByLabelText(
      "播放 第一讲",
    )) as HTMLVideoElement;
    Object.defineProperty(media, "duration", {
      configurable: true,
      value: 100,
    });
    fireEvent.loadedMetadata(media);
    media.currentTime = 50;
    vi.useFakeTimers();

    fireEvent.keyDown(document.body, { key: "ArrowRight" });
    fireEvent.keyDown(document.body, { key: "ArrowRight", repeat: true });
    act(() => vi.advanceTimersByTime(349));
    expect(media.currentTime).toBe(50);
    fireEvent.keyUp(document.body, { key: "ArrowRight" });
    expect(media.currentTime).toBe(55);
    act(() => vi.advanceTimersByTime(500));
    expect(media.currentTime).toBe(55);
  });

  it("temporarily fast-forwards, adjusts speed and restores the base paused state", async () => {
    render(<VideosView videos={videos} />);
    fireEvent.click(screen.getAllByRole("button", { name: "播放" })[0]);
    const media = (await screen.findByLabelText(
      "播放 第一讲",
    )) as HTMLVideoElement;
    let paused = true;
    const play = vi.fn(async () => {
      paused = false;
    });
    const pause = vi.fn(() => {
      paused = true;
    });
    Object.defineProperties(media, {
      paused: { configurable: true, get: () => paused },
      duration: { configurable: true, value: 100 },
      play: { configurable: true, value: play },
      pause: { configurable: true, value: pause },
    });
    fireEvent.loadedMetadata(media);
    fireEvent.click(screen.getByRole("button", { name: "播放速度 1×" }));
    fireEvent.click(screen.getByRole("menuitemradio", { name: "1.25×" }));
    expect(media.playbackRate).toBe(1.25);
    vi.useFakeTimers();

    fireEvent.keyDown(document.body, { key: "ArrowRight" });
    act(() => vi.advanceTimersByTime(350));
    expect(media.playbackRate).toBe(2);
    expect(play).toHaveBeenCalledTimes(1);
    expect(screen.getByText("快进 2×")).toBeTruthy();
    fireEvent.keyDown(document.body, { key: "ArrowUp" });
    expect(media.playbackRate).toBe(3);
    expect(screen.getByText("快进 3×")).toBeTruthy();
    fireEvent.keyDown(document.body, { key: "ArrowDown" });
    expect(media.playbackRate).toBe(2);
    fireEvent.keyUp(document.body, { key: "ArrowRight" });
    expect(media.playbackRate).toBe(1.25);
    expect(pause).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("快进 2×")).toBeNull();
  });

  it("continuously rewinds to zero and restores the playing state", async () => {
    render(<VideosView videos={videos} />);
    fireEvent.click(screen.getAllByRole("button", { name: "播放" })[0]);
    const media = (await screen.findByLabelText(
      "播放 第一讲",
    )) as HTMLVideoElement;
    let paused = false;
    const play = vi.fn(async () => {
      paused = false;
    });
    const pause = vi.fn(() => {
      paused = true;
    });
    Object.defineProperties(media, {
      paused: { configurable: true, get: () => paused },
      duration: { configurable: true, value: 100 },
      play: { configurable: true, value: play },
      pause: { configurable: true, value: pause },
    });
    fireEvent.loadedMetadata(media);
    media.currentTime = 0.25;
    vi.useFakeTimers();

    fireEvent.keyDown(document.body, { key: "ArrowLeft" });
    act(() => vi.advanceTimersByTime(350));
    expect(pause).toHaveBeenCalledTimes(1);
    expect(screen.getByText("倒退 2×")).toBeTruthy();
    fireEvent.keyDown(document.body, { key: "ArrowUp" });
    expect(screen.getByText("倒退 3×")).toBeTruthy();
    act(() => vi.advanceTimersByTime(200));
    expect(media.currentTime).toBe(0);
    fireEvent.keyUp(document.body, { key: "ArrowLeft" });
    expect(media.playbackRate).toBe(1);
    expect(play).toHaveBeenCalledTimes(1);
    act(() => vi.advanceTimersByTime(500));
    expect(media.currentTime).toBe(0);
  });

  it("cleans temporary playback on blur, Escape, video switch and unmount", async () => {
    const view = render(
      <VideosView
        videos={videos}
        onPlay={async (video) => ({
          url: video.playbackUrl ?? "https://example.test/" + video.id + ".mp4",
        })}
      />,
    );
    fireEvent.click(screen.getAllByRole("button", { name: "播放" })[0]);
    let media = (await screen.findByLabelText(
      "播放 第一讲",
    )) as HTMLVideoElement;
    Object.defineProperties(media, {
      paused: { configurable: true, value: false },
      duration: { configurable: true, value: 100 },
      play: { configurable: true, value: vi.fn(async () => undefined) },
      pause: { configurable: true, value: vi.fn() },
    });
    fireEvent.loadedMetadata(media);
    vi.useFakeTimers();

    fireEvent.keyDown(document.body, { key: "ArrowRight" });
    act(() => vi.advanceTimersByTime(350));
    expect(media.playbackRate).toBe(2);
    fireEvent(window, new Event("blur"));
    expect(media.playbackRate).toBe(1);

    fireEvent.keyDown(document.body, { key: "ArrowRight" });
    act(() => vi.advanceTimersByTime(350));
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(media.playbackRate).toBe(1);

    fireEvent.keyDown(document.body, { key: "ArrowRight" });
    act(() => vi.advanceTimersByTime(350));
    vi.useRealTimers();
    fireEvent.click(screen.getAllByRole("button", { name: "播放" })[2]);
    expect(media.playbackRate).toBe(1);
    media = (await screen.findByLabelText("播放 第三讲")) as HTMLVideoElement;
    Object.defineProperties(media, {
      paused: { configurable: true, value: false },
      play: { configurable: true, value: vi.fn(async () => undefined) },
      pause: { configurable: true, value: vi.fn() },
    });
    await act(async () => {
      fireEvent.loadedMetadata(media);
    });
    vi.useFakeTimers();
    fireEvent.keyDown(document.body, { key: "ArrowRight" });
    act(() => vi.advanceTimersByTime(350));
    expect(media.playbackRate).toBe(2);
    view.unmount();
    expect(media.playbackRate).toBe(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("keeps the course toolbar after the page header in normal flow", () => {
    const host = document.createElement("div");
    host.innerHTML =
      '<header class="page-header"></header><div class="content"></div>';
    const content = host.querySelector(".content");
    const view = render(<VideosView videos={videos} />, {
      container: content as HTMLElement,
    });
    const pageHeader = host.querySelector(".page-header");
    const toolbar = content?.querySelector(
      '.video-course-toolbar[aria-label="课程与视频来源"]',
    ) as HTMLElement;
    expect(pageHeader?.compareDocumentPosition(toolbar)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
    expect(toolbar.tagName).toBe("SECTION");

    const css = readFileSync("src/index.css", "utf8");
    const toolbarRule = css.match(/\.video-course-toolbar \{([^}]*)\}/)?.[1];
    expect(toolbarRule).toContain("position: static");
    expect(toolbarRule).toContain("margin: 0");
    expect(toolbarRule).toContain("transform: none");
    expect(toolbarRule).not.toMatch(
      /margin[^;]*:-|position:\s*(absolute|fixed)|translate/,
    );
    expect(css).toMatch(
      /\.content:has\(> \.view-transition > \.video-workbench\) \{[^}]*padding-top: 16px/,
    );
    expect(css).toMatch(
      /@media \(max-width: 1120px\) \{[^}]*\.video-course-toolbar \{[^}]*grid-template-columns: minmax\(0, 1fr\)/,
    );
  });

  it("does not capture shortcuts from controls, editable content or modified keys", async () => {
    render(<VideosView videos={videos} />);
    fireEvent.click(screen.getAllByRole("button", { name: "播放" })[0]);
    const media = (await screen.findByLabelText(
      "播放 第一讲",
    )) as HTMLVideoElement;
    Object.defineProperties(media, {
      duration: { configurable: true, value: 100 },
      play: { configurable: true, value: vi.fn(async () => undefined) },
    });
    fireEvent.loadedMetadata(media);
    media.currentTime = 50;

    const checkbox = screen.getByRole("checkbox", { name: "选择 第一讲" });
    checkbox.focus();
    fireEvent.keyDown(checkbox, { key: "j" });
    expect(media.currentTime).toBe(50);

    const speedButton = screen.getByRole("button", { name: "播放速度 1×" });
    speedButton.focus();
    fireEvent.keyDown(speedButton, { key: "l" });
    expect(media.currentTime).toBe(50);

    const editable = document.createElement("div");
    editable.setAttribute("contenteditable", "true");
    document.body.append(editable);
    editable.focus();
    fireEvent.keyDown(editable, { key: "ArrowRight" });
    expect(media.currentTime).toBe(50);
    editable.remove();

    for (const modifier of ["metaKey", "ctrlKey", "altKey"] as const) {
      fireEvent.keyDown(document.body, { key: "l", [modifier]: true });
    }
    expect(media.currentTime).toBe(50);
  });

  it("offers all playback rates, applies selection and keeps it across videos", async () => {
    render(
      <VideosView
        videos={videos}
        onPlay={async (video) => ({
          url: video.playbackUrl ?? `https://example.test/${video.id}.mp4`,
        })}
      />,
    );
    fireEvent.click(screen.getAllByRole("button", { name: "播放" })[0]);
    let media = (await screen.findByLabelText(
      "播放 第一讲",
    )) as HTMLVideoElement;
    fireEvent.loadedMetadata(media);
    fireEvent.click(screen.getByRole("button", { name: "播放速度 1×" }));
    const options = screen.getAllByRole("menuitemradio");
    expect(options.map((option) => option.textContent)).toEqual([
      "0.5×",
      "0.75×",
      "1×",
      "1.25×",
      "1.5×",
      "1.75×",
      "2×",
    ]);
    fireEvent.click(screen.getByRole("menuitemradio", { name: "1.25×" }));
    expect(media.playbackRate).toBe(1.25);
    expect(
      screen.getByText("1.25×", { selector: ".video-player-feedback" }),
    ).toBeTruthy();

    const thirdPlay = screen.getAllByRole("button", {
      name: "播放",
    })[2] as HTMLButtonElement;
    await waitFor(() => expect(thirdPlay.disabled).toBe(false));
    fireEvent.click(thirdPlay);
    media = (await screen.findByLabelText("播放 第三讲")) as HTMLVideoElement;
    fireEvent.loadedMetadata(media);
    expect(media.playbackRate).toBe(1.25);
    expect(screen.getByRole("button", { name: "播放速度 1.25×" })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "快捷键" }));
    const shortcutHelp = screen.getByRole("dialog", {
      name: "播放器快捷键",
    }).textContent;
    expect(shortcutHelp).toContain("短按");
    expect(shortcutHelp).toContain("长按");
    expect(shortcutHelp).toContain("调整临时速度");
    expect(shortcutHelp).toContain("基础倍速");
  });

  it("turns rejected play promises into understandable in-player feedback", async () => {
    render(<VideosView videos={videos} />);
    fireEvent.click(screen.getAllByRole("button", { name: "播放" })[0]);
    const media = (await screen.findByLabelText(
      "播放 第一讲",
    )) as HTMLVideoElement;
    Object.defineProperties(media, {
      paused: { configurable: true, value: true },
      play: {
        configurable: true,
        value: vi.fn(() => Promise.reject(new Error("blocked"))),
      },
    });
    fireEvent.loadedMetadata(media);
    fireEvent.keyDown(document.body, { key: " " });
    expect(
      await screen.findByText("浏览器阻止播放，请点击播放器继续"),
    ).toBeTruthy();
  });

  it("deduplicates and sorts tasks, shows at most three attention items, and folds history", () => {
    const history = Array.from({ length: 12 }, (_, index) => ({
      id: `history-${index}`,
      videoId: `history-video-${index}`,
      title: `历史 ${index}`,
      kind: "video" as const,
      status: "completed" as const,
      progress: 100,
      updatedAt: `2026-09-${String(28 - index).padStart(2, "0")}T10:00:00+08:00`,
    }));
    const taskItems = [
      ...history,
      {
        id: "running-old",
        videoId: "active-video",
        title: "较旧进行中",
        kind: "subtitle" as const,
        status: "running" as const,
        progress: 20,
        updatedAt: "2026-09-28T10:00:00+08:00",
      },
      {
        id: "running-new",
        videoId: "active-video",
        title: "管理会计 · 最新进行中",
        kind: "subtitle" as const,
        status: "running" as const,
        progress: 30,
        updatedAt: "2026-09-30T10:00:00+08:00",
      },
      ...Array.from({ length: 4 }, (_, index) => ({
        id: `failed-${index}`,
        videoId: `failed-video-${index}`,
        title: `需关注 ${index}`,
        kind: "slides_pdf" as const,
        status: "failed" as const,
        progress: 40,
        updatedAt: `2026-09-${String(29 - index).padStart(2, "0")}T10:00:00+08:00`,
      })),
      {
        id: "failed-0",
        videoId: "duplicate-id-video",
        title: "重复 ID 不应显示",
        kind: "video" as const,
        status: "failed" as const,
        progress: 5,
        updatedAt: "2026-09-01T10:00:00+08:00",
      },
    ];
    const { container } = render(
      <VideosView videos={videos} tasks={taskItems} courseName="管理会计" />,
    );
    expect(screen.getByText("最新进行中")).toBeTruthy();
    expect(screen.queryByText("管理会计 · 最新进行中")).toBeNull();
    expect(screen.queryByText("较旧进行中")).toBeNull();
    expect(screen.queryByText("重复 ID 不应显示")).toBeNull();
    expect(screen.getAllByText(/需关注 \d/)).toHaveLength(3);
    expect(screen.queryByText("历史 0")).toBeNull();
    expect(
      container.querySelectorAll('.video-job-list [role="progressbar"]'),
    ).toHaveLength(4);

    const disclosure = screen.getByRole("button", { name: "历史任务 12" });
    expect(disclosure.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(disclosure);
    expect(disclosure.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("历史 0")).toBeTruthy();
    expect(screen.getByText("历史 9")).toBeTruthy();
    expect(screen.queryByText("历史 10")).toBeNull();
    expect(screen.getByText("仅显示最近10条")).toBeTruthy();
    expect(
      container.querySelector(".video-job-history .video-job-progress"),
    ).toBeNull();
    fireEvent.click(disclosure);
    expect(screen.queryByText("历史 0")).toBeNull();
  });

  it("keeps history-only task state to one compact summary row", () => {
    const { container } = render(
      <VideosView
        videos={videos}
        tasks={[
          {
            id: "done",
            videoId: "v1",
            title: "第一讲",
            kind: "video",
            status: "completed",
            progress: 100,
          },
        ]}
      />,
    );
    expect(container.querySelector(".video-jobs.is-empty")).toBeTruthy();
    expect(screen.getByText("暂无进行中的任务")).toBeTruthy();
    expect(screen.getByRole("button", { name: "历史任务 1" })).toBeTruthy();
    expect(container.querySelector(".video-job-progress")).toBeNull();
  });
});
