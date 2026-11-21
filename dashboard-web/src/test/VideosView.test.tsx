// @vitest-environment jsdom

import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";
import { type CourseVideoItem, VideosView } from "@/components/VideosView";
import type { TranscriptJob } from "@/lib/types";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@/test/render";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const queryPlayer = (title: string) =>
  document.querySelector<HTMLVideoElement>(`video[aria-label="播放 ${title}"]`);

const getPlayer = (title: string) => {
  const player = queryPlayer(title);
  if (!player) throw new Error(`未找到 ${title} 播放器`);
  return player;
};

const findPlayer = (title: string) => waitFor(() => getPlayer(title));

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
  it("在 loading 首帧保留课程、深色播放器与录像列表骨架", () => {
    const { container } = render(<VideosView videos={[]} loading />);

    expect(screen.getByLabelText("正在确认课程")).toBeTruthy();
    expect(screen.getByText("正在准备视频工作台")).toBeTruthy();
    expect(screen.getByLabelText("正在加载录像列表")).toBeTruthy();
    expect(container.querySelector(".video-player-shell")).toBeTruthy();
    expect(
      container.querySelectorAll(".video-recording-skeleton"),
    ).toHaveLength(4);
    expect(screen.queryByText("需要视频访问权限")).toBeNull();
  });

  it("数据到达后原位填充主要工作台容器", () => {
    const view = render(<VideosView videos={[]} loading />);
    const workbench = view.container.querySelector(".video-workbench");
    const player = view.container.querySelector(".video-player-shell");
    const list = view.container.querySelector(".video-list-panel");

    view.rerender(
      <VideosView videos={videos} courseId={1} courseName="数据结构" />,
    );

    expect(view.container.querySelector(".video-workbench")).toBe(workbench);
    expect(view.container.querySelector(".video-player-shell")).toBe(player);
    expect(view.container.querySelector(".video-list-panel")).toBe(list);
    expect(screen.getByText("第一讲")).toBeTruthy();
  });

  it.each([
    {
      props: { error: "网络连接失败" },
      state: "error",
      copy: "录像列表加载失败",
      action: "重新加载",
    },
    {
      props: {},
      state: "empty",
      copy: "这门课程还没有录像",
      action: "重新加载",
    },
    {
      props: { permissionDenied: true },
      state: "permission-denied",
      copy: "当前账号无权查看课程录像",
      action: "重新检查登录",
    },
  ])(
    "$state 状态仍保留播放器框架与可行动文案",
    ({ props, state, copy, action }) => {
      const retry = vi.fn();
      const { container } = render(
        <VideosView videos={[]} onRetry={retry} {...props} />,
      );

      expect(
        container.querySelector(`.video-workbench[data-state="${state}"]`),
      ).toBeTruthy();
      expect(container.querySelector(".video-player-shell")).toBeTruthy();
      expect(container.querySelector(".video-list-panel")).toBeTruthy();
      expect(screen.getByText(copy)).toBeTruthy();
      fireEvent.click(screen.getByRole("button", { name: action }));
      expect(retry).toHaveBeenCalledOnce();
    },
  );

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
    expect(screen.getByRole("listbox").parentElement).toBe(document.body);
    const option = screen.getByRole("option", {
      name: "文本分析与大语言模型（长课程名称）",
    });
    fireEvent.click(option);
    expect(change).toHaveBeenCalledWith(2);
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
  });

  it("closes the course picker when its workspace scrolls or the window resizes", () => {
    render(
      <div className="workspace">
        <VideosView
          videos={videos}
          courseId={1}
          courseOptions={[
            { id: 1, name: "数据结构" },
            { id: 2, name: "文本分析与大语言模型" },
          ]}
        />
      </div>,
    );

    const trigger = screen.getByRole("combobox", { name: "选择课程" });
    fireEvent.click(trigger);
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    fireEvent.scroll(document.querySelector(".workspace") as HTMLElement);
    expect(trigger.getAttribute("aria-expanded")).toBe("false");

    fireEvent.click(trigger);
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    fireEvent.resize(window);
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

  it("整行入口支持鼠标与键盘播放且使用准确名称", async () => {
    const play = vi.fn();
    render(<VideosView videos={[videos[0]]} onPlay={play} />);

    const rowPlay = screen.getByRole("button", { name: "播放 第一讲" });
    expect(rowPlay.classList.contains("video-recording-play-target")).toBe(
      true,
    );

    fireEvent.click(rowPlay);
    await waitFor(() => expect(play).toHaveBeenCalledTimes(1));

    rowPlay.focus();
    fireEvent.keyDown(rowPlay, { key: "Enter" });
    await waitFor(() => expect(play).toHaveBeenCalledTimes(2));

    fireEvent.keyDown(rowPlay, { key: " " });
    await waitFor(() => expect(play).toHaveBeenCalledTimes(3));
  });

  it("播放加载期间禁用入口、公开忙碌状态并阻止重复播放", async () => {
    let resolvePlay: (() => void) | undefined;
    const play = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          resolvePlay = resolve;
        }),
    );
    render(<VideosView videos={[videos[0]]} onPlay={play} />);

    fireEvent.click(screen.getByRole("button", { name: "播放 第一讲" }));

    const loading = await screen.findByRole("button", {
      name: "正在加载 第一讲",
    });
    expect((loading as HTMLButtonElement).disabled).toBe(true);
    expect(loading.getAttribute("aria-busy")).toBe("true");
    expect(screen.getByRole("status", { name: "正在加载" })).toBeTruthy();

    fireEvent.click(loading);
    fireEvent.keyDown(loading, { key: "Enter" });
    expect(play).toHaveBeenCalledTimes(1);

    await act(async () => resolvePlay?.());
    await waitFor(() =>
      expect(
        screen
          .getByRole("button", { name: "播放 第一讲" })
          .hasAttribute("aria-busy"),
      ).toBe(false),
    );
  });

  it("不可播放行使用准确的禁用名称", () => {
    render(<VideosView videos={[videos[1]]} />);

    const unavailable = screen.getByRole("button", {
      name: "不可播放 旧版录像",
    });
    expect((unavailable as HTMLButtonElement).disabled).toBe(true);
    expect(unavailable.hasAttribute("aria-busy")).toBe(false);
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
    fireEvent.click(screen.getByRole("button", { name: "播放 第一讲" }));
    expect(await findPlayer("第一讲")).toBeTruthy();
    expect(
      screen
        .getAllByText("第一讲")
        .find((node) => node.closest("li"))
        ?.closest("li")
        ?.getAttribute("aria-current"),
    ).toBe("true");
  });

  it("已有 transcript job 时同时保留详情与载入播放器字幕", async () => {
    const loadSubtitles = vi.fn(async () => ({
      subtitleUrl: "https://example.test/subtitle.vtt",
    }));
    render(
      <VideosView
        videos={[videos[2]]}
        transcriptJobs={[
          {
            id: "production-job",
            batch_id: "batch",
            source_id: "v3",
            title: "第三讲",
            status: "completed",
            stage: "completed",
            progress: 100,
            attempts: 1,
            phase1_status: "completed",
          },
        ]}
        onStartTranscript={vi.fn(async () => undefined)}
        onRetryTranscript={vi.fn(async () => undefined)}
        onRevealTranscript={vi.fn(async () => undefined)}
        onLoadSubtitles={loadSubtitles}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "第三讲 更多操作" }));
    expect(screen.getByRole("menuitem", { name: "查看字幕详情" })).toBeTruthy();
    const load = screen.getByRole("menuitem", { name: "载入播放器字幕" });
    expect(load).toBeTruthy();
    fireEvent.click(load);
    await waitFor(() => expect(loadSubtitles).toHaveBeenCalledWith(videos[2]));
  });

  it("切换来源时清理未播放的跨来源选择、地址与字幕", async () => {
    render(
      <VideosView
        videos={videos}
        onPlay={async () => ({
          url: "https://example.test/canvas.mp4",
          subtitleUrl: "https://example.test/canvas.vtt",
        })}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "播放 第一讲" }));
    const media = (await findPlayer("第一讲")) as HTMLVideoElement;
    Object.defineProperty(media, "paused", { configurable: true, value: true });

    fireEvent.mouseDown(screen.getByRole("tab", { name: "视频空间" }), {
      button: 0,
      ctrlKey: false,
    });

    expect(queryPlayer("第一讲")).toBeNull();
    expect(document.querySelector("video track")).toBeNull();
    expect(screen.getByText("尚未选择播放内容")).toBeTruthy();
  });

  it("来源切换会清除失效播放地址请求的 busy 状态", async () => {
    let resolvePlayback: ((value: { url: string }) => void) | undefined;
    const onPlay = vi.fn(
      () =>
        new Promise<{ url: string }>((resolve) => {
          resolvePlayback = resolve;
        }),
    );
    render(<VideosView videos={videos} onPlay={onPlay} />);

    fireEvent.click(screen.getByRole("button", { name: "播放 第一讲" }));
    fireEvent.mouseDown(screen.getByRole("tab", { name: "视频空间" }), {
      button: 0,
      ctrlKey: false,
    });

    const remainingPlay = screen.getByRole("button", { name: "播放 第三讲" });
    expect((remainingPlay as HTMLButtonElement).disabled).toBe(false);
    await act(async () =>
      resolvePlayback?.({ url: "https://example.test/stale.mp4" }),
    );
    expect(queryPlayer("第一讲")).toBeNull();
  });

  it("来源切换会清除失效字幕请求的 busy 状态并忽略迟到结果", async () => {
    let resolveSubtitle: ((value: { subtitleUrl: string }) => void) | undefined;
    const onLoadSubtitles = vi.fn(
      () =>
        new Promise<{ subtitleUrl: string }>((resolve) => {
          resolveSubtitle = resolve;
        }),
    );
    render(
      <VideosView
        videos={videos}
        onPlay={async () => ({ url: "https://example.test/third.mp4" })}
        onLoadSubtitles={onLoadSubtitles}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "播放 第三讲" }));
    await waitFor(() => expect(onLoadSubtitles).toHaveBeenCalledOnce());
    fireEvent.mouseDown(screen.getByRole("tab", { name: "Canvas" }), {
      button: 0,
      ctrlKey: false,
    });

    const remainingPlay = screen.getByRole("button", { name: "播放 第一讲" });
    expect((remainingPlay as HTMLButtonElement).disabled).toBe(false);
    await act(async () =>
      resolveSubtitle?.({ subtitleUrl: "https://example.test/stale.vtt" }),
    );
    expect(document.querySelector("video track")).toBeNull();
  });

  it("跨来源切换时保留正在播放的视频并显示原来源", async () => {
    render(
      <VideosView
        videos={videos}
        onPlay={async () => ({ url: "https://example.test/canvas.mp4" })}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "播放 第一讲" }));
    const media = (await findPlayer("第一讲")) as HTMLVideoElement;
    Object.defineProperty(media, "paused", {
      configurable: true,
      value: false,
    });

    fireEvent.mouseDown(screen.getByRole("tab", { name: "视频空间" }), {
      button: 0,
      ctrlKey: false,
    });

    expect(getPlayer("第一讲")).toBe(media);
    expect(screen.getByText(/正在播放 · Canvas/)).toBeTruthy();
    expect(screen.getByText("第三讲")).toBeTruthy();

    fireEvent.pause(media);
    expect(screen.queryByText(/正在播放 · Canvas/)).toBeNull();
    fireEvent.play(media);
    expect(screen.getByText(/正在播放 · Canvas/)).toBeTruthy();
    fireEvent.ended(media);
    expect(screen.queryByText(/正在播放 · Canvas/)).toBeNull();
  });

  it.each([
    { reduceMotion: false, behavior: "smooth" },
    { reduceMotion: true, behavior: "auto" },
  ] as const)(
    "窄窗录像列表入口转移焦点并在 reduced-motion=$reduceMotion 时使用 $behavior 滚动",
    ({ reduceMotion, behavior }) => {
      const scrollIntoView = vi.fn();
      Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
        configurable: true,
        value: scrollIntoView,
      });
      vi.stubGlobal(
        "matchMedia",
        vi.fn(() => ({ matches: reduceMotion })),
      );
      render(<VideosView videos={videos} />);

      const jump = screen.getByRole("button", { name: "查看录像列表" });
      fireEvent.click(jump);
      const list = screen.getByLabelText("课程录像");
      expect(document.activeElement).toBe(list);
      expect(list.getAttribute("tabindex")).toBe("-1");
      expect(scrollIntoView).toHaveBeenCalledWith({
        behavior,
        block: "start",
      });
    },
  );

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
    fireEvent.click(screen.getByRole("button", { name: "播放 第一讲" }));
    await waitFor(() => expect(createObjectURL).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "播放 第三讲" }));
    await waitFor(() => expect(createObjectURL).toHaveBeenCalledTimes(2));
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:subtitle-1");
    view.unmount();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:subtitle-2");
  });

  it("supports every learning shortcut, case-insensitive keys, clamp and transient feedback", async () => {
    render(<VideosView videos={videos} />);
    fireEvent.click(screen.getByRole("button", { name: "播放 第一讲" }));
    const media = (await findPlayer("第一讲")) as HTMLVideoElement;
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

  it("treats a sub-threshold press as one short seek and ignores key repeat", async () => {
    render(<VideosView videos={videos} />);
    fireEvent.click(screen.getByRole("button", { name: "播放 第一讲" }));
    const media = (await findPlayer("第一讲")) as HTMLVideoElement;
    Object.defineProperty(media, "duration", {
      configurable: true,
      value: 100,
    });
    fireEvent.loadedMetadata(media);
    media.currentTime = 50;
    vi.useFakeTimers();

    fireEvent.keyDown(document.body, { key: "ArrowRight" });
    fireEvent.keyDown(document.body, { key: "ArrowRight", repeat: true });
    await act(async () => vi.advanceTimersByTimeAsync(300));
    expect(media.currentTime).toBe(50);
    fireEvent.keyUp(document.body, { key: "ArrowRight" });
    expect(media.currentTime).toBe(55);
    await act(async () => vi.advanceTimersByTimeAsync(500));
    expect(media.currentTime).toBe(55);
  });

  it("temporarily fast-forwards, adjusts speed and restores the base paused state", async () => {
    render(<VideosView videos={videos} />);
    fireEvent.click(screen.getByRole("button", { name: "播放 第一讲" }));
    const media = (await findPlayer("第一讲")) as HTMLVideoElement;
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
    media.pause();
    play.mockClear();
    pause.mockClear();
    fireEvent.click(screen.getByRole("button", { name: "播放速度 1×" }));
    fireEvent.click(screen.getByRole("menuitemradio", { name: "1.25×" }));
    expect(media.playbackRate).toBe(1.25);
    vi.useFakeTimers();

    fireEvent.keyDown(document.body, { key: "ArrowRight" });
    await act(async () => vi.advanceTimersByTimeAsync(350));
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
    fireEvent.click(screen.getByRole("button", { name: "播放 第一讲" }));
    const media = (await findPlayer("第一讲")) as HTMLVideoElement;
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
    play.mockClear();
    media.currentTime = 0.25;
    vi.useFakeTimers();

    fireEvent.keyDown(document.body, { key: "ArrowLeft" });
    await act(async () => vi.advanceTimersByTimeAsync(350));
    expect(pause).toHaveBeenCalledTimes(1);
    expect(screen.getByText("倒退 2×")).toBeTruthy();
    fireEvent.keyDown(document.body, { key: "ArrowUp" });
    expect(screen.getByText("倒退 3×")).toBeTruthy();
    await act(async () => vi.advanceTimersByTimeAsync(200));
    expect(media.currentTime).toBe(0);
    fireEvent.keyUp(document.body, { key: "ArrowLeft" });
    expect(media.playbackRate).toBe(1);
    expect(play).toHaveBeenCalledTimes(1);
    await act(async () => vi.advanceTimersByTimeAsync(500));
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
    fireEvent.click(screen.getByRole("button", { name: "播放 第一讲" }));
    let media = (await findPlayer("第一讲")) as HTMLVideoElement;
    Object.defineProperties(media, {
      paused: { configurable: true, value: false },
      duration: { configurable: true, value: 100 },
      play: { configurable: true, value: vi.fn(async () => undefined) },
      pause: { configurable: true, value: vi.fn() },
    });
    fireEvent.loadedMetadata(media);
    vi.useFakeTimers();

    fireEvent.keyDown(document.body, { key: "ArrowRight" });
    await act(async () => vi.advanceTimersByTimeAsync(350));
    expect(media.playbackRate).toBe(2);
    fireEvent(window, new Event("blur"));
    expect(media.playbackRate).toBe(1);

    fireEvent.keyDown(document.body, { key: "ArrowRight" });
    await act(async () => vi.advanceTimersByTimeAsync(350));
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(media.playbackRate).toBe(1);

    fireEvent.keyDown(document.body, { key: "ArrowRight" });
    await act(async () => vi.advanceTimersByTimeAsync(350));
    vi.useRealTimers();
    fireEvent.click(screen.getByRole("button", { name: "播放 第三讲" }));
    expect(media.playbackRate).toBe(1);
    media = (await findPlayer("第三讲")) as HTMLVideoElement;
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
    await act(async () => vi.advanceTimersByTimeAsync(350));
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
    expect(toolbarRule).toContain("grid-template-columns: minmax(0, 1fr) auto");
    expect(
      within(toolbar)
        .getByRole("tablist")
        .querySelector("[data-selection-indicator]"),
    ).toBeNull();
  });

  it("does not capture shortcuts from controls, editable content or modified keys", async () => {
    render(<VideosView videos={videos} />);
    fireEvent.click(screen.getByRole("button", { name: "播放 第一讲" }));
    const media = (await findPlayer("第一讲")) as HTMLVideoElement;
    Object.defineProperties(media, {
      duration: { configurable: true, value: 100 },
      play: { configurable: true, value: vi.fn(async () => undefined) },
    });
    fireEvent.loadedMetadata(media);
    media.currentTime = 50;

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
    fireEvent.click(screen.getByRole("button", { name: "播放 第一讲" }));
    let media = (await findPlayer("第一讲")) as HTMLVideoElement;
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

    const thirdPlay = screen.getByRole("button", {
      name: "播放 第三讲",
    }) as HTMLButtonElement;
    await waitFor(() => expect(thirdPlay.disabled).toBe(false));
    fireEvent.click(thirdPlay);
    media = (await findPlayer("第三讲")) as HTMLVideoElement;
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
    fireEvent.click(screen.getByRole("button", { name: "播放 第一讲" }));
    const media = (await findPlayer("第一讲")) as HTMLVideoElement;
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

  it("查看学习材料直接切换选中录像，无需请求播放地址", async () => {
    const playMedia = vi
      .spyOn(HTMLMediaElement.prototype, "play")
      .mockResolvedValue(undefined);
    vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
    const learningVideos = [
      videos[0],
      { ...videos[2], playbackUrl: "https://example.test/third.mp4" },
    ];
    const completed = learningVideos.map((video, index) => ({
      id: `complete-${index}`,
      batch_id: "batch",
      source_id: video.id,
      title: video.title,
      status: "completed" as const,
      stage: "completed",
      progress: 100,
      attempts: 1,
      phase1_status: "completed" as const,
    }));
    render(
      <VideosView
        videos={learningVideos}
        transcriptJobs={completed}
        onPlay={async (video) => ({ url: video.playbackUrl })}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "播放 第一讲" }));
    const firstMedia = (await findPlayer("第一讲")) as HTMLVideoElement;
    fireEvent.loadedMetadata(firstMedia);
    await waitFor(() => expect(playMedia).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "第三讲 更多操作" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "查看学习材料" }));
    expect(queryPlayer("第一讲")).toBeNull();
    expect(queryPlayer("第三讲")).toBeNull();
    expect(screen.getByText("已选择当前录像")).toBeTruthy();
    expect(playMedia).toHaveBeenCalledTimes(1);
  });

  it("查看另一节材料清除旧画面且不受该节播放地址失败影响", async () => {
    const completed = [videos[0], videos[2]].map((video, index) => ({
      id: `done-${index}`,
      batch_id: "batch",
      source_id: video.id,
      title: video.title,
      status: "completed" as const,
      stage: "completed",
      progress: 100,
      attempts: 1,
    }));
    render(
      <VideosView
        videos={[videos[0], videos[2]]}
        transcriptJobs={completed}
        onPlay={async (video) => {
          if (video.id === "v3") throw new Error("地址已失效");
          return { url: video.playbackUrl };
        }}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "播放 第一讲" }));
    await findPlayer("第一讲");
    fireEvent.click(screen.getByRole("button", { name: "第三讲 更多操作" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "查看学习材料" }));

    expect(screen.getByText("已选择当前录像")).toBeTruthy();
    expect(screen.queryByText("第三讲 无法载入")).toBeNull();
    expect(queryPlayer("第一讲")).toBeNull();
  });
});

