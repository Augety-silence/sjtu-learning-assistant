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
  within,
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
  it("正常打开不显示成功段落，结果数仅提供给读屏，读正文没有分页说明", async () => {
    render(<CompiledKnowledgeBrowser />);
    await screen.findByText("关系代数");
    expect(screen.getByText("找到 87 份结果").className).toBe("sr-only");
    fireEvent.click(screen.getByRole("button", { name: "在本地显示" }));
    await waitFor(() => expect(openCompiledKnowledge).toHaveBeenCalled());
    expect(screen.queryByText(/已在系统文件管理器/)).toBeNull();
    expect(screen.queryByText(/正文分页预览|已加载.*字符/)).toBeNull();
  });

  it("目录不完整与定位失败仍明确显示，不因去文案而隐藏", async () => {
    vi.mocked(searchCompiledKnowledge).mockResolvedValue({
      items: [document],
      count: 1,
      next_offset: null,
      issues: ["索引部分损坏"],
      truncated: true,
    });
    vi.mocked(openCompiledKnowledge).mockRejectedValue(
      new Error("无法在本地显示"),
    );
    render(<CompiledKnowledgeBrowser />);
    await screen.findByText("关系代数");
    expect(screen.getByText("索引部分损坏").getAttribute("role")).toBe("alert");
    expect(screen.getByText(/结果范围不完整/).getAttribute("role")).toBe(
      "alert",
    );
    fireEvent.click(screen.getByRole("button", { name: "在本地显示" }));
    expect(
      (await screen.findByText("无法在本地显示")).getAttribute("role"),
    ).toBe("alert");
  });

  it("来源说明只在独立提示中，正文没有常驻注释，图标细小且可键盘关闭提示", async () => {
    const { container } = render(<CompiledKnowledgeBrowser />);
    await screen.findByText("关系代数");
    const reader = screen.getByLabelText("Markdown 正文预览");
    expect(within(reader).queryByText(/来源：/)).toBeNull();
    const info = screen.getByRole("button", { name: "文档来源与状态" });
    const tooltip = window.document.getElementById(
      info.getAttribute("aria-describedby") || "",
    ) as HTMLElement;
    expect(info.getAttribute("aria-describedby")).toBe(tooltip.id);
    expect(tooltip.textContent).toContain(page.citation);
    fireEvent.focus(info);
    fireEvent.keyDown(info, { key: "Escape" });
    expect(info.parentElement?.getAttribute("data-dismissed")).toBe("true");
    const icon = container.querySelector(
      ".compiled-knowledge-navigation-toggle svg",
    );
    expect(icon?.getAttribute("width")).toBe("16");
    expect(icon?.getAttribute("stroke-width")).toBe("1.5");
  });

  it("统一目录选择保留两类全部文件，定位字幕时使用原来源而非虚拟范围", async () => {
    const transcript = {
      ...document,
      id: "b".repeat(32),
      source_id: "automatic-transcripts",
      title: "第一讲字幕.md",
    };
    const scope = "automatic-learning:term30";
    vi.mocked(searchCompiledKnowledge).mockResolvedValue({
      items: [document, transcript],
      count: 2,
      next_offset: null,
      issues: [],
      truncated: false,
      folders: [{ source_id: scope, path: "秋", name: "秋", count: 2 }],
    });
    vi.mocked(readCompiledKnowledge).mockImplementation(async (id) => ({
      ...page,
      ...(id === transcript.id ? transcript : document),
      next_offset: null,
    }));
    render(<CompiledKnowledgeBrowser />);
    fireEvent.click(await screen.findByRole("button", { name: /秋.*2/ }));
    await waitFor(() =>
      expect(searchCompiledKnowledge).toHaveBeenCalledWith("", scope, 0, "秋"),
    );
    fireEvent.click(await screen.findByRole("button", { name: /第一讲字幕/ }));
    await waitFor(() =>
      expect(readCompiledKnowledge).toHaveBeenCalledWith(transcript.id, 0),
    );
    fireEvent.click(screen.getByRole("button", { name: "在本地显示" }));
    await waitFor(() =>
      expect(openCompiledKnowledge).toHaveBeenCalledWith(
        transcript.source_id,
        transcript.id,
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "收起课程目录" }));
    fireEvent.click(screen.getByRole("button", { name: "展开课程目录" }));
    fireEvent.click(screen.getByRole("button", { name: "全部课程" }));
    await waitFor(() =>
      expect(screen.queryByLabelText("Markdown 正文预览")).toBeNull(),
    );
    expect(screen.getByLabelText("课程与课时文件夹")).toBeTruthy();
  });
  it("长课程树不挤走文件列表，同一文件夹全部六份文件均可切换", async () => {
    const docs = Array.from({ length: 6 }, (_, index) => ({
      ...document,
      id: String(index + 1).repeat(32),
      title: `第${index + 1}讲.pptx.md`,
      folder: "秋/数据库/课件",
    }));
    vi.mocked(searchCompiledKnowledge).mockResolvedValue({
      items: docs,
      count: 6,
      next_offset: null,
      issues: [],
      truncated: false,
      folders: [
        { source_id: source.id, path: "秋", name: "秋", count: 94 },
        { source_id: source.id, path: "秋/数据库", name: "数据库", count: 8 },
        {
          source_id: source.id,
          path: "秋/数据库/课件",
          name: "课件",
          count: 6,
        },
      ],
    });
    vi.mocked(readCompiledKnowledge).mockImplementation(async (id) => ({
      ...page,
      id,
      text: `文件正文${id}`,
      next_offset: null,
    }));
    render(<CompiledKnowledgeBrowser />);
    fireEvent.click(await screen.findByRole("button", { name: /课件.*6/ }));
    const list = await screen.findByLabelText("Markdown 结果列表");
    for (const doc of docs) {
      fireEvent.click(
        within(list).getByRole("button", {
          name: new RegExp(doc.title.split(".")[0]),
        }),
      );
      await screen.findByText(`文件正文${doc.id}`);
    }
    expect(within(list).getAllByRole("button")).toHaveLength(6);
    expect(list.parentElement).toBe(
      screen.getByLabelText("课程与课时文件夹").parentElement,
    );
  });

  it("从头阅读始终位于正文滚动区外，正文允许任意文字选择和原生复制", async () => {
    vi.mocked(readCompiledKnowledge).mockResolvedValue({
      ...page,
      text: "# 课程材料\n\n可选择的第一段。\n\n可选择的第二段。",
      next_offset: null,
    });
    render(<CompiledKnowledgeBrowser />);
    await screen.findByText("可选择的第二段。");
    const reader = screen.getByLabelText("Markdown 正文预览");
    const actions = screen.getByRole("toolbar", { name: "阅读操作" });
    expect(reader.contains(actions)).toBe(false);
    expect(
      within(actions).getByRole("button", { name: "从头阅读" }),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: /复制.*Markdown/ })).toBeNull();
    const range = window.document.createRange();
    range.setStart(screen.getByText("可选择的第一段。").firstChild!, 0);
    range.setEnd(screen.getByText("可选择的第二段。").firstChild!, 7);
    window.getSelection()?.removeAllRanges();
    window.getSelection()?.addRange(range);
    expect(window.getSelection()?.toString()).toContain("第一段");
    expect(window.getSelection()?.toString()).toContain("第二段");
    expect(fireEvent.copy(reader)).toBe(true);
    reader.scrollTop = 800;
    fireEvent.scroll(reader);
    fireEvent.click(within(actions).getByRole("button", { name: "从头阅读" }));
    await waitFor(() => expect(reader.scrollTop).toBe(0));
  });
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
    expect(screen.queryByRole("button", { name: "继续阅读" })).toBeNull();
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
    fireEvent.click(screen.getByRole("button", { name: "在本地显示" }));
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
    expect(await screen.findByText("暂无编译资料")).toBeTruthy();
  });
  it("提供真实安装路径的 MCP 配置而不修改 AI 客户端", async () => {
    render(<CompiledKnowledgeBrowser />);
    fireEvent.click(screen.getByText("外部 AI 接入"));
    fireEvent.click(screen.getByRole("button", { name: "显示连接配置" }));
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
    await screen.findByText("数据库");
    expect(screen.queryByLabelText("Markdown 正文预览")).toBeNull();
    expect(screen.queryByRole("toolbar", { name: "阅读操作" })).toBeNull();
    expect(screen.queryByText("选择文件夹或搜索知识点。")).toBeNull();
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
    expect(screen.queryByRole("button", { name: "继续阅读" })).toBeNull();
  });
});
