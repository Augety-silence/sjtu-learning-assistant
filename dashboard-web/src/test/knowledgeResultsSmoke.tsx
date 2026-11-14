/** 合成数据 UI 验收；没有真实课程或外部模型请求。 */
import ReactDOM from "react-dom/client";
import { CompiledKnowledgeBrowser } from "@/components/CompiledKnowledgeBrowser";
import { ToastProvider } from "@/components/Toast";
import "@/index.css";

const source = {
  id: "automatic-courses",
  name: "自动课件 Markdown",
  kind: "automatic",
  root: "/synthetic/Courses",
  count: 87,
};
const documents = Array.from({ length: 20 }, (_, index) => ({
  id: index.toString(16).padStart(32, "0"),
  source_id: source.id,
  source_name: source.name,
  title: `第 ${index + 1} 讲.pptx.md`,
  relative_path: `2026-2027 秋季/数据库系统/课程资料/第 ${index + 1} 讲.pptx.md`,
  status: index === 0 ? "completed" : "pending",
  snippet: "关系代数、选择运算与投影运算的章节引用",
  match_offset: 0,
}));
window.pywebview = {
  api: {
    invoke: async <T,>(action: string, payload?: Record<string, unknown>) => {
      let data: unknown;
      if (action === "compiled_knowledge_sources")
        data = { items: [source], count: 87, issues: [], truncated: false };
      else if (action === "compiled_knowledge_search")
        data = {
          items: documents,
          count: 87,
          next_offset: 20,
          issues: [],
          truncated: false,
        };
      else if (action === "compiled_knowledge_read")
        data = {
          ...documents.find((doc) => doc.id === payload?.document_id),
          text: "# 关系代数\n\n## 第 1 页\n\n关系代数包括选择、投影、连接等运算。\n\n|运算|作用|\n|---|---|\n|选择|按谓词过滤元组|\n|投影|保留指定属性|\n\n![第1页](assets/page-0001.jpg)\n\n> 合成测试内容，不代表真实课程。",
          offset: 0,
          next_offset: null,
          total_chars: 180,
          section: "第 1 页",
          citation: "数据库/第1讲.pptx.md",
          truncated: false,
        };
      else if (action === "mcp_config")
        data = { command: "/synthetic/App", args: ["--mcp-stdio"] };
      else if (action === "compiled_knowledge_open")
        data = { status: "opened" };
      else
        return {
          ok: false,
          error: { code: "denied", message: "隔离验收不允许其他操作" },
        };
      return { ok: true, data: data as T };
    },
  },
};
const root = document.getElementById("root");
if (root)
  ReactDOM.createRoot(root).render(
    <ToastProvider>
      <main style={{ padding: 16 }}>
        <CompiledKnowledgeBrowser />
      </main>
    </ToastProvider>,
  );
