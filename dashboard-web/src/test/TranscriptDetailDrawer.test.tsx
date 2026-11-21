// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TranscriptDetailDrawer } from "@/components/TranscriptDetailDrawer";
import {
  getTranscriptArtifacts,
  getTranscriptV2Artifacts,
  readTranscriptArtifact,
  readTranscriptV2Artifact,
} from "@/lib/api";
import type { TranscriptArtifact, TranscriptJob } from "@/lib/types";
import { cleanup, fireEvent, render, screen, waitFor } from "@/test/render";

vi.mock("@/lib/api", () => ({
  getTranscriptArtifacts: vi.fn(),
  getTranscriptV2Artifacts: vi.fn(),
  readTranscriptArtifact: vi.fn(),
  readTranscriptV2Artifact: vi.fn(),
}));
const getV1 = vi.mocked(getTranscriptArtifacts);
const readV1 = vi.mocked(readTranscriptArtifact);
const job: TranscriptJob = {
  id: "a".repeat(32),
  batch_id: "b".repeat(32),
  source_id: "sjtu-video:12:99",
  title: "管理会计 · 第三讲",
  status: "completed",
  stage: "completed",
  progress: 100,
  attempts: 1,
  lesson_notes_status: "completed",
};
const item = (
  kind: TranscriptArtifact["kind"],
  id = job.id,
): TranscriptArtifact => ({
  id: `${id}:${kind}`,
  kind,
  label: kind,
  content_type: "text/markdown",
});
function chooseTab(name: string) {
  fireEvent.mouseDown(screen.getByRole("tab", { name }), {
    button: 0,
    ctrlKey: false,
  });
}
function actions() {
  const details = screen.getByText("更多操作").closest("details");
  if (details) details.open = true;
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((a, b) => {
    resolve = a;
    reject = b;
  });
  return { promise, resolve, reject };
}
beforeEach(() => {
  vi.resetAllMocks();
  getV1.mockResolvedValue({
    items: [
      item("lesson_summary"),
      item("original"),
      item("summary_json"),
      item("cleaned"),
    ],
  });
  readV1.mockImplementation(async (id) => ({
    id,
    kind: id.endsWith(":original") ? "original" : "lesson_summary",
    content: id.endsWith(":original")
      ? "# 原始字幕\n\n    老师的原话。"
      : "# 课堂主线\n\n先讲机会成本，再讲决策条件。",
  }));
});
afterEach(cleanup);

