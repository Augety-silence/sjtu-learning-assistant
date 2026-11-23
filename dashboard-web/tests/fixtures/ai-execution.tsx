// 仅渲染合成对话，不连接桥接接口、真实课程或模型服务。
import ReactDOM from "react-dom/client";
import { Profiler, useEffect, useState } from "react";
import { AIChatMessageBubble } from "@/components/AIChatMessageBubble";
import type { AIAgentTrace } from "@/lib/types";
import "@/index.css";
import "@/ai-chat-aura.css";

const compact = new URLSearchParams(location.search).has("compact");
const live = new URLSearchParams(location.search).has("live");
const note = '**课程文件转换情况： **\n\n- 正文可读：**101**\n- 已生成总结：**43**\n\n| 类型 | 数量 |\n| --- | --- |\n| 课堂文稿 | 194 |\n| 课程课件 | 101 |\n\n```text\n只读核对，不重复编译\n```\n\n接下来核对未完成范围与来源。';
const metrics = { commits: 0, totalMs: 0, maxMs: 0 };
const trace: AIAgentTrace = {
  id: "fixture-trace", preset_id: "general", status: "completed", steps: 3, duration_ms: 12000,
  tool_runs: [
    { tool_name: "browse_knowledge_wiki", phase: "model", status: "ok", progress_text: live ? "先查看**课程知识地图**。" : note, arguments_summary: {}, result_summary: { count: 8 }, duration_ms: 180 },
    { tool_name: "read_compiled_knowledge", phase: "model", status: "ok", progress_text: "已定位相关课件，接下来核对正文和出处。", arguments_summary: {}, result_summary: { read_status: "ready", returned_chars: 12000 }, duration_ms: 200 },
  ],
};

function Fixture() {
  const [received, setReceived] = useState(0);
  useEffect(() => {
    if (!live) return;
    const timer = window.setInterval(() => setReceived((n) => Math.min(note.length, n + 8)), 100);
    return () => window.clearInterval(timer);
  }, []);
  const running = live && received < note.length;
  return (
  <main className="ai-workspace" style={{ minHeight: "100vh", padding: 32 }}>
    <section className="ai-conversation has-messages" style={{ width: compact ? 560 : 1000, maxWidth: "100%", margin: "0 auto", padding: 24 }}>
      <div className="ai-chat-messages">
        <Profiler id="fixture" onRender={(_id, _phase, duration) => {
          metrics.commits++; metrics.totalMs += duration; metrics.maxMs = Math.max(metrics.maxMs, duration);
          document.body.dataset.metrics = JSON.stringify(metrics);
        }}>
          <AIChatMessageBubble
            message={{ id: "fixture-answer", role: "assistant", content: running ? "" : "已找到课程成绩组成：\n\n课堂参与 15% · 测验 20% · 项目 25% · 期末考试 40%。\n\n来源：建模与优化 · 第一节课件第 23 页。", created_at: "2026-11-23T01:00:00Z", model: "deepseek-chat", trace_id: trace.id }}
            trace={live ? {...trace, status: running ? "running" : "completed", pending_text: note.slice(0, received)} : trace}
            running={running}
            agentName="综合助理" onOpenActivity={() => {}} onRevealAttachment={() => {}} showCodeLineNumbers={false}
          />
        </Profiler>
      </div>
    </section>
  </main>
  );
}
ReactDOM.createRoot(document.getElementById("root")!).render(<Fixture />);
