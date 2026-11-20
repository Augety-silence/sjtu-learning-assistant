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
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@/test/render";

vi.mock("@/lib/api", () => ({
  invoke: vi.fn().mockResolvedValue({
    terms: [],
    count: 0,
    complete: false,
    refreshed_at: null,
  }),
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
  localStorage.clear();
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
  it("每份文件独立恢复阅读位置，新文件从头开始，版本变化也从头开始", async () => {
    const second = { ...document, id: "b".repeat(32), title: "第二讲.pptx.md" };
    vi.mocked(searchCompiledKnowledge).mockResolvedValue({
      items: [document, second],
      count: 2,
      next_offset: null,
      issues: [],
      truncated: false,
    });
    let version = "c".repeat(64);
    vi.mocked(readCompiledKnowledge).mockImplementation(async (id) => ({
      ...page,
      id,
      text: `正文${id}`,
      next_offset: null,
      content_sha256: version,
    }));
    const { unmount } = render(<CompiledKnowledgeBrowser />);
    await screen.findByText(`正文${document.id}`);
    const reader = screen.getByLabelText("Markdown 正文预览");
    reader.scrollTop = 800;
    // 包括程序性位置改变没有派发 scroll 的情形：切换前必须主动保存。
    fireEvent.click(screen.getByRole("button", { name: /第二讲/ }));
    await screen.findByText(`正文${second.id}`);
    expect(reader.scrollTop).toBe(0);
    reader.scrollTop = 200;
    fireEvent.scroll(reader);
    fireEvent.click(screen.getByRole("button", { name: /第一讲/ }));
    await screen.findByText(`正文${document.id}`);
    expect(reader.scrollTop).toBe(800);
    unmount();
    render(<CompiledKnowledgeBrowser />);
    await screen.findByText(`正文${document.id}`);
    expect(screen.getByLabelText("Markdown 正文预览").scrollTop).toBe(800);
    fireEvent.click(screen.getByRole("button", { name: "从头阅读" }));
    await waitFor(() =>
      expect(screen.getByLabelText("Markdown 正文预览").scrollTop).toBe(0),
    );
    const active = screen.getByLabelText("Markdown 正文预览");
    active.scrollTop = 900;
    fireEvent.scroll(active);
    version = "d".repeat(64);
    fireEvent.click(screen.getByRole("button", { name: /第一讲/ }));
    await waitFor(() => expect(active.scrollTop).toBe(0));
  });

  it("可收起目录扩大正文，切换不销毁正文", async () => {
    render(<CompiledKnowledgeBrowser />);
    await screen.findByText("关系代数");
    const reader = screen.getByLabelText("Markdown 正文预览");
    fireEvent.click(screen.getByRole("button", { name: "收起课程目录" }));
    expect(screen.getByLabelText("Markdown 正文预览")).toBe(reader);
    expect(screen.getByRole("button", { name: "展开课程目录" })).toBeTruthy();
  });

  it("超长讲义续读遇到版本变化保留旧正文，不混入新版本片段", async () => {
    vi.mocked(readCompiledKnowledge).mockResolvedValueOnce({
      ...page,
      text: "# 原版本\n原来的正文",
      content_sha256: "a".repeat(64),
      total_chars: 300_000,
      next_offset: 12,
    });
    vi.mocked(readCompiledKnowledge).mockResolvedValueOnce({
      ...page,
      text: "不应显示的新版本尾段",
      content_sha256: "b".repeat(64),
      offset: 12,
      total_chars: 300_000,
      next_offset: null,
    });
    render(<CompiledKnowledgeBrowser />);
    await screen.findByText("原来的正文");
    fireEvent.click(screen.getByRole("button", { name: "继续阅读" }));
    expect(await screen.findByRole("alert")).toHaveProperty(
      "textContent",
      expect.stringContaining("版本或分页已变化"),
    );
    expect(screen.getByText("原来的正文")).toBeTruthy();
    expect(screen.queryByText("不应显示的新版本尾段")).toBeNull();
  });
  it.each([
    ["course-lecture-v2", "旧版知识稿"],
    ["course-study-v3", "完整课程讲义"],
    ["course-summary-v1", "精炼课程总结"],
  ])("区分 %s 而不把旧机械稿标为新讲义", async (contract, label) => {
    vi.mocked(readCompiledKnowledge).mockResolvedValue({
      ...page,
      status: "completed",
      contract,
    });
    render(<CompiledKnowledgeBrowser />);
    expect(await screen.findByText(new RegExp(label))).toBeTruthy();
  });
  it("延迟切换和失败不卸载目录、焦点及旧正文", async () => {
    vi.mocked(searchCompiledKnowledge).mockResolvedValue({
      items: [document],
      count: 1,
      next_offset: null,
      issues: [],
      truncated: false,
      folders: [
        { source_id: source.id, path: "数据库", name: "数据库", count: 1 },
      ],
    });
    render(<CompiledKnowledgeBrowser />);
    const folder = await screen.findByRole("button", { name: /数据库.*1/ });
    fireEvent.click(folder);
    await screen.findByText("关系代数");
    let reject: (reason: Error) => void = () => {};
    vi.mocked(searchCompiledKnowledge).mockImplementationOnce(
      () =>
        new Promise((_resolve, fail) => {
          reject = fail;
        }),
    );
    folder.focus();
    fireEvent.click(folder);
    expect(screen.getByRole("button", { name: /数据库.*1/ })).toBe(folder);
    expect(folder.ownerDocument.activeElement).toBe(folder);
    expect(screen.getByText("关系代数")).toBeTruthy();
    await act(async () => reject(new Error("请求失败")));
    expect(await screen.findByRole("alert")).toHaveProperty(
      "textContent",
      "请求失败",
    );
    expect(screen.getByText("关系代数")).toBeTruthy();
  });
  it("真实版本分页会自动读完，不把预览截断当作编译完成", async () => {
    const hash = "c".repeat(64);
    vi.mocked(readCompiledKnowledge).mockResolvedValueOnce({
      ...page,
      text: "# 完整讲义\n",
      content_sha256: hash,
      total_chars: 14,
      next_offset: 7,
    });
    vi.mocked(readCompiledKnowledge).mockResolvedValueOnce({
      ...page,
      text: "\n最后的知识点",
      content_sha256: hash,
      offset: 7,
      total_chars: 14,
      next_offset: null,
    });
    render(<CompiledKnowledgeBrowser />);
    expect(await screen.findByText("最后的知识点")).toBeTruthy();
    expect(screen.getByText(/全文已显示/)).toBeTruthy();
  });
  it("公式能显示，但不能借数学命令加载外部资源", async () => {
    vi.mocked(readCompiledKnowledge).mockResolvedValue({
      ...page,
      next_offset: null,
      text: "$x^2 + y^2$\n\n$\\\\href{https://example.invalid}{unsafe}$",
    });
    const { container } = render(<CompiledKnowledgeBrowser />);
    await waitFor(() => expect(container.querySelector(".katex")).toBeTruthy());
    expect(container.querySelector("a[href^='https:']")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
  });
  it("无需选目录就显示列表和正文，保留视觉未完成状态", async () => {
    render(<CompiledKnowledgeBrowser />);
    expect(await screen.findByText("关系代数")).toBeTruthy();
    expect(screen.getByRole("button", { name: /第一讲/ })).toBeTruthy();
    expect(
      screen.getAllByText(/原文已提取 · 知识稿待续编/).length,
    ).toBeGreaterThan(0);
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(screen.queryByRole("button", { name: "打开结果目录" })).toBeNull();
    expect(
      screen.queryByRole("heading", { name: "课程讲义与知识点" }),
    ).toBeNull();
    fireEvent.click(
      screen.getByRole("button", { name: "定位 Markdown 文件及原图" }),
    );
    await waitFor(() =>
      expect(openCompiledKnowledge).toHaveBeenCalledWith(
        source.id,
        document.id,
      ),
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
    fireEvent.click(await screen.findByRole("button", { name: /第二讲/ }));
    await screen.findByText("关系代数");
    resolveOld({ ...page, text: "过时正文" });
    await waitFor(() => expect(screen.queryByText("过时正文")).toBeNull());
  });
  it("按课程课时文件夹筛选，目录来自全部结果而非当前分页", async () => {
    vi.mocked(searchCompiledKnowledge).mockResolvedValue({
      items: [document],
      count: 87,
      next_offset: 20,
      issues: [],
      truncated: false,
      folders: [
        { source_id: source.id, path: "数据库", name: "数据库", count: 87 },
        {
          source_id: source.id,
          path: "数据库/第一讲",
          name: "第一讲",
          count: 3,
        },
      ],
    });
    render(<CompiledKnowledgeBrowser />);
    await screen.findByText("选择文件夹或搜索知识点。");
    expect(screen.queryByText("查看此文件夹全部课件")).toBeNull();
    const summary = screen.getByText("数据库").closest("summary");
    expect(summary).toBeTruthy();
    fireEvent.click(summary as HTMLElement);
    await waitFor(() =>
      expect(searchCompiledKnowledge).toHaveBeenCalledWith(
        "",
        source.id,
        0,
        "数据库",
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: /第一讲.*3/ }));
    await waitFor(() =>
      expect(searchCompiledKnowledge).toHaveBeenCalledWith(
        "",
        source.id,
        0,
        "数据库/第一讲",
      ),
    );
  });
  it("返回全部课程清除文件夹来源，恢复全来源浏览", async () => {
    vi.mocked(searchCompiledKnowledge).mockResolvedValue({
      items: [document],
      count: 1,
      next_offset: null,
      issues: [],
      truncated: false,
      folders: [
        { source_id: source.id, path: "数据库", name: "数据库", count: 1 },
      ],
    });
    render(<CompiledKnowledgeBrowser />);
    fireEvent.click(await screen.findByRole("button", { name: /数据库.*1/ }));
    await waitFor(() =>
      expect(searchCompiledKnowledge).toHaveBeenCalledWith(
        "",
        source.id,
        0,
        "数据库",
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "全部课程" }));
    await waitFor(() =>
      expect(searchCompiledKnowledge).toHaveBeenLastCalledWith("", "", 0),
    );
  });
  it("资料项目首选来源仍固定，返回根目录不会混入其他项目", async () => {
    render(<CompiledKnowledgeBrowser preferredSource={source.id} />);
    await waitFor(() =>
      expect(searchCompiledKnowledge).toHaveBeenCalledWith("", source.id, 0),
    );
    fireEvent.click(screen.getByRole("button", { name: "全部课程" }));
    await waitFor(() =>
      expect(searchCompiledKnowledge).toHaveBeenLastCalledWith(
        "",
        source.id,
        0,
      ),
    );
  });
  it("继续阅读追加完整后文，不丢弃前段", async () => {
    const first = {
      ...page,
      text: "第一段\n\n",
      total_chars: 10,
      next_offset: 5,
    };
    vi.mocked(readCompiledKnowledge)
      .mockResolvedValueOnce(first)
      .mockResolvedValueOnce({
        ...first,
        text: "完整后文",
        offset: 5,
        next_offset: null,
      });
    render(<CompiledKnowledgeBrowser />);
    await screen.findByText("第一段");
    fireEvent.click(screen.getByRole("button", { name: "继续阅读" }));
    await screen.findByText("完整后文");
    expect(screen.getByText("第一段")).toBeTruthy();
    expect(screen.getByText(/全文已显示/)).toBeTruthy();
  });
});
