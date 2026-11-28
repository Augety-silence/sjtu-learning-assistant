// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CompiledKnowledgeBrowser } from "@/components/CompiledKnowledgeBrowser";
import { MaterialsView } from "@/components/MaterialsView";
import {
  getMediaPreview,
  invoke,
  listCompiledKnowledgeSources,
  previewMaterial,
  readCompiledKnowledge,
  searchCompiledKnowledge,
} from "@/lib/api";
import type {
  CompiledKnowledgePage,
  MaterialNode,
  MaterialTree,
} from "@/lib/types";
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
  invoke: vi.fn(),
  getMediaPreview: vi.fn(),
  previewMaterial: vi.fn(),
  moveMaterial: vi.fn(),
  restoreMaterialAuto: vi.fn(),
  listCompiledKnowledgeSources: vi.fn(),
  searchCompiledKnowledge: vi.fn(),
  readCompiledKnowledge: vi.fn(),
  openCompiledKnowledge: vi.fn(),
  getKnowledgeMCPConfig: vi.fn(),
}));

const document = {
  id: "a".repeat(32),
  source_id: "automatic-courses",
  source_name: "课程",
  title: "讲义.md",
  relative_path: "学期/课程/讲义.md",
  folder: "学期/课程",
  status: "completed" as const,
};
const page: CompiledKnowledgePage = {
  ...document,
  text: "# 课程讲义\n\n这是正文。",
  content_sha256: "version1",
  offset: 0,
  next_offset: null,
  total_chars: 18,
  citation: document.relative_path,
  section: "第1页",
  truncated: false,
};
const folders = ["学期", "学期/课程", "学期/课程/课件"].map((path) => ({
  source_id: "automatic-courses",
  path,
  name: path.split("/").at(-1)!,
  count: 25,
}));
function material(
  name: string,
  extra: Partial<MaterialNode> = {},
): MaterialTree {
  return {
    categories: [],
    download_statuses: [],
    root: {
      id: "root",
      name: "全部资料",
      kind: "root",
      children: [
        {
          id: "course",
          name: "测试课程",
          kind: "course",
          children: [
            { id: "subfolder", kind: "folder", name: "下一层", children: [] },
            {
              id: "file",
              name,
              kind: "file",
              source_id: "file1",
              can_preview: true,
              can_open: true,
              download_status: "downloaded",
              local_path: "/tmp/文件",
              ...extra,
            },
          ],
        },
      ],
    },
  };
}
beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  documentElementRelease("2");
  vi.mocked(invoke).mockResolvedValue(material("讲义.md"));
  vi.mocked(previewMaterial).mockResolvedValue({
    kind: "text",
    name: "讲义.md",
    text: "# Markdown 讲义\n\n正文内容",
  });
  vi.mocked(listCompiledKnowledgeSources).mockResolvedValue({
    items: [],
    count: 25,
    issues: [],
    truncated: false,
  });
  vi.mocked(searchCompiledKnowledge).mockImplementation(
    async (_query, _source, _offset, folder) => ({
      items: folder === "学期/课程" ? [document] : [],
      folders,
      count: 1,
      next_offset: null,
      issues: [],
      truncated: false,
    }),
  );
  vi.mocked(readCompiledKnowledge).mockResolvedValue(page);
});
function documentElementRelease(release: string) {
  window.document.documentElement.dataset.uiRelease = release;
}
afterEach(() => {
  cleanup();
  documentElementRelease("classic");
});
async function openMaterials() {
  render(<MaterialsView />);
  fireEvent.click(await screen.findByRole("treeitem", { name: "测试课程" }));
  return screen.findByRole("listitem", { name: /讲义|课件|文档/ });
}
async function openKnowledgeFolder() {
  render(<CompiledKnowledgeBrowser />);
  const list = await screen.findByRole("list", { name: "知识库文件列表" });
  fireEvent.click(await within(list).findByRole("button", { name: /学期/ }));
  fireEvent.click(await within(list).findByRole("button", { name: /课程/ }));
  await within(list).findByRole("button", { name: /讲义.md/ });
  return list;
}

