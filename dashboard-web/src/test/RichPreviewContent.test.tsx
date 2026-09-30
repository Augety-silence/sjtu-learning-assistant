// @vitest-environment jsdom

import { afterEach, describe, expect, it } from "vitest";
import {
  RichPreviewContent,
  sanitizePreviewHtml,
} from "@/components/RichPreviewContent";
import { cleanup, render, screen } from "@/test/render";

afterEach(cleanup);

describe("RichPreviewContent", () => {
  it("renders accessible markdown including tables", () => {
    render(
      <RichPreviewContent
        kind="markdown"
        title="报告"
        content={"# 标题\n\n| 项目 | 分数 |\n| --- | --- |\n| A | 90 |"}
      />,
    );
    expect(screen.getByRole("heading", { name: "标题" })).toBeTruthy();
    expect(screen.getByRole("region", { name: "表格内容" })).toBeTruthy();
  });

  it("removes active HTML content and unsafe links", () => {
    const safe = sanitizePreviewHtml(
      '<p onclick="alert(1)">正文</p><script>alert(2)</script><a href="javascript:alert(3)">链接</a>',
    );
    expect(safe).not.toContain("onclick");
    expect(safe).not.toContain("script");
    expect(safe).not.toContain("javascript:");
    render(<RichPreviewContent kind="html" content={safe} />);
    expect(screen.getByText("正文")).toBeTruthy();
  });

  it("exposes unsupported and empty states", () => {
    const { rerender } = render(
      <RichPreviewContent kind="unsupported" content="压缩包不能预览" />,
    );
    expect(screen.getByText("暂不支持此格式的内嵌预览")).toBeTruthy();
    rerender(<RichPreviewContent kind="text" content="" />);
    expect(screen.getByText("暂无可预览内容")).toBeTruthy();
  });
});
