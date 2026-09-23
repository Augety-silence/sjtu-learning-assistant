// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  MessageDetailContent,
  sanitizeMessageHtml,
} from "@/components/MessageDetailContent";
import {
  getMessageResource,
  openExternal,
  openMailAttachment,
  revealMailAttachment,
} from "@/lib/api";
import type { MessageDetail } from "@/lib/types";
import { cleanup, fireEvent, render, screen, waitFor } from "@/test/render";

vi.mock("@/lib/api", () => ({
  getMessageResource: vi.fn(),
  openExternal: vi.fn(),
  openMailAttachment: vi.fn(),
  revealMailAttachment: vi.fn(),
}));

const makeDetail = (overrides: Partial<MessageDetail> = {}): MessageDetail => ({
  title: "详情",
  source_label: "课程",
  occurred_at: null,
  body: "纯文本正文",
  body_html: null,
  format: "text",
  pending_body_sync: false,
  attachments: [],
  resources: [],
  url: null,
  is_unread: false,
  ...overrides,
});

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(openExternal).mockResolvedValue({ status: "opened" });
  vi.mocked(openMailAttachment).mockResolvedValue({
    source_id: "mail-1",
    attachment_id: "attachment-1",
    status: "opened",
  });
  vi.mocked(revealMailAttachment).mockResolvedValue({
    source_id: "mail-1",
    attachment_id: "attachment-1",
    status: "revealed",
  });
});

afterEach(cleanup);

