// 仅渲染合成对话，不连接桥接接口、真实课程或模型服务。
import ReactDOM from "react-dom/client";
import { AIChatMessageBubble } from "@/components/AIChatMessageBubble";
import type { AIAgentTrace } from "@/lib/types";
import "@/index.css";
import "@/ai-chat-aura.css";

const compact = new URLSearchParams(location.search).has("compact");
const trace: AIAgentTrace = {
  id: "fixture-trace", preset_id: "general", status: "completed", steps: 3, duration_ms: 12000,
  tool_runs: [
    { tool_name: "browse_knowledge_wiki", phase: "model", status: "ok", progress_text: "先查看课程知识地图。", arguments_summary: {}, result_summary: { count: 8 }, duration_ms: 180 },
    { tool_name: "read_compiled_knowledge", phase: "model", status: "ok", progress_text: "已定位相关课件，接下来核对正文和出处。", arguments_summary: {}, result_summary: { read_status: "ready", returned_chars: 12000 }, duration_ms: 200 },
  ],
};

ReactDOM.createRoot(document.getElementById("root")!).render(
  <main className="ai-workspace" style={{ minHeight: "100vh", padding: 32 }}>
    <section className="ai-conversation has-messages" style={{ width: compact ? 560 : 1000, maxWidth: "100%", margin: "0 auto", padding: 24 }}>
      <div className="ai-chat-messages">
        <AIChatMessageBubble
          message={{ id: "fixture-answer", role: "assistant", content: "已找到课程成绩组成：\n\n课堂参与 15% · 测验 20% · 项目 25% · 期末考试 40%。\n\n来源：建模与优化 · 第一节课件第 23 页。", created_at: "2026-11-23T01:00:00Z", model: "deepseek-chat", trace_id: trace.id }}
          trace={trace} agentName="综合助理" onOpenActivity={() => {}} onRevealAttachment={() => {}} showCodeLineNumbers={false}
        />
      </div>
    </section>
  </main>,
);
