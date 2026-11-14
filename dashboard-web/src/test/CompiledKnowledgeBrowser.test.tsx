// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CompiledKnowledgeBrowser } from "@/components/CompiledKnowledgeBrowser";
import {
  getKnowledgeMCPConfig,
  listCompiledKnowledgeSources,
  openCompiledKnowledge,
  readCompiledKnowledge,
  searchCompiledKnowledge,
} from "@/lib/api";
import type { CompiledKnowledgePage } from "@/lib/types";
import { cleanup, fireEvent, render, screen, waitFor } from "@/test/render";

vi.mock("@/lib/api", () => ({
  getKnowledgeMCPConfig: vi.fn(),
  listCompiledKnowledgeSources: vi.fn(),
  openCompiledKnowledge: vi.fn(),
  readCompiledKnowledge: vi.fn(),
  searchCompiledKnowledge: vi.fn(),
}));

const source = {
  id: "automatic-courses",
  name: "自动课件 Markdown",
  kind: "automatic" as const,
  root: "/archive/Knowledge Markdown/Courses",
  count: 87,
};
const document = {
  id: "a".repeat(32),
  source_id: source.id,
  source_name: source.name,
  title: "第一讲.pptx.md",
  relative_path: "数据库/第一讲.pptx.md",
  status: "pending" as const,
  match_offset: 0,
  snippet: "",
};
const page: CompiledKnowledgePage = {
  ...document,
  text: "# 数据库\n\n关系代数",
  citation: "数据库/第一讲.pptx.md",
  section: "第 1 页",
  offset: 0,
  next_offset: 8000,
  total_chars: 12000,
  truncated: true,
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(listCompiledKnowledgeSources).mockResolvedValue({
    items: [source],
    count: 87,
    issues: [],
    truncated: false,
  });
  vi.mocked(searchCompiledKnowledge).mockResolvedValue({
    items: [document],
    count: 87,
    next_offset: 20,
    issues: [],
    truncated: false,
  });
  vi.mocked(readCompiledKnowledge).mockResolvedValue(page);
  vi.mocked(openCompiledKnowledge).mockResolvedValue({ status: "opened" });
  vi.mocked(getKnowledgeMCPConfig).mockResolvedValue({
    command: "/Applications/App/Contents/MacOS/App",
    args: ["--mcp-stdio"],
  });
});
afterEach(cleanup);

describe("CompiledKnowledgeBrowser", () => {
  it("无需选目录就显示列表和正文，保留视觉未完成状态", async () => {
    render(<CompiledKnowledgeBrowser />);
    expect(await screen.findByText("关系代数")).toBeTruthy();
    expect(screen.getByRole("button", { name: /第一讲.pptx.md/ })).toBeTruthy();
    expect(screen.getAllByText(/全文就绪 · 视觉待编译/).length).toBeGreaterThan(
      0,
    );
    fireEvent.click(screen.getByRole("button", { name: "打开结果目录" }));
    await waitFor(() =>
      expect(openCompiledKnowledge).toHaveBeenCalledWith(source.id, undefined),
    );
  });
  it("检索正文并按返回的字符位置继续读取", async () => {
    render(<CompiledKnowledgeBrowser />);
    await screen.findByText("关系代数");
    fireEvent.change(screen.getByRole("searchbox"), {
      target: { value: "关系代数" },
    });
    fireEvent.click(screen.getByRole("button", { name: "搜索正文" }));
    await waitFor(() =>
      expect(searchCompiledKnowledge).toHaveBeenCalledWith("关系代数", "", 0),
    );
    await screen.findByText("关系代数");
    fireEvent.click(screen.getByRole("button", { name: "继续阅读" }));
    await waitFor(() =>
      expect(readCompiledKnowledge).toHaveBeenCalledWith(document.id, 8000),
    );
  });
  it("后续结果使用已提交关键词，不使用未搜索的新输入", async () => {
    render(<CompiledKnowledgeBrowser />);
    await screen.findByText("关系代数");
    fireEvent.change(screen.getByRole("searchbox"), {
      target: { value: "未提交词" },
    });
    fireEvent.click(screen.getByRole("button", { name: "下一组结果" }));
    await waitFor(() =>
      expect(searchCompiledKnowledge).toHaveBeenCalledWith("", "", 20),
    );
  });
  it("预览不加载知识文本中的外部图片、链接和脚本", async () => {
    vi.mocked(readCompiledKnowledge).mockResolvedValue({
      ...page,
      text: "![image](https://example.invalid/private)\n[link](https://example.invalid/private)\n<script>alert(1)</script>",
    });
    const { container } = render(<CompiledKnowledgeBrowser />);
    await screen.findByText(/请在结果目录查看/);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("a")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
  });
  it("空结果与错误可刷新恢复", async () => {
    vi.mocked(listCompiledKnowledgeSources).mockRejectedValueOnce(
      new Error("索引离线"),
    );
    render(<CompiledKnowledgeBrowser />);
    expect(await screen.findByRole("alert")).toHaveProperty(
      "textContent",
      "索引离线",
    );
    vi.mocked(searchCompiledKnowledge).mockResolvedValue({
      items: [],
      count: 0,
      next_offset: null,
      issues: [],
      truncated: false,
    });
    fireEvent.click(screen.getByRole("button", { name: "刷新结果" }));
    expect(await screen.findByText(/此来源暂无可读取/)).toBeTruthy();
  });
  it("提供真实安装路径的 MCP 配置而不修改 AI 客户端", async () => {
    render(<CompiledKnowledgeBrowser />);
    fireEvent.click(screen.getByRole("button", { name: "显示 AI 连接配置" }));
    const config = await screen.findByRole("textbox", {
      name: "AI 工具连接配置",
    });
    expect((config as HTMLTextAreaElement).value).toContain("--mcp-stdio");
    expect((config as HTMLTextAreaElement).value).toContain(
      "/Applications/App",
    );
  });
  it("旧正文请求不能覆盖新文档", async () => {
    let resolveOld: (value: CompiledKnowledgePage) => void = () => {};
    vi.mocked(readCompiledKnowledge).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveOld = resolve;
        }),
    );
    const second = { ...document, id: "b".repeat(32), title: "第二讲.md" };
    vi.mocked(searchCompiledKnowledge).mockResolvedValue({
      items: [document, second],
      count: 2,
      next_offset: null,
      issues: [],
      truncated: false,
    });
    render(<CompiledKnowledgeBrowser />);
    fireEvent.click(await screen.findByRole("button", { name: /第二讲.md/ }));
    await screen.findByText("关系代数");
    resolveOld({ ...page, text: "过时正文" });
    await waitFor(() => expect(screen.queryByText("过时正文")).toBeNull());
  });
});
