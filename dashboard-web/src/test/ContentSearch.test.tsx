// @vitest-environment jsdom
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { FilePreviewDialog } from "@/components/FilePreviewDialog";
import { PdfSearchPreview } from "@/components/PdfSearchPreview";
import { RichPreviewContent } from "@/components/RichPreviewContent";
import { VideoTranscript } from "@/components/VideoTranscript";
import { findTextMatches } from "@/lib/contentSearch";

afterEach(cleanup);
it("文档标题区的查找快捷键可用，Esc 先清除搜索再关闭浮层", async () => {
  const close = vi.fn();
  render(
    <FilePreviewDialog
      preview={{ kind: "text", name: "课程.md", text: "优化" }}
      onClose={close}
    />,
  );
  await waitFor(() =>
    expect(document.activeElement).toBe(
      screen.getByRole("button", { name: "关闭文件预览" }),
    ),
  );
  fireEvent.keyDown(document, { key: "f", metaKey: true });
  expect(document.activeElement).toBe(screen.getByRole("searchbox"));
  fireEvent.change(screen.getByRole("searchbox"), {
    target: { value: "优化" },
  });
  await waitFor(() => expect(document.querySelector("mark")).toBeTruthy());
  fireEvent.keyDown(screen.getByRole("searchbox"), { key: "Escape" });
  expect(close).not.toHaveBeenCalled();
  fireEvent.keyDown(screen.getByRole("searchbox"), { key: "Escape" });
  expect(close).toHaveBeenCalledOnce();
});
const search = (label: string, text: string) =>
  fireEvent.change(screen.getByRole("searchbox", { name: label }), {
    target: { value: text },
  });
const hits = () =>
  Array.from(document.querySelectorAll("mark[data-search-hit]"));

it("中文和正则特殊字符按字面匹配，英文忽略大小写且保留原文", () => {
  expect(findTextMatches("A+B a+b [test] 你好你好", "a+b")).toEqual([
    { start: 0, end: 3 },
    { start: 4, end: 7 },
  ]);
  expect(findTextMatches("[test]", "[")).toEqual([{ start: 0, end: 1 }]);
  expect(findTextMatches("你好你好", "你好")).toHaveLength(2);
  expect(findTextMatches("hello", "  ")).toEqual([]);
});

it("Markdown 跨加粗和链接匹配，保留格式，并按一次命中计数", async () => {
  render(
    <RichPreviewContent
      kind="markdown"
      content={"知识**库**\n\n[知识库](https://example.com)"}
    />,
  );
  search("搜索文档", "知识库");
  await waitFor(() => expect(screen.getByText("1 / 2")).toBeTruthy());
  expect(
    hits()
      .map((h) => h.textContent)
      .join(""),
  ).toBe("知识库知识库");
  expect(document.querySelector("strong mark")?.textContent).toBe("库");
  expect(screen.getByRole("link").getAttribute("href")).toBe(
    "https://example.com",
  );
  fireEvent.click(screen.getByRole("button", { name: "下一个匹配" }));
  expect(screen.getByText("2 / 2")).toBeTruthy();
  fireEvent.keyDown(screen.getByRole("searchbox"), {
    key: "Enter",
    shiftKey: true,
  });
  expect(screen.getByText("1 / 2")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "清除搜索" }));
  await waitFor(() => expect(hits()).toHaveLength(0));
});

it("HTML 搜索保留清洗规则，搜索字符串不会生成 HTML", async () => {
  render(
    <RichPreviewContent
      kind="html"
      content={
        '<p onclick="evil()">正文正文 &lt;img&gt;</p><script>正文</script>'
      }
    />,
  );
  search("搜索文档", "正文");
  await waitFor(() => expect(hits()).toHaveLength(2));
  expect(document.querySelector("script")).toBeNull();
  expect(document.querySelector("[onclick]")).toBeNull();
  search("搜索文档", "<img>");
  await waitFor(() => expect(hits()).toHaveLength(1));
  expect(hits()[0].textContent).toBe("<img>");
  expect(document.querySelector("img")).toBeNull();
});

