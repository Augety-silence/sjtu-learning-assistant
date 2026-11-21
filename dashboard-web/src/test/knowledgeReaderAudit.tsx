/** 隔离视觉验收：只使用合成课件，不访问数据库、学校、模型或真实用户文件。 */
import ReactDOM from "react-dom/client";
import { CompiledKnowledgeBrowser } from "@/components/CompiledKnowledgeBrowser";
import { ToastProvider } from "@/components/Toast";
import type { BridgeResponse } from "@/lib/api";
import "@/index.css";

const params = new URLSearchParams(location.search);
const width = params.get("width") === "640" ? 640 : 1200;
const source = {
  id: "automatic-courses",
  name: "自动课件 Markdown",
  kind: "automatic",
  root: "/synthetic/Knowledge Markdown/Courses",
  count: 94,
};
const documents = Array.from({ length: 6 }, (_, index) => ({
  id: String(index + 1).repeat(32),
  source_id: source.id,
  source_name: source.name,
  title: `第${index + 1}讲：课程主题与关键知识.pdf.md`,
  relative_path: `2026-2027 Fall/中级宏观经济学/课件/第${index + 1}讲.pdf.md`,
  folder: "2026-2027 Fall/中级宏观经济学/课件",
  status: "completed",
  contract: "course-summary-v1",
  match_offset: 0,
  snippet: "",
}));
const folders = [
  {
    source_id: source.id,
    path: "2026-2027 Fall",
    name: "2026-2027 Fall",
    count: 94,
  },
  ...[
    "中级宏观经济学",
    "市场调研分析",
    "建模与优化",
    "数据库原理与系统",
    "数据结构",
    "文本分析与大模型",
    "管理会计",
    "马克思主义基本原理",
  ].flatMap((name) => [
    { source_id: source.id, path: `2026-2027 Fall/${name}`, name, count: 12 },
    {
      source_id: source.id,
      path: `2026-2027 Fall/${name}/课件`,
      name: "课件",
      count: 6,
    },
  ]),
];
window.pywebview = {
  api: {
    invoke: async <T,>(
      action: string,
      payload?: Record<string, unknown>,
    ): Promise<BridgeResponse<T>> => {
      let data: unknown;
      if (action === "compiled_knowledge_sources")
        data = { items: [source], count: 94, issues: [], truncated: false };
      else if (action === "compiled_knowledge_search")
        data = {
          items: documents,
          count: 6,
          next_offset: null,
          folders,
          issues: [],
          truncated: false,
        };
      else if (action === "compiled_knowledge_read") {
        const doc =
          documents.find((item) => item.id === payload?.document_id) ??
          documents[0];
        const text =
          `# ${doc.title}\n\n## 课程主线\n\n这是隔离验收的合成资料，不能作为真实课程知识。\n\n` +
          Array.from(
            { length: 36 },
            (_, index) =>
              `### 知识段落 ${index + 1}\n\n本段包含可以自由选择复制的课程内容。概念、条件与结论应该一起阅读；切换文件时保持每份文件独立的阅读记忆。\n\n`,
          ).join("");
        const offset = Number(payload?.offset ?? 0);
        data = {
          ...doc,
          text: text.slice(offset, offset + 8000),
          offset,
          total_chars: text.length,
          next_offset: offset + 8000 < text.length ? offset + 8000 : null,
          truncated: offset + 8000 < text.length,
          content_sha256: doc.id.repeat(2),
          section: "课程主线",
          citation: doc.relative_path,
        };
      } else
        return {
          ok: false,
          error: { message: "隔离验收不执行写入或外部连接。" },
        } as BridgeResponse<T>;
      return { ok: true, data } as BridgeResponse<T>;
    },
  },
};

const root = document.getElementById("root");
if (root)
  ReactDOM.createRoot(root).render(
    <ToastProvider>
      <main style={{ width, maxWidth: "100%", margin: "0 auto", padding: 20 }}>
        <CompiledKnowledgeBrowser />
      </main>
    </ToastProvider>,
  );
