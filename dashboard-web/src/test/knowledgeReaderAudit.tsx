/** 原生知识目录合成夹具；不读取学习库、不调用API、不真的打开Finder。 */

import { useState } from "react";
import ReactDOM from "react-dom/client";
import { AppShell } from "@/components/AppShell";
import { KnowledgeCompilerView } from "@/components/KnowledgeCompilerView";
import { ToastProvider } from "@/components/Toast";
import type { BridgeResponse } from "@/lib/api";
import type { ViewName } from "@/lib/types";
import "@/index.css";
import "@/aura-pages.css";

const scope = "automatic-learning:fixture";
const docs = Array.from({ length: 6 }, (_, i) => ({
  id: String(i + 1).repeat(32),
  source_id: i < 3 ? "automatic-courses" : "automatic-transcripts",
  source_name: i < 3 ? "自动课件" : "课堂字幕",
  relative_path: `秋/数据库/第${i + 1}讲.md`,
  title: `第${i + 1}讲：数据库资料.md`,
  folder: "秋/数据库",
  status: "completed",
  contract: "course-summary-v1",
}));
window.pywebview = {
  api: {
    invoke: async <T,>(
      action: string,
      payload?: Record<string, unknown>,
    ): Promise<BridgeResponse<T>> => {
      const doc = docs.find((d) => d.id === payload?.document_id) ?? docs[0];
      let data: unknown;
      if (action === "settings_status")
        data = { ai_enabled: true, ai_key_saved: true };
      else if (action === "ai_chat_sessions") data = { items: [] };
      else if (action === "knowledge_compiler_status")
        data = {
          status: "idle",
          task_id: null,
          mode: null,
          phase_index: 0,
          total_phases: 0,
          current_phase: null,
          counts: {
            markdown_files: 0,
            processed_files: 0,
            generated_notes: 0,
            warnings: 0,
          },
          events: [],
          warnings: [],
          error: null,
        };
      else if (action === "course_markdown_status")
        data = {
          enabled: true,
          status: "completed",
          output_root: "/synthetic",
          total: 6,
          completed: 6,
          pending: 0,
          failed: 0,
          issues: [],
          calls_today: 0,
        };
      else if (action === "compiled_knowledge_sources")
        data = {
          items: ["automatic-courses", "automatic-transcripts"].map((id) => ({
            id,
            name: id,
            kind: "automatic",
            root: "/synthetic",
            count: 3,
          })),
          count: 6,
          issues: [],
          truncated: false,
        };
      else if (action === "compiled_knowledge_search")
        data = {
          items: docs,
          count: 6,
          next_offset: null,
          issues: [],
          truncated: false,
          folders: [
            { source_id: scope, path: "秋", name: "秋", count: 6 },
            { source_id: scope, path: "秋/数据库", name: "数据库", count: 6 },
          ],
        };
      else if (action === "compiled_knowledge_read") {
        const text =
          `# ${doc.title}\n\n` +
          Array.from(
            { length: 60 },
            (_, i) =>
              `## 知识点${i + 1}\n\n第${docs.indexOf(doc) + 1}份正文：关系代数、查询条件及老师当堂补充。\n`,
          ).join("\n");
        data = {
          ...doc,
          text,
          total_chars: text.length,
          offset: 0,
          next_offset: null,
          section: "文档开头",
          citation: doc.relative_path,
          content_sha256: doc.id,
          truncated: false,
        };
      } else if (action === "compiled_knowledge_open") {
        document.documentElement.dataset.revealed = String(payload?.source_id);
        data = { status: "opened" };
      } else
        return {
          ok: false,
          error: { message: "隔离验收不执行其他操作" },
        } as BridgeResponse<T>;
      return { ok: true, data } as BridgeResponse<T>;
    },
  },
};
const root = document.getElementById("root");
function Audit() {
  const [view, setView] = useState<ViewName>("knowledge");
  const [drawerOpen, setDrawerOpen] = useState(false);
  return (
    <AppShell
      view={view}
      setView={setView}
      drawerOpen={drawerOpen}
      setDrawerOpen={setDrawerOpen}
      syncStatus={null}
      syncing={false}
      onSync={() => {}}
      onOpenAI={() => {}}
    >
      <KnowledgeCompilerView />
    </AppShell>
  );
}
if (root)
  ReactDOM.createRoot(root).render(
    <ToastProvider>
      <Audit />
    </ToastProvider>,
  );
