import { MotionConfig } from "motion/react";
import ReactDOM from "react-dom/client";
import { BackupView } from "@/components/BackupView";
import { MarkdownSyncProgress } from "@/components/MarkdownSyncProgress";
import type { BridgeResponse } from "@/lib/api";
import "@/index.css";
import "@/app-brand.css";
import "@/performance.css";

const group = {
  total: 101,
  completed: 100,
  pending: 1,
  failed: 0,
  ai_completed: 77,
  ai_failed: 0,
  issues: [],
  truncated: false,
};
const backup = {
  status: "idle",
  available: true,
  availability_message: null,
  counts: {
    total: 2654,
    canvas: 1168,
    mail: 261,
    ready: 1676,
    cloud_only: 0,
    missing_local: 978,
  },
  progress: null,
  last_result: {
    started_at: "2026-11-25",
    finished_at: "2026-11-25",
    uploaded: 1200,
    skipped_existing: 800,
    skipped_missing_local: 978,
    local_removed: 0,
    failed: 0,
    failures: [],
  },
};
function install() {
  window.pywebview ??= {};
  window.pywebview.api = {
    invoke: async <T,>(action: string): Promise<BridgeResponse<T>> => {
      if (action === "backup_status") return { ok: true, data: backup as T };
      if (action === "markdown_sync_progress")
        return {
          ok: true,
          data: {
            courses: group,
            transcripts: group,
            automatic: false,
            ai_ready: true,
            ai_model: "deepseek-reasoner",
            current: { status: "disabled", phase: null, current_file: null },
            updated_at: "2026-11-25",
            current_term_md: {
              enabled: true,
              status: "idle",
              total: 190,
              existing: 190,
              completed: 0,
              pending: 0,
              failed: 0,
            },
          } as T,
        };
      return {
        ok: false,
        error: { code: "fixture", message: "隔离验收仅提供合成读取数据" },
      };
    },
  };
}
install();
window.addEventListener("pywebviewready", install);
ReactDOM.createRoot(document.getElementById("root")!).render(
  <MotionConfig reducedMotion="always">
    <main style={{ padding: 24 }}>
      <MarkdownSyncProgress onNavigate={() => {}} />
      <BackupView />
    </main>
  </MotionConfig>,
);