describe("MessageDetailContent", () => {
  it("优先显示清洗后的 HTML，并过滤脚本、事件、样式、危险链接和图片来源属性", () => {
    const html = `
      <div style="color:red" onclick="alert(1)">
        <p>安全<strong>加粗</strong></p>
        <script>alert(1)</script><style>body{display:none}</style>
        <a href="javascript:alert(1)" target="_blank">危险链接</a>
        <a href="https://example.edu/path" style="color:red">安全链接</a>
        <img data-resource-id="image-1" alt="课程图片" src="https://evil.test/a.png"
          srcset="https://evil.test/b.png" style="width:999px" onerror="alert(1)">
      </div>`;

    render(
      <MessageDetailContent
        detail={makeDetail({ body_html: html, format: "html" })}
        kind="announcement"
        sourceId="announcement-1"
      />,
    );

    expect(screen.getByRole("document").textContent).toContain("安全加粗");
    expect(document.querySelector("script")).toBeNull();
    expect(document.querySelector("style")).toBeNull();
    const dangerous = screen.getByText("危险链接");
    const safe = screen.getByText("安全链接");
    expect(dangerous.getAttribute("href")).toBeNull();
    expect(safe.getAttribute("href")).toBe("https://example.edu/path");
    expect(safe.getAttribute("target")).toBeNull();
    expect(safe.getAttribute("style")).toBeNull();
    const image = screen.getByAltText("课程图片");
    expect(image.getAttribute("data-resource-id")).toBe("image-1");
    expect(image.getAttribute("src")).toBeNull();
    expect(image.getAttribute("srcset")).toBeNull();
    expect(image.getAttribute("style")).toBeNull();
    expect(image.getAttribute("onerror")).toBeNull();
  });

  it("没有 HTML 时显示纯文本，pending 时仅显示同步提示", () => {
    const { rerender } = render(
      <MessageDetailContent
        detail={makeDetail({ body: "纯文本 <b>不会渲染标签</b>" })}
        kind="email"
        sourceId="mail-1"
      />,
    );
    expect(screen.getByRole("document").textContent).toBe(
      "纯文本 <b>不会渲染标签</b>",
    );
    expect(document.querySelector("b")).toBeNull();

    rerender(
      <MessageDetailContent
        detail={makeDetail({
          body_html: "<p>不应显示</p>",
          pending_body_sync: true,
        })}
        kind="email"
        sourceId="mail-1"
      />,
    );
    expect(screen.getByText("正文尚未同步，请点击同步后再试")).toBeTruthy();
    expect(screen.queryByRole("document")).toBeNull();
  });

  it("拦截正文 HTTPS 链接并交给 openExternal，危险链接不可导航", async () => {
    render(
      <MessageDetailContent
        detail={makeDetail({
          body_html:
            '<a href="https://example.edu/page">外部页面</a><a href="javascript:alert(1)">危险页面</a>',
          format: "html",
        })}
        kind="announcement"
        sourceId="announcement-1"
      />,
    );

    expect(fireEvent.click(screen.getByText("外部页面"))).toBe(false);
    await waitFor(() =>
      expect(openExternal).toHaveBeenCalledWith("https://example.edu/page"),
    );
    vi.mocked(openExternal).mockClear();
    expect(fireEvent.click(screen.getByText("危险页面"))).toBe(false);
    expect(openExternal).not.toHaveBeenCalled();
  });

  it("按 resource-id 定向替换远程和邮件内联图片，单张失败不影响正文", async () => {
    vi.mocked(getMessageResource).mockImplementation(
      async (_kind, _sourceId, resourceId) => {
        if (resourceId === "failed") throw new Error("读取失败");
        return { data_url: "data:image/png;base64,remote" };
      },
    );
    render(
      <MessageDetailContent
        detail={makeDetail({
          body_html: `
            <p>正文仍显示</p>
            <img data-resource-id="remote" alt="远程图">
            <img data-resource-id="inline" alt="内联图">
            <img data-resource-id="failed">`,
          format: "html",
          resources: [
            { id: "remote", type: "image" },
            { id: "failed", type: "image" },
          ],
          attachments: [
            {
              id: "inline",
              name: "inline.png",
              type: "image/png",
              size: 10,
              is_inline: true,
              available: true,
              inline_data_url: "data:image/png;base64,inline",
            },
          ],
        })}
        kind="email"
        sourceId="mail-1"
      />,
    );

    await waitFor(() =>
      expect(screen.getByAltText("远程图").getAttribute("src")).toBe(
        "data:image/png;base64,remote",
      ),
    );
    expect(screen.getByAltText("内联图").getAttribute("src")).toBe(
      "data:image/png;base64,inline",
    );
    await waitFor(() =>
      expect(
        screen
          .getByAltText("图片加载失败")
          .classList.contains("message-resource-failed"),
      ).toBe(true),
    );
    expect(screen.getByText("正文仍显示")).toBeTruthy();
    expect(getMessageResource).toHaveBeenCalledWith(
      "email",
      "mail-1",
      "remote",
    );
    expect(getMessageResource).toHaveBeenCalledWith(
      "email",
      "mail-1",
      "failed",
    );
    expect(getMessageResource).not.toHaveBeenCalledWith(
      "email",
      "mail-1",
      "inline",
    );
  });

  it("已加载正文图片支持键盘和鼠标放大，并可关闭预览", async () => {
    vi.mocked(getMessageResource).mockResolvedValue({
      data_url: "data:image/png;base64,preview",
    });
    render(
      <MessageDetailContent
        detail={makeDetail({
          body_html: '<img data-resource-id="preview" alt="课程图片">',
          format: "html",
          resources: [{ id: "preview", type: "image" }],
        })}
        kind="announcement"
        sourceId="announcement-1"
      />,
    );

    const image = await screen.findByRole("button", {
      name: "放大查看：课程图片",
    });
    expect(image.getAttribute("tabindex")).toBe("0");

    fireEvent.keyDown(image, { key: "Enter" });
    let preview = screen.getByRole("dialog", { name: "正文图片预览" });
    expect(preview.querySelector("img")?.getAttribute("src")).toBe(
      "data:image/png;base64,preview",
    );
    fireEvent.click(screen.getByRole("button", { name: "关闭" }));
    expect(screen.queryByRole("dialog", { name: "正文图片预览" })).toBeNull();

    fireEvent.click(image);
    preview = screen.getByRole("dialog", { name: "正文图片预览" });
    expect(preview).toBeTruthy();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "正文图片预览" })).toBeNull();
  });

  it("图片加载失败后可重试并重新请求资源", async () => {
    vi.mocked(getMessageResource)
      .mockRejectedValueOnce(new Error("暂时失败"))
      .mockResolvedValueOnce({ data_url: "data:image/png;base64,retried" });

    render(
      <MessageDetailContent
        detail={makeDetail({
          body_html: '<img data-resource-id="retry-image">',
          format: "html",
          resources: [{ id: "retry-image", type: "image" }],
        })}
        kind="announcement"
        sourceId="announcement-1"
      />,
    );

    const retry = await screen.findByRole("button", { name: "重试" });
    fireEvent.click(retry);

    await waitFor(() =>
      expect(document.querySelector("img")?.getAttribute("src")).toBe(
        "data:image/png;base64,retried",
      ),
    );
    expect(getMessageResource).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("button", { name: "重试" })).toBeNull();
  });

  it("显示邮件附件信息并执行打开、Finder 操作，未缓存附件禁用操作", async () => {
    render(
      <MessageDetailContent
        detail={makeDetail({
          attachments: [
            {
              id: "attachment-1",
              name: "讲义.pdf",
              type: "application/pdf",
              size: 2048,
              is_inline: false,
              available: true,
              inline_data_url: null,
            },
            {
              id: "attachment-2",
              name: "大文件.zip",
              type: "application/zip",
              size: 4096,
              is_inline: false,
              available: false,
              inline_data_url: null,
            },
          ],
        })}
        kind="email"
        sourceId="mail-1"
      />,
    );

    expect(screen.getByText("讲义.pdf")).toBeTruthy();
    expect(screen.getByText("application/pdf · 2.0 KB")).toBeTruthy();
    expect(screen.getByText("附件过大或未缓存")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "打开" }));
    await waitFor(() =>
      expect(openMailAttachment).toHaveBeenCalledWith("mail-1", "attachment-1"),
    );
    fireEvent.click(screen.getByRole("button", { name: "在 Finder 显示" }));
    await waitFor(() =>
      expect(revealMailAttachment).toHaveBeenCalledWith(
        "mail-1",
        "attachment-1",
      ),
    );
  });

  it("Canvas 附件仅为 HTTPS URL 提供浏览器打开操作", async () => {
    render(
      <MessageDetailContent
        detail={makeDetail({
          attachments: [
            {
              name: "课程资料.pdf",
              size: null,
              url: "https://oc.sjtu.edu.cn/files/1",
            },
            {
              name: "不安全附件",
              size: 1,
              url: "http://example.edu/file",
            },
          ],
        })}
        kind="assignment"
        sourceId="assignment-1"
      />,
    );

    expect(
      screen.getAllByRole("button", { name: "在浏览器打开" }),
    ).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "在浏览器打开" }));
    await waitFor(() =>
      expect(openExternal).toHaveBeenCalledWith(
        "https://oc.sjtu.edu.cn/files/1",
      ),
    );
  });
});

describe("sanitizeMessageHtml", () => {
  it("仅保留白名单标签", () => {
    const sanitized = sanitizeMessageHtml(
      "<section><p>段落</p><svg><circle /></svg><blockquote>引用</blockquote></section>",
    );
    expect(sanitized).toContain("<p>段落</p>");
    expect(sanitized).toContain("<blockquote>引用</blockquote>");
    expect(sanitized).not.toContain("section");
    expect(sanitized).not.toContain("svg");
    expect(sanitized).not.toContain("circle");
  });
});