describe("2.0 文件目录与悬浮预览", () => {
  it("资料左侧只放目录，右侧有文件和子文件夹，点击打开独立 Markdown 预览并返回焦点", async () => {
    const row = await openMaterials();
    const fileButton = within(row).getByRole("button");
    expect(window.document.querySelector(".v2-material-preview")).toBeNull();
    expect(
      screen.getByRole("tree", { name: "资料目录" }).textContent,
    ).not.toContain("讲义.md");
    expect(
      screen
        .getByRole("button", { name: "下一层" })
        .closest(".v2-material-files"),
    ).toBeTruthy();
    fileButton.focus();
    fireEvent.click(fileButton);
    const dialog = await screen.findByRole("dialog", { name: "讲义.md" });
    expect(dialog.closest("[data-modal-layer]")?.parentElement).toBe(
      window.document.body,
    );
    expect(
      await within(dialog).findByRole("heading", { name: "Markdown 讲义" }),
    ).toBeTruthy();
    expect(screen.queryByText(/已加载“讲义.md”/)).toBeNull();
    await waitFor(() =>
      expect(window.document.activeElement).toBe(
        within(dialog).getByRole("button", { name: "关闭文件预览" }),
      ),
    );
    fireEvent.keyDown(window.document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(window.document.activeElement).toBe(fileButton);
    expect(window.document.querySelector("[inert]")).toBeNull();
  });
  it("空格仅触发一次预览，关闭后晚到的响应不能重开窗口", async () => {
    let finish!: (value: Awaited<ReturnType<typeof previewMaterial>>) => void;
    vi.mocked(previewMaterial).mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    const row = await openMaterials();
    const button = within(row).getByRole("button");
    button.focus();
    fireEvent.keyDown(button, { key: " " });
    fireEvent.keyUp(button, { key: " " });
    expect(previewMaterial).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "关闭文件预览" }));
    await act(async () =>
      finish({ kind: "text", name: "讲义.md", text: "晚到内容" }),
    );
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryByText("晚到内容")).toBeNull();
  });
  it.each(["pptx", "docx"])(
    "%s 保留真实结构化内容预览接口和本地打开",
    async (extension) => {
      vi.mocked(invoke).mockResolvedValue(
        material(extension === "pptx" ? "课件.pptx" : "文档.docx"),
      );
      vi.mocked(getMediaPreview).mockResolvedValue(
        extension === "pptx"
          ? { kind: "pptx", slides: [{ number: 1, text: "演示文稿正文" }] }
          : { kind: "docx", text: "Word 正文" },
      );
      const row = await openMaterials();
      fireEvent.click(within(row).getByRole("button"));
      const dialog = screen.getByRole("dialog");
      await waitFor(() =>
        expect(dialog.textContent).toContain(
          extension === "pptx" ? "演示文稿正文" : "Word 正文",
        ),
      );
      expect(getMediaPreview).toHaveBeenCalledWith("file1");
      fireEvent.click(within(dialog).getByRole("button", { name: "本地打开" }));
      await waitFor(() =>
        expect(invoke).toHaveBeenCalledWith("material_open", {
          source_id: "file1",
        }),
      );
    },
  );
  it("知识库不自动读正文，右侧仅列当前目录，点击与空格打开并恢复阅读位置", async () => {
    const list = await openKnowledgeFolder();
    expect(readCompiledKnowledge).not.toHaveBeenCalled();
    expect(within(list).getByRole("button", { name: /课件/ })).toBeTruthy();
    expect(
      window.document.querySelector(".v2-knowledge-directory")?.textContent,
    ).not.toContain("讲义.md");
    const button = within(list).getByRole("button", { name: /讲义.md/ });
    button.focus();
    fireEvent.click(button);
    const dialog = screen.getByRole("dialog", { name: "讲义" });
    await within(dialog).findByText("这是正文。");
    const reader = within(dialog).getByLabelText("Markdown 正文预览");
    reader.scrollTop = 184;
    fireEvent.scroll(reader);
    fireEvent.click(
      within(dialog).getByRole("button", { name: "关闭文件预览" }),
    );
    expect(window.document.activeElement).toBe(button);
    fireEvent.keyDown(button, { key: " " });
    await within(screen.getByRole("dialog")).findByText("这是正文。");
    expect(screen.getByLabelText("Markdown 正文预览").scrollTop).toBe(184);
  });
  it("知识库关闭后晚到的读取不能重开；目录浏览使用直属分页，搜索保留后代范围", async () => {
    let finish!: (value: CompiledKnowledgePage) => void;
    vi.mocked(readCompiledKnowledge).mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    const list = await openKnowledgeFolder();
    expect(searchCompiledKnowledge).toHaveBeenLastCalledWith(
      "",
      "automatic-courses",
      0,
      "学期/课程",
      undefined,
      true,
    );
    fireEvent.click(within(list).getByRole("button", { name: /讲义.md/ }));
    fireEvent.click(screen.getByRole("button", { name: "关闭文件预览" }));
    await act(async () => finish(page));
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.change(screen.getByRole("searchbox"), {
      target: { value: "公式" },
    });
    fireEvent.submit(screen.getByRole("search", { name: "知识点搜索" }));
    await waitFor(() =>
      expect(searchCompiledKnowledge).toHaveBeenLastCalledWith(
        "公式",
        "automatic-courses",
        0,
        "学期/课程",
        undefined,
        false,
      ),
    );
    expect(readCompiledKnowledge).toHaveBeenCalledTimes(1);
  });
});