it("无结果、切换文件和更新正文都正确重置或更新，不污染原文", async () => {
  const { rerender } = render(
    <RichPreviewContent kind="text" title="一.txt" content="资料" />,
  );
  search("搜索文档", "资料");
  await waitFor(() => expect(screen.getByText("1 / 1")).toBeTruthy());
  rerender(
    <RichPreviewContent kind="text" title="一.txt" content="资料 资料" />,
  );
  await waitFor(() => expect(screen.getByText("1 / 2")).toBeTruthy());
  search("搜索文档", "没有");
  await waitFor(() => expect(screen.getByText("无匹配")).toBeTruthy());
  rerender(
    <RichPreviewContent kind="text" title="二.txt" content="新的资料" />,
  );
  await waitFor(() =>
    expect((screen.getByRole("searchbox") as HTMLInputElement).value).toBe(""),
  );
  expect(document.querySelector("pre")?.textContent).toBe("新的资料");
});

it("查找快捷键聚焦搜索框，输入法确认不触发结果导航", async () => {
  render(<RichPreviewContent kind="text" content="中文 中文" />);
  fireEvent.keyDown(screen.getByRole("region"), { key: "f", metaKey: true });
  expect(document.activeElement).toBe(screen.getByRole("searchbox"));
  search("搜索文档", "中文");
  await waitFor(() => expect(screen.getByText("1 / 2")).toBeTruthy());
  fireEvent.keyDown(screen.getByRole("searchbox"), {
    key: "Enter",
    isComposing: true,
  });
  expect(screen.getByText("1 / 2")).toBeTruthy();
  fireEvent.keyDown(screen.getByRole("searchbox"), { key: "Escape" });
  await waitFor(() => expect(hits()).toHaveLength(0));
});

it("在线观看字幕过滤、实体解码、高亮与时间定位不受播放更新影响", async () => {
  const seek = vi.fn();
  const cues = [
    { startMs: 1000, endMs: 3000, text: "第一段" },
    { startMs: 4000, endMs: 6000, text: "优化 &amp; 对偶优化" },
  ];
  const { rerender } = render(
    <VideoTranscript
      sourceId="video-1"
      cues={cues}
      playbackTime={2}
      onSeek={seek}
    />,
  );
  search("搜索字幕", "优化");
  await waitFor(() => expect(screen.getByText("1 / 2")).toBeTruthy());
  expect(
    screen.queryByRole("button", { name: "跳转到 00:01：第一段" }),
  ).toBeNull();
  expect(seek).not.toHaveBeenCalled();
  rerender(
    <VideoTranscript
      sourceId="video-1"
      cues={cues}
      playbackTime={4.5}
      onSeek={seek}
    />,
  );
  expect((screen.getByRole("searchbox") as HTMLInputElement).value).toBe(
    "优化",
  );
  fireEvent.click(
    screen.getByRole("button", { name: "跳转到 00:04：优化 & 对偶优化" }),
  );
  expect(seek).toHaveBeenCalledWith(4);
  fireEvent.click(screen.getByRole("button", { name: "清除搜索" }));
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "跳转到 00:01：第一段" }),
    ).toBeTruthy(),
  );
});

it("PDF 搜索结果高亮并定位实际页码，清除后恢复整页预览", async () => {
  render(
    <PdfSearchPreview
      title="课件"
      sourceUrl="blob:document"
      pages={[
        { page: 1, text: "简介" },
        { page: 12, text: "对偶理论" },
      ]}
    />,
  );
  search("搜索文档", "对偶");
  await waitFor(() => expect(screen.getByText("1 / 1")).toBeTruthy());
  expect(hits()[0].textContent).toBe("对偶");
  fireEvent.click(screen.getByRole("button", { name: "定位第 12 页" }));
  expect(screen.getByTitle("课件 PDF 预览").getAttribute("src")).toBe(
    "blob:document#page=12",
  );
  fireEvent.click(screen.getByRole("button", { name: "清除搜索" }));
  await waitFor(() => expect(screen.queryByRole("complementary")).toBeNull());
  expect(screen.getByTitle("课件 PDF 预览")).toBeTruthy();
});