describe("VideosView 课程隔离", () => {
  it("切换 courseId 时清空选择、播放器和字幕状态，同时保留工作台骨架", async () => {
    const view = render(
      <VideosView
        videos={videos}
        courseId={12}
        onPlay={async () => ({
          url: "https://example.test/a.mp4",
          subtitleUrl: "https://example.test/a.vtt",
        })}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "播放 第一讲" }));
    expect(await findPlayer("第一讲")).toBeTruthy();
    expect(document.querySelector("video track")?.getAttribute("src")).toBe(
      "https://example.test/a.vtt",
    );
    expect(screen.queryByLabelText("批量操作")).toBeNull();

    view.rerender(<VideosView videos={[]} courseId={13} loading />);

    expect(queryPlayer("第一讲")).toBeNull();
    expect(screen.queryByLabelText("批量操作")).toBeNull();
    expect(screen.queryByText("第一讲")).toBeNull();
    expect(document.querySelector("video track")).toBeNull();
    expect(view.container.querySelector(".video-player-shell")).toBeTruthy();
    expect(screen.getByLabelText("正在加载录像列表")).toBeTruthy();
  });
});

describe("VideosView 全屏与 PPT 同步分屏", () => {
  const remoteVideo: CourseVideoItem = {
    ...videos[0],
    id: "sjtu-video:12:99",
    source: "video_space",
  };
  const slideMetadata = {
    status: "ready",
    message: "课件已就绪。",
    pages: [
      { index: 2, timeMs: 0 },
      { index: 0, timeMs: 1500 },
      { index: 1, timeMs: 2700 },
    ],
  };

  it("去除已配置 AI 常驻提示且保留视频操作", () => {
    const { container } = render(
      <VideosView
        videos={[remoteVideo]}
        aiReadiness={{
          configured: true,
          status: "ready",
          message: "配置已保存，连接尚未验证。",
          model: "model",
          connectionVerified: false,
        }}
        onOpenAISettings={vi.fn()}
      />,
    );
    expect(container.querySelector(".video-ai-readiness")).toBeNull();
    expect(screen.queryByText(/配置已保存，连接尚未验证/)).toBeNull();
    expect(screen.queryByRole("button", { name: "查看 AI 设置" })).toBeNull();
    expect(screen.getByRole("button", { name: "播放 第一讲" })).toBeTruthy();
  });

  it("按非整数播放时间同步课件与标准 WEBVTT 字幕，回退时间时也切回", async () => {
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: vi.fn(() => "blob:synchronized-subtitles"),
      revokeObjectURL: vi.fn(),
    });
    const loadImage = vi.fn(
      async (_video, index: number) => `data:image/png;base64,page${index}`,
    );
    const loadSlides = vi.fn(async () => slideMetadata);
    render(
      <VideosView
        videos={[remoteVideo]}
        onLoadSlides={loadSlides}
        onLoadSlideImage={loadImage}
        onLoadSubtitles={async () => ({
          subtitleVtt:
            "WEBVTT\n\n00:00:01.200 --> 00:00:02.800\n当前讲解内容\n",
        })}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "播放 第一讲" }));
    const player = await findPlayer("第一讲");
    fireEvent.click(screen.getByRole("button", { name: "打开 PPT 分屏" }));
    expect(await screen.findByAltText("第一讲，课件第 3 页")).toBeTruthy();
    expect(loadSlides).toHaveBeenCalledWith(remoteVideo);
    expect(loadImage).toHaveBeenCalledWith(remoteVideo, 2);
    expect(loadImage).toHaveBeenCalledWith(remoteVideo, 0);

    player.currentTime = 1.49;
    fireEvent.timeUpdate(player);
    expect(loadImage).toHaveBeenCalledTimes(2);
    player.currentTime = 1.5;
    fireEvent.timeUpdate(player);
    expect(await screen.findByAltText("第一讲，课件第 1 页")).toBeTruthy();
    expect(
      screen.getByRole("dialog", { name: "PPT 同步分屏" }).textContent,
    ).toContain("当前讲解内容");

    player.currentTime = 2.7;
    fireEvent.seeked(player);
    expect(await screen.findByAltText("第一讲，课件第 2 页")).toBeTruthy();
    player.currentTime = 1.3;
    fireEvent.seeked(player);
    expect(await screen.findByAltText("第一讲，课件第 3 页")).toBeTruthy();
    player.currentTime = 2.9;
    fireEvent.timeUpdate(player);
    expect(await screen.findByAltText("第一讲，课件第 2 页")).toBeTruthy();
    expect(
      screen.getByRole("dialog", { name: "PPT 同步分屏" }).textContent,
    ).toContain("当前时间暂无字幕");
    expect(loadImage).toHaveBeenCalledTimes(3);
  });

  it("拖动与缩放浮窗，并在放开指针后停止调整", async () => {
    vi.stubGlobal("PointerEvent", MouseEvent);
    render(
      <VideosView
        videos={[remoteVideo]}
        onLoadSlides={async () => slideMetadata}
        onLoadSlideImage={async () => "data:image/png;base64,page"}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "播放 第一讲" }));
    await findPlayer("第一讲");
    fireEvent.click(screen.getByRole("button", { name: "打开 PPT 分屏" }));
    const pane = await screen.findByRole("dialog", { name: "PPT 同步分屏" });
    const left = Number.parseFloat(pane.style.left);
    const top = Number.parseFloat(pane.style.top);
    fireEvent.pointerDown(
      screen.getByRole("button", { name: "拖动 PPT 分屏" }),
      {
        button: 0,
        clientX: 100,
        clientY: 100,
      },
    );
    fireEvent.pointerMove(window, { clientX: 60, clientY: 70 });
    expect(Number.parseFloat(pane.style.left)).toBe(left - 40);
    expect(Number.parseFloat(pane.style.top)).toBe(top - 30);
    fireEvent.pointerUp(window);
    fireEvent.pointerMove(window, { clientX: 0, clientY: 0 });
    expect(Number.parseFloat(pane.style.left)).toBe(left - 40);

    const width = Number.parseFloat(pane.style.width);
    const height = Number.parseFloat(pane.style.height);
    fireEvent.pointerDown(
      screen.getByRole("separator", { name: "调整 PPT 分屏大小" }),
      {
        button: 0,
        clientX: 100,
        clientY: 100,
      },
    );
    fireEvent.pointerMove(window, { clientX: 150, clientY: 140 });
    expect(Number.parseFloat(pane.style.width)).toBe(width + 50);
    expect(Number.parseFloat(pane.style.height)).toBe(height + 40);
    fireEvent.pointerUp(window);
    fireEvent.keyDown(
      screen.getByRole("separator", { name: "调整 PPT 分屏大小" }),
      {
        key: "ArrowLeft",
      },
    );
    expect(Number.parseFloat(pane.style.width)).toBe(width + 34);
    fireEvent.click(
      screen.getAllByRole("button", { name: "关闭 PPT 分屏" })[0],
    );
    expect(screen.queryByRole("dialog", { name: "PPT 同步分屏" })).toBeNull();
  });

  it("全屏容器同时包含视频和 PPT 浮窗，并提供退出入口", async () => {
    render(
      <VideosView
        videos={[remoteVideo]}
        onLoadSlides={async () => slideMetadata}
        onLoadSlideImage={async () => "data:image/png;base64,page"}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "播放 第一讲" }));
    const player = await findPlayer("第一讲");
    const shell = player.closest(".video-player-shell") as HTMLDivElement;
    const originalFullscreen = Object.getOwnPropertyDescriptor(
      document,
      "fullscreenElement",
    );
    const originalExit = Object.getOwnPropertyDescriptor(
      document,
      "exitFullscreen",
    );
    const request = vi.fn(async () => {
      Object.defineProperty(document, "fullscreenElement", {
        value: shell,
        configurable: true,
      });
      fireEvent(document, new Event("fullscreenchange"));
    });
    const exit = vi.fn(async () => {
      Object.defineProperty(document, "fullscreenElement", {
        value: null,
        configurable: true,
      });
      fireEvent(document, new Event("fullscreenchange"));
    });
    Object.defineProperty(shell, "requestFullscreen", { value: request });
    Object.defineProperty(document, "exitFullscreen", {
      value: exit,
      configurable: true,
    });
    try {
      fireEvent.click(screen.getByRole("button", { name: "打开 PPT 分屏" }));
      await screen.findByAltText("第一讲，课件第 3 页");
      fireEvent.click(screen.getByRole("button", { name: "全屏播放" }));
      await waitFor(() => expect(request).toHaveBeenCalledOnce());
      expect(
        shell.contains(screen.getByRole("dialog", { name: "PPT 同步分屏" })),
      ).toBe(true);
      fireEvent.click(screen.getAllByRole("button", { name: "退出全屏" })[0]);
      await waitFor(() => expect(exit).toHaveBeenCalledOnce());
      expect(screen.getByRole("button", { name: "全屏播放" })).toBeTruthy();
    } finally {
      if (originalFullscreen)
        Object.defineProperty(
          document,
          "fullscreenElement",
          originalFullscreen,
        );
      else Reflect.deleteProperty(document, "fullscreenElement");
      if (originalExit)
        Object.defineProperty(document, "exitFullscreen", originalExit);
      else Reflect.deleteProperty(document, "exitFullscreen");
    }
  });

  it("桌面 Portal 全屏不受旧 video WebKit 事件影响，并可退出恢复", async () => {
    vi.stubGlobal("pywebview", { api: { invoke: vi.fn() } });
    const { container } = render(<VideosView videos={[remoteVideo]} />);
    fireEvent.click(screen.getByRole("button", { name: "播放 第一讲" }));
    const oldVideo = await findPlayer("第一讲");
    const shell = oldVideo.closest(".video-player-shell") as HTMLDivElement;

    fireEvent.click(screen.getByRole("button", { name: "全屏播放" }));
    await waitFor(() => {
      const fullscreenShell = document.body.querySelector(
        ".video-player-shell",
      );
      expect(fullscreenShell?.parentElement).toBe(document.body);
      expect(
        fullscreenShell?.classList.contains(
          "video-player-shell--pseudo-fullscreen",
        ),
      ).toBe(true);
    });
    const fullscreenShell = document.body.querySelector(
      ".video-player-shell",
    ) as HTMLDivElement;

    fireEvent(oldVideo, new Event("webkitendfullscreen"));
    expect(fullscreenShell?.parentElement).toBe(document.body);
    expect(
      fullscreenShell?.classList.contains(
        "video-player-shell--pseudo-fullscreen",
      ),
    ).toBe(true);

    fireEvent.click(screen.getAllByRole("button", { name: "退出全屏" })[0]);
    await waitFor(() => {
      const restoredShell = container.querySelector(".video-player-shell");
      expect(restoredShell?.parentElement).not.toBe(document.body);
      expect(
        restoredShell?.classList.contains(
          "video-player-shell--pseudo-fullscreen",
        ),
      ).toBe(false);
      expect(screen.getByRole("button", { name: "全屏播放" })).toBeTruthy();
    });
    const restoredShell = container.querySelector(
      ".video-player-shell",
    ) as HTMLDivElement;
    expect(
      container.querySelector(".video-player-column")?.contains(restoredShell),
    ).toBe(true);
  });

  it("无页码时间时明确说明不能同步，并避免随意读取课件页", async () => {
    const image = vi.fn(async () => "data:image/png;base64,page");
    render(
      <VideosView
        videos={[remoteVideo]}
        onLoadSlides={async () => ({
          ...slideMetadata,
          pages: [{ index: 0, timeMs: null }],
        })}
        onLoadSlideImage={image}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "播放 第一讲" }));
    await findPlayer("第一讲");
    fireEvent.click(screen.getByRole("button", { name: "打开 PPT 分屏" }));
    expect(
      await screen.findByText(
        "课件已就绪，但服务未提供页码时间，无法自动同步。",
      ),
    ).toBeTruthy();
    expect(image).not.toHaveBeenCalled();
  });

  it("切换课程时关闭分屏并忽略旧课件请求的迟到结果", async () => {
    let finish: ((result: typeof slideMetadata) => void) | undefined;
    const image = vi.fn(async () => "data:image/png;base64,stale");
    const callbacks = {
      onLoadSlides: () =>
        new Promise<typeof slideMetadata>((resolve) => {
          finish = resolve;
        }),
      onLoadSlideImage: image,
    };
    const view = render(
      <VideosView videos={[remoteVideo]} courseId={12} {...callbacks} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "播放 第一讲" }));
    await findPlayer("第一讲");
    fireEvent.click(screen.getByRole("button", { name: "打开 PPT 分屏" }));
    expect(screen.getByRole("dialog", { name: "PPT 同步分屏" })).toBeTruthy();
    view.rerender(<VideosView videos={[]} courseId={13} {...callbacks} />);
    expect(screen.queryByRole("dialog", { name: "PPT 同步分屏" })).toBeNull();
    await act(async () => {
      finish?.(slideMetadata);
    });
    expect(screen.queryByRole("dialog", { name: "PPT 同步分屏" })).toBeNull();
    expect(image).not.toHaveBeenCalled();
  });
});