describe("课堂资料详情", () => {
  it("只呈现两种新材料，不调用旧校对接口、不保留旧入口", async () => {
    render(<TranscriptDetailDrawer job={job} onClose={vi.fn()} />);
    await screen.findByText("先讲机会成本，再讲决策条件。");
    expect(readV1).toHaveBeenCalledWith(`${job.id}:lesson_summary`);
    expect(screen.getAllByRole("tab")).toHaveLength(2);
    for (const text of [
      "校对与历史材料",
      "规整字幕",
      "主动练习",
      "AI 校对",
      "质量诊断：AI 校对未通过",
    ])
      expect(screen.queryByText(text)).toBeNull();
    expect(getTranscriptV2Artifacts).not.toHaveBeenCalled();
    expect(readTranscriptV2Artifact).not.toHaveBeenCalled();
    chooseTab("原始字幕");
    await screen.findByText("老师的原话。");
    expect(readV1).toHaveBeenLastCalledWith(`${job.id}:original`);
    expect(
      document.querySelector<HTMLElement>(".transcript-markdown")?.style
        .userSelect,
    ).toBe("text");
  });
  it.each(["practice", "cleaned"] as const)(
    "旧 %s 请求安全映射到精炼资料",
    async (initialTab) => {
      render(
        <TranscriptDetailDrawer
          job={job}
          initialTab={initialTab}
          onClose={vi.fn()}
        />,
      );
      await screen.findByText("课堂主线");
      expect(screen.getAllByRole("tab")).toHaveLength(2);
    },
  );
  it("没有精炼总结也不回退到旧的巨长校对稿", async () => {
    getV1.mockResolvedValue({
      items: [
        item("summary_json"),
        item("summary"),
        item("lesson"),
        item("raw_vtt"),
      ],
    });
    render(
      <TranscriptDetailDrawer
        job={{ ...job, lesson_notes_status: "pending" }}
        onClose={vi.fn()}
      />,
    );
    await screen.findByText(/精炼课堂总结尚未完成/);
    expect(readV1).not.toHaveBeenCalled();
    readV1.mockResolvedValue({
      id: `${job.id}:raw_vtt`,
      kind: "raw_vtt",
      content: "WEBVTT\n完整原话",
    });
    chooseTab("原始字幕");
    await screen.findByText(/WEBVTT/);
  });
  it("旧校对失败不冒充新资料失败，也不显示旧进度诊断", async () => {
    render(
      <TranscriptDetailDrawer
        job={{
          ...job,
          status: "failed",
          stage: "map_reduce",
          progress: 55,
          error: "旧校对报错",
          message: "旧失败",
        }}
        onClose={vi.fn()}
      />,
    );
    await screen.findByText("课堂材料已就绪");
    expect(screen.queryByText("旧校对报错")).toBeNull();
    expect(screen.queryByRole("progressbar")).toBeNull();
  });
  it("任务100%但没有材料，不显示完成", async () => {
    getV1.mockResolvedValue({ items: [] });
    render(
      <TranscriptDetailDrawer
        job={{ ...job, status: "failed" }}
        onClose={vi.fn()}
      />,
    );
    await screen.findByText(/精炼课堂总结尚未完成/);
    expect(screen.getByText("暂无可用材料")).toBeTruthy();
    expect(screen.queryByText("课堂材料已就绪")).toBeNull();
  });
  it("读取失败可重试，不需要重新生成任务", async () => {
    readV1.mockRejectedValueOnce(new Error("正文临时不可读"));
    render(<TranscriptDetailDrawer job={job} onClose={vi.fn()} />);
    await screen.findByText("正文临时不可读");
    fireEvent.click(screen.getByRole("button", { name: "重试加载学习材料" }));
    await screen.findByText("课堂主线");
    expect(readV1).toHaveBeenCalledTimes(2);
  });
  it("列表失败可重试", async () => {
    getV1.mockRejectedValueOnce(new Error("列表暂不可读"));
    render(<TranscriptDetailDrawer job={job} onClose={vi.fn()} />);
    await screen.findByText("列表暂不可读");
    fireEvent.click(screen.getByRole("button", { name: "重试加载学习材料" }));
    await screen.findByText("课堂主线");
  });
  it("切换材料复用缓存", async () => {
    render(<TranscriptDetailDrawer job={job} onClose={vi.fn()} />);
    await screen.findByText("课堂主线");
    chooseTab("原始字幕");
    await screen.findByText("老师的原话。");
    chooseTab("当堂老师讲了什么");
    await screen.findByText("课堂主线");
    expect(readV1).toHaveBeenCalledTimes(2);
  });
  it("同一任务总结完成后重新获取列表和正文", async () => {
    getV1.mockResolvedValueOnce({ items: [item("original")] });
    const view = render(
      <TranscriptDetailDrawer
        job={{ ...job, lesson_notes_status: "pending" }}
        onClose={vi.fn()}
      />,
    );
    await screen.findByText(/精炼课堂总结尚未完成/);
    view.rerender(<TranscriptDetailDrawer job={job} onClose={vi.fn()} />);
    await screen.findByText("课堂主线");
    expect(getV1).toHaveBeenCalledTimes(2);
  });
  it("切换任务隔离延迟正文与缓存", async () => {
    const old = deferred<Awaited<ReturnType<typeof readTranscriptArtifact>>>();
    readV1.mockReturnValueOnce(old.promise);
    const view = render(<TranscriptDetailDrawer job={job} onClose={vi.fn()} />);
    await waitFor(() => expect(readV1).toHaveBeenCalledTimes(1));
    const next = { ...job, id: "c".repeat(32), title: "另一堂课" };
    getV1.mockResolvedValue({ items: [item("lesson_summary", next.id)] });
    readV1.mockResolvedValue({
      id: `${next.id}:lesson_summary`,
      kind: "lesson_summary",
      content: "新课堂的独立主线",
    });
    view.rerender(<TranscriptDetailDrawer job={next} onClose={vi.fn()} />);
    await screen.findByText("新课堂的独立主线");
    old.resolve({
      id: `${job.id}:lesson_summary`,
      kind: "lesson_summary",
      content: "不应该显示的上一堂课",
    });
    await waitFor(() =>
      expect(screen.queryByText("不应该显示的上一堂课")).toBeNull(),
    );
  });
  it("切换任务隔离延迟列表", async () => {
    const old = deferred<Awaited<ReturnType<typeof getTranscriptArtifacts>>>();
    getV1.mockReturnValueOnce(old.promise);
    const view = render(<TranscriptDetailDrawer job={job} onClose={vi.fn()} />);
    const next = { ...job, id: "c".repeat(32) };
    getV1.mockResolvedValue({ items: [item("lesson_summary", next.id)] });
    view.rerender(<TranscriptDetailDrawer job={next} onClose={vi.fn()} />);
    await screen.findByText("课堂主线");
    old.resolve({ items: [item("original")] });
    expect(readV1).not.toHaveBeenCalledWith(`${job.id}:original`);
  });
  it("在文件管理器定位当前材料，失败不关闭正文", async () => {
    const reveal = vi.fn().mockRejectedValue(new Error("定位失败"));
    const close = vi.fn();
    render(
      <TranscriptDetailDrawer job={job} onClose={close} onReveal={reveal} />,
    );
    await screen.findByText("课堂主线");
    actions();
    fireEvent.click(screen.getByRole("button", { name: /在.*中显示/ }));
    await screen.findByText("定位失败");
    expect(reveal).toHaveBeenCalledWith(item("lesson_summary"));
    expect(close).not.toHaveBeenCalled();
    expect(screen.getByText("课堂主线")).toBeTruthy();
  });
  it("任务重试防重复，失败可再次操作", async () => {
    const pending = deferred<void>();
    const retry = vi
      .fn()
      .mockReturnValueOnce(pending.promise)
      .mockResolvedValue(undefined);
    const close = vi.fn();
    getV1.mockResolvedValue({ items: [] });
    render(
      <TranscriptDetailDrawer
        job={{ ...job, status: "failed", lesson_notes_status: "pending" }}
        onClose={close}
        onRetry={retry}
      />,
    );
    await screen.findByText(/精炼课堂总结尚未完成/);
    actions();
    const button = screen.getByRole("button", { name: "重新生成课堂材料" });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(retry).toHaveBeenCalledTimes(1);
    pending.reject(new Error("重试失败"));
    await screen.findByText("重试失败");
    expect(close).not.toHaveBeenCalled();
    fireEvent.click(button);
    await waitFor(() => expect(close).toHaveBeenCalledOnce());
  });
  it("取消失败仍可阅读且保持打开", async () => {
    const cancel = vi.fn().mockRejectedValue(new Error("取消失败"));
    const close = vi.fn();
    render(
      <TranscriptDetailDrawer
        job={{
          ...job,
          status: "organizing",
          progress: 55,
          lesson_notes_status: "pending",
        }}
        onClose={close}
        onCancel={cancel}
      />,
    );
    await screen.findByText("课堂主线");
    actions();
    fireEvent.click(screen.getByRole("button", { name: "取消任务" }));
    await screen.findByText("取消失败");
    expect(close).not.toHaveBeenCalled();
  });
  it("稀疏畸形列表安全为空态", async () => {
    getV1.mockResolvedValue({
      items: [
        null,
        {},
        { kind: "original" },
      ] as unknown as TranscriptArtifact[],
    });
    render(<TranscriptDetailDrawer job={job} onClose={vi.fn()} />);
    await screen.findByText(/精炼课堂总结尚未完成/);
    expect(readV1).not.toHaveBeenCalled();
  });
  it("关闭按钮和Escape保留模态交互", async () => {
    const close = vi.fn();
    render(<TranscriptDetailDrawer job={job} onClose={close} />);
    await screen.findByText("课堂主线");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(close).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole("button", { name: "关闭字幕详情" }));
    expect(close).toHaveBeenCalledTimes(2);
  });
});
