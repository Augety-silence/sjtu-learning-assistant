/** 合成数据 UI 验收；没有真实课程或外部模型请求。 */

import { FolderSync, HardDrive } from "lucide-react";
import ReactDOM from "react-dom/client";
import { CompiledKnowledgeBrowser } from "@/components/CompiledKnowledgeBrowser";
import { ToastProvider } from "@/components/Toast";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/Tabs";
import "@/components/cloud-drive.css";
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
  contract: index === 0 ? "course-lecture-v2" : undefined,
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
          folders: [
            {
              source_id: source.id,
              path: "2026-2027 秋季",
              name: "2026-2027 秋季",
              count: 87,
            },
            {
              source_id: source.id,
              path: "2026-2027 秋季/数据库系统",
              name: "数据库系统",
              count: 87,
            },
            {
              source_id: source.id,
              path: "2026-2027 秋季/数据库系统/课程资料",
              name: "课程资料",
              count: 87,
            },
          ],
          count: 87,
          next_offset: 20,
          issues: [],
          truncated: false,
        };
      else if (action === "compiled_knowledge_read")
        data = {
          ...documents.find((doc) => doc.id === payload?.document_id),
          text: Array.from(
            { length: payload?.document_id === documents[0].id ? 40 : 25 },
            (_, index) =>
              `## 知识主题 ${index + 1}\n\n关系模型组织实体和关系，先检查适用条件，再使用选择、投影与连接构造查询。本组合成数据只用于测试长文滚动，不代表真实编译长度。\n\n`,
          ).join(""),
          content_sha256: String(payload?.document_id).padEnd(64, "0"),
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
        <div className="drive-page-head">
          <Tabs defaultValue="files">
            <TabsList indicator aria-label="交大云盘视图">
              <TabsTrigger value="files">
                <HardDrive className="drive-tab-icon" aria-hidden="true" />
                我的文件
              </TabsTrigger>
              <TabsTrigger value="sync">
                <FolderSync className="drive-tab-icon" aria-hidden="true" />
                本地同步
              </TabsTrigger>
            </TabsList>
          </Tabs>
        </div>
        <CompiledKnowledgeBrowser />
      </main>
    </ToastProvider>,
  );
