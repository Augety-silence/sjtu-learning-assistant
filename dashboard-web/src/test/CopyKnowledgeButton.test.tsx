// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { CopyKnowledgeButton } from "@/components/CopyKnowledgeButton";
import { readCompiledKnowledge } from "@/lib/api";
import { copyKnowledgeText } from "@/lib/compiledKnowledgeCopy";
import { cleanup, fireEvent, render, screen } from "@/test/render";

vi.mock("@/lib/api", () => ({ readCompiledKnowledge: vi.fn() }));
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});
describe("完整复制", () => {
  it("同一文件升级讲义后，不复制上一版本的 WebKit 准备缓存", async () => {
    const copied: string[] = [];
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: undefined,
    });
    Object.defineProperty(document, "execCommand", {
      configurable: true,
      value: vi.fn(() => {
        copied.push(document.querySelector("textarea")?.value ?? "");
        return copied.length > 1;
      }),
    });
    vi.mocked(readCompiledKnowledge).mockResolvedValueOnce({
      id: "a",
      text: "旧全文",
      offset: 0,
      next_offset: null,
      total_chars: 3,
      content_sha256: "old",
    } as never);
    const view = render(<CopyKnowledgeButton id="a" version="old" />);
    fireEvent.click(screen.getByRole("button", { name: "复制完整 Markdown" }));
    await screen.findByRole("button", { name: "复制已读取的完整 Markdown" });
    vi.mocked(readCompiledKnowledge).mockResolvedValueOnce({
      id: "a",
      text: "新全文",
      offset: 0,
      next_offset: null,
      total_chars: 3,
      content_sha256: "new",
    } as never);
    view.rerender(<CopyKnowledgeButton id="a" version="new" />);
    fireEvent.click(screen.getByRole("button", { name: "复制完整 Markdown" }));
    await screen.findByText(/已复制完整 Markdown/);
    expect(copied).toEqual(["旧全文", "新全文"]);
    expect(readCompiledKnowledge).toHaveBeenCalledTimes(2);
  });
  it("异步读取失去 WebKit 点击权限时，新点击可复制同一完整版本而不再读取", async () => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: undefined,
    });
    Object.defineProperty(document, "execCommand", {
      configurable: true,
      value: vi.fn().mockReturnValueOnce(false).mockReturnValueOnce(true),
    });
    vi.mocked(readCompiledKnowledge).mockResolvedValue({
      id: "a",
      text: "全文",
      offset: 0,
      next_offset: null,
      total_chars: 2,
      content_sha256: "hash",
    } as never);
    render(<CopyKnowledgeButton id="a" />);
    fireEvent.click(screen.getByRole("button", { name: "复制完整 Markdown" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "复制已读取的完整 Markdown" }),
    );
    await screen.findByText(/已复制完整 Markdown/);
    expect(readCompiledKnowledge).toHaveBeenCalledTimes(1);
  });
  it("复制完整文档并给出反馈", async () => {
    const write = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: write },
    });
    vi.mocked(readCompiledKnowledge).mockResolvedValue({
      id: "a",
      text: "全文",
      offset: 0,
      next_offset: null,
      total_chars: 2,
      content_sha256: "hash",
    } as never);
    render(<CopyKnowledgeButton id="a" />);
    fireEvent.click(screen.getByRole("button", { name: "复制完整 Markdown" }));
    await screen.findByText(/已复制完整 Markdown/);
    expect(write).toHaveBeenCalledWith("全文");
  });
  it("WebKit 回退复制后清理临时控件并恢复焦点", async () => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: undefined,
    });
    Object.defineProperty(document, "execCommand", {
      configurable: true,
      value: vi.fn().mockReturnValue(true),
    });
    const { container } = render(<button type="button">原焦点</button>);
    const button = screen.getByRole("button", { name: "原焦点" });
    button.focus();
    await copyKnowledgeText("完整讲义");
    expect(document.activeElement).toBe(button);
    expect(container.ownerDocument.querySelector("textarea")).toBeNull();
  });
});
