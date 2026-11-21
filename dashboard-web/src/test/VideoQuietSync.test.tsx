// @vitest-environment jsdom
import { StrictMode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useVideoPdfSync } from "@/components/useVideoPdfSync";
import { type CourseVideoItem, VideosView } from "@/components/VideosView";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@/test/render";

const lesson: CourseVideoItem = {
  id: "sjtu-video:12:99",
  title: "第一节",
  courseName: "课程",
  source: "video_space",
  supportsSubtitle: true,
  supportsSlidesPdf: true,
  playable: true,
};
const other = { ...lesson, id: "sjtu-video:12:100", title: "第二节" };
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
const play = async () => ({ url: "https://example.test/video.mp4" });
function Hook({
  video = lesson,
  create,
  retry = 0,
}: {
  video?: CourseVideoItem;
  create: (v: CourseVideoItem) => Promise<{ url: string }>;
  retry?: number;
}) {
  const sync = useVideoPdfSync(video, true, 12, create, retry);
  return (
    <div>
      <span data-testid="pdf">{sync.result?.url}</span>
      <span data-testid="error">{sync.error}</span>
      <span data-testid="busy">{String(sync.busy)}</span>
    </div>
  );
}

describe("quiet classroom sync", () => {
  it("releases a hung PDF UI wait while retaining a single in-flight backend task", async () => {
    vi.useFakeTimers();
    const create = vi.fn(() => new Promise<{ url: string }>(() => {}));
    const view = render(<Hook create={create} />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(120000);
    });
    expect(screen.getByTestId("busy").textContent).toBe("false");
    expect(screen.getByTestId("error").textContent).toContain("超时");
    view.rerender(<Hook create={create} retry={1} />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(create).toHaveBeenCalledOnce();
  });
  it("retries a failed transcript from the single resume button without starting a new batch", async () => {
    const retry = vi.fn(async () => {});
    const start = vi.fn(async () => {});
    render(
      <VideosView
        videos={[lesson]}
        onPlay={play}
        onRetryTranscript={retry}
        onStartTranscript={start}
        transcriptJobs={[
          {
            id: "failed",
            batch_id: "batch",
            source_id: lesson.id,
            title: lesson.title,
            status: "failed",
            stage: "review",
            progress: 20,
            attempts: 1,
          },
        ]}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "播放 第一节" }));
    await waitFor(() =>
      expect(document.querySelector("video source")).toBeTruthy(),
    );
    fireEvent.click(screen.getByRole("button", { name: "重试同步" }));
    await waitFor(() => expect(retry).toHaveBeenCalledOnce());
    expect(start).not.toHaveBeenCalled();
  });
  it("automatically preserves original classroom sync even when AI is unconfigured", async () => {
    const prepare = vi.fn(async () => {});
    render(
      <VideosView
        videos={[lesson]}
        onPlay={play}
        onPrepareVideo={prepare}
        aiReadiness={{
          configured: false,
          status: "blocked",
          message: "未配置",
          model: "",
        }}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "播放 第一节" }));
    await waitFor(() => expect(prepare).toHaveBeenCalledOnce());
    expect(screen.queryByText("未配置")).toBeNull();
  });
  it("keeps the remaining menu keyboard-accessible and independent of playback", () => {
    const download = vi.fn();
    const play = vi.fn();
    render(
      <VideosView videos={[lesson]} onPlay={play} onDownload={download} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "第一节 更多操作" }));
    const menu = screen.getByRole("menu");
    const item = screen.getByRole("menuitem", { name: "下载视频" });
    fireEvent.keyDown(menu, { key: "Home" });
    expect(document.activeElement).toBe(item);
    fireEvent.keyDown(menu, { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
    expect(play).not.toHaveBeenCalled();
  });
  it("removes practice, counters, filters, batch controls and task monitor from the real video view", () => {
    const { container } = render(<VideosView videos={[lesson, other]} />);
    expect(screen.getAllByRole("tab").map((t) => t.textContent)).not.toContain(
      "主动练习",
    );
    expect(
      container.querySelector(
        ".video-status-filters,.video-course-progress,.video-batch-bar,.video-jobs,input[type=checkbox]",
      ),
    ).toBeNull();
    expect(screen.getByRole("button", { name: "重试同步" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "播放 第一节" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "播放 第二节" })).toBeTruthy();
    expect(
      container.querySelector(
        ".video-learning-tabs [data-selection-indicator]",
      ),
    ).toBeNull();
  });
  it("starts subtitles and remote PDF silently after playback, only opens PDF on explicit click", async () => {
    const open = vi.fn();
    const pdf = vi.fn(async () => ({ open }));
    const prepare = vi.fn(async () => {});
    render(
      <VideosView
        videos={[lesson]}
        onPlay={play}
        onPrepareVideo={prepare}
        onCreateSlidesPdf={pdf}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "播放 第一节" }));
    await waitFor(() => expect(pdf).toHaveBeenCalledOnce());
    await waitFor(() => expect(prepare).toHaveBeenCalledOnce());
    expect(open).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.mouseDown(screen.getByRole("tab", { name: "课件" }), {
      button: 0,
    });
    fireEvent.click(
      await screen.findByRole("button", { name: "打开课件 PDF" }),
    );
    await waitFor(() => expect(open).toHaveBeenCalledOnce());
  });
  it("does not fetch PDF when playback failed or auto-convert local videos to screenshot PDFs", async () => {
    const pdf = vi.fn(async () => ({ url: "test" }));
    const view = render(
      <VideosView
        videos={[lesson]}
        onCreateSlidesPdf={pdf}
        onPlay={async () => {
          throw new Error("播放失败");
        }}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "播放 第一节" }));
    await screen.findByText("第一节 无法载入");
    expect(pdf).not.toHaveBeenCalled();
    view.rerender(
      <VideosView
        videos={[{ ...lesson, source: "canvas" }]}
        onPlay={play}
        onCreateSlidesPdf={pdf}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "播放 第一节" }));
    await waitFor(() =>
      expect(document.querySelector("video source")).toBeTruthy(),
    );
    expect(pdf).not.toHaveBeenCalled();
  });
  it("deduplicates strict-mode mount, rerenders and revisiting completed PDFs", async () => {
    const create = vi.fn(async () => ({ url: "first" }));
    const view = render(
      <StrictMode>
        <Hook create={create} />
      </StrictMode>,
    );
    await waitFor(() =>
      expect(screen.getByTestId("pdf").textContent).toBe("first"),
    );
    view.rerender(
      <StrictMode>
        <Hook create={create} />
      </StrictMode>,
    );
    expect(create).toHaveBeenCalledOnce();
    view.rerender(
      <StrictMode>
        <Hook create={create} retry={1} />
      </StrictMode>,
    );
    await waitFor(() => expect(create).toHaveBeenCalledTimes(2));
  });
  it("serializes PDF generation and isolates late results after switching videos", async () => {
    let finish: ((value: { url: string }) => void) | undefined;
    const create = vi
      .fn()
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finish = resolve;
          }),
      )
      .mockResolvedValueOnce({ url: "second" });
    const view = render(<Hook create={create} />);
    await waitFor(() => expect(create).toHaveBeenCalledOnce());
    view.rerender(<Hook video={other} create={create} />);
    expect(screen.getByTestId("pdf").textContent).toBe("");
    expect(create).toHaveBeenCalledOnce();
    await act(async () => finish?.({ url: "first" }));
    await waitFor(() =>
      expect(screen.getByTestId("pdf").textContent).toBe("second"),
    );
    view.rerender(<Hook create={create} />);
    await waitFor(() =>
      expect(screen.getByTestId("pdf").textContent).toBe("first"),
    );
    expect(create).toHaveBeenCalledTimes(2);
  });
  it("uses three bounded automatic attempts then permits explicit resume without a daily cap", async () => {
    vi.useFakeTimers();
    const create = vi.fn().mockRejectedValue(new Error("等待课件"));
    const view = render(<Hook create={create} />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(90000);
    });
    expect(create).toHaveBeenCalledTimes(3);
    view.rerender(<Hook create={create} retry={1} />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(create).toHaveBeenCalledTimes(4);
  });
  it("keeps learning segments readable without progress, review or failure noise", async () => {
    render(
      <VideosView
        videos={[lesson]}
        onPlay={play}
        onLoadLearningSegments={async () => ({
          status: "ready",
          errorMessage: "旧失败",
          completedSegments: 5,
          failedSegments: 1,
          stage: "review",
          segments: [
            {
              id: "one",
              startMs: 0,
              endMs: 10000,
              title: "知识主线",
              content: "机会成本与资源分配",
              provisional: true,
            },
          ],
        })}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "播放 第一节" }));
    expect(await screen.findByText("机会成本与资源分配")).toBeTruthy();
    expect(screen.queryByText(/旧失败|已完成 5 段|深度校对尚未/)).toBeNull();
  });
  it("keeps raw subtitle text selectable and does not replace it with a status message", async () => {
    vi.stubGlobal("URL", {
      createObjectURL: vi.fn(() => "blob:raw"),
      revokeObjectURL: vi.fn(),
    });
    render(
      <VideosView
        videos={[lesson]}
        onPlay={async () => ({
          url: "https://example.test/a.mp4",
          subtitleVtt: "WEBVTT\n\n00:00.000 --> 00:01.000\n课堂原话",
        })}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "播放 第一节" }));
    await waitFor(() => expect(document.querySelector("video")).toBeTruthy());
    fireEvent.mouseDown(screen.getByRole("tab", { name: "字幕" }), {
      button: 0,
    });
    expect(await screen.findByText("课堂原话")).toBeTruthy();
    expect(document.querySelector(".video-raw-subtitle")).toBeTruthy();
  });
});
