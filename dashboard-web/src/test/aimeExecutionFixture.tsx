import { MotionConfig } from "motion/react";
import ReactDOM from "react-dom/client";
import { AIChatTracePanel } from "@/components/AIChatTracePanel";
import { InlineAgentExecution } from "@/components/AIExecutionCard";
import { MessageInbox } from "@/components/MessageInbox";
import { applyThemeMode } from "@/lib/theme";
import type { AIAgentTrace, MessageItem, ThemeMode } from "@/lib/types";
import "@/index.css";
import "@/aura-pages.css";
import "@/ai-chat-aura.css";
import "@/app-brand.css";
import "@/performance.css";
import "@/selection-indicator.css";
import "@/message-center.css";
import "@/aime-chat.css";

const trace: AIAgentTrace = {
  id: "preview",
  preset_id: "general",
  status: "completed",
  steps: 3,
  duration_ms: 12000,
  tool_runs: [
    {
      tool_name: "read_compiled_knowledge",
      phase: "model",
      status: "ok",
      progress_text: "**正在核对课程资料**",
      arguments_summary: { offset: 0 },
      result_summary: {
        returned_chars: 600,
        total_chars: 1800,
        offset: 0,
        next_offset: 600,
        preview: {
          version: 1,
          items: [{ title: "图论讲义", detail: "离散数学 · 秋季" }],
          text: "欧拉路径经过每条边一次。\n连通性是判断路径存在的条件之一。",
          text_truncated: false,
        },
      },
      duration_ms: 230,
    },
    {
      tool_name: "read_compiled_knowledge",
      phase: "model",
      status: "ok",
      arguments_summary: { offset: 600 },
      result_summary: {
        returned_chars: 600,
        total_chars: 1800,
        offset: 600,
        next_offset: 1200,
        preview: {
          version: 1,
          items: [{ title: "图论讲义（续）", detail: "离散数学" }],
          text: "本段继续说明欧拉回路的条件。",
          text_truncated: false,
        },
      },
      duration_ms: 160,
    },
    {
      tool_name: "read_course_file_text",
      phase: "model",
      status: "rejected",
      arguments_summary: {},
      result_summary: { error: "所选文件尚未下载到本机。" },
    },
    {
      tool_name: "propose_file_change",
      phase: "model",
      status: "ok",
      arguments_summary: { mode: "create" },
      result_summary: {
        change_id: "synthetic-change",
        added: 6,
        removed: 0,
        approval: "pending_user",
      },
    },
  ],
};
const mode = new URLSearchParams(location.search).get("theme") as ThemeMode;
const messages: MessageItem[] = [
  {
    kind: "email",
    source_id: "fixture-mail",
    title: "论文阅读清单已更新",
    source_label: "课程助教",
    occurred_at: "2026-11-25T10:00:00Z",
    preview: "请阅读本周指定论文，课堂讨论将在周五进行。",
    is_unread: true,
    attachment_count: 1,
    url: null,
  },
];
applyThemeMode(
  ["light", "dark", "system", "aime"].includes(mode) ? mode : "aime",
);
ReactDOM.createRoot(document.getElementById("root")!).render(
  <MotionConfig reducedMotion="always">
    <div
      className="aime-preview-root"
      style={{ maxWidth: 760, margin: "24px auto", padding: 16 }}
    >
      <style>
        {
          ".aime-preview-root .ai-activity { width: 100%; position: relative; min-height: 300px; }"
        }
      </style>
      <div
        style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 20 }}
      >
        {(["system", "light", "dark", "aime"] as const).map((value, index) => (
          <button
            key={value}
            type="button"
            onClick={() => applyThemeMode(value)}
          >
            {["跟随系统", "浅色", "深色", "Aime 风格"][index]}
          </button>
        ))}
      </div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "minmax(0, 1fr)",
          gap: 24,
        }}
      >
        <section
          className="messages-page"
          style={{
            minWidth: 0,
            border: "1px solid var(--border-subtle)",
            borderRadius: 12,
            overflow: "hidden",
          }}
        >
          <MessageInbox
            items={messages}
            query=""
            total={1}
            unreadCount={1}
            selectedKey="email:fixture-mail"
            detailItem={null}
            rowRefs={new Map()}
            onSelect={() => undefined}
            onOpen={() => undefined}
            nextOffset={null}
            loadingMore={false}
            loadMore={async () => undefined}
          />
        </section>
        <main style={{ maxWidth: 760, width: "100%" }}>
          <h1 style={{ fontSize: 22, marginBottom: 16 }}>课程资料核对</h1>
          <p style={{ marginBottom: 16 }}>
            这是一份合成记录，用于核验折叠、展开、失败提示和文件修改确认状态。
          </p>
          <InlineAgentExecution
            trace={trace}
            running
            onOpenActivity={() => undefined}
          />
          <p style={{ marginTop: 20 }}>
            读取了两段正文；文件修改提案等待你的确认。
          </p>
        </main>
        <div style={{ position: "relative", maxWidth: 760, minHeight: 330 }}>
          <AIChatTracePanel
            open
            onClose={() => undefined}
            preset={null}
            traces={[trace]}
            busy={false}
            runningTaskId={null}
          />
        </div>
      </div>
    </div>
  </MotionConfig>,
);
