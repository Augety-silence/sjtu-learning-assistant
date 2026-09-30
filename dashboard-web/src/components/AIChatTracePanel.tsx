import {
  CheckCircle2,
  ChevronRight,
  CircleSlash2,
  LoaderCircle,
  Search,
  X,
} from "lucide-react";
import { useReducedMotion } from "motion/react";
import { useEffect, useRef, useState } from "react";
import aiAgentLogo from "@/assets/ai-agent-logo.png";
import { Button } from "@/components/ui/Button";
import type { AIAgentPreset, AIAgentTrace, AIToolRun } from "@/lib/types";

const toolLabels: Record<string, string> = {
  list_courses: "检索课程信息",
  search_course_files: "检索课程文件",
  list_course_files: "读取课程文件",
  get_deadlines: "检索截止日期",
  search_messages: "检索课程消息",
  get_message_detail: "读取消息详情",
  get_material_tree: "读取资料目录",
};

const traceStatusLabels: Record<string, string> = {
  completed: "已完成",
  complete: "已完成",
  ok: "已完成",
  max_steps: "已达步骤上限",
  failed: "未完成",
};

function compactJson(value: unknown) {
  if (value === null || value === undefined) return "无";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function resultLabel(run: AIToolRun) {
  if (run.status === "rejected") return "调用被安全策略拒绝";
  if (run.result_summary && typeof run.result_summary === "object") {
    const summary = run.result_summary as Record<string, unknown>;
    if (typeof summary.count === "number") return `命中 ${summary.count} 项`;
    if (typeof summary.items_count === "number") {
      return `命中 ${summary.items_count} 项`;
    }
    if (typeof summary.status === "string") return summary.status;
  }
  return "已返回安全摘要";
}

function traceHits(trace: AIAgentTrace) {
  return trace.tool_runs.reduce((total, run) => {
    if (!run.result_summary || typeof run.result_summary !== "object") {
      return total;
    }
    const summary = run.result_summary as Record<string, unknown>;
    const count = summary.count ?? summary.items_count;
    return total + (typeof count === "number" ? count : 0);
  }, 0);
}

function traceTitle(trace: AIAgentTrace) {
  const run = trace.tool_runs[0];
  return run ? (toolLabels[run.tool_name] ?? run.tool_name) : "整理检索结果";
}

function TraceRun({ run }: { run: AIToolRun }) {
  const ok = run.status === "ok";
  return (
    <details className="ai-tool-run">
      <summary>
        <span
          className={ok ? "ai-tool-icon is-ok" : "ai-tool-icon is-rejected"}
        >
          {ok ? (
            <CheckCircle2 aria-hidden="true" />
          ) : (
            <CircleSlash2 aria-hidden="true" />
          )}
        </span>
        <span className="ai-tool-run-copy">
          <strong>{toolLabels[run.tool_name] ?? run.tool_name}</strong>
          <span>{resultLabel(run)}</span>
        </span>
        <span className="ai-tool-phase">
          {run.phase === "prefetch" ? "预检索" : "模型调用"}
        </span>
        <ChevronRight className="ai-tool-caret" aria-hidden="true" />
      </summary>
      <div className="ai-tool-detail">
        <div>
          <span>安全参数</span>
          <pre>{compactJson(run.arguments_summary)}</pre>
        </div>
        <div>
          <span>结果摘要</span>
          <pre>{compactJson(run.result_summary)}</pre>
        </div>
      </div>
    </details>
  );
}

export function AIChatTracePanel({
  open,
  onClose,
  preset,
  traces,
  busy,
}: {
  open: boolean;
  onClose: () => void;
  preset: AIAgentPreset | null;
  traces: AIAgentTrace[];
  busy: boolean;
}) {
  const shouldReduceMotion = useReducedMotion();
  const scrollRef = useRef<HTMLDivElement>(null);
  const pinnedToLatestRef = useRef(true);
  const [showLatest, setShowLatest] = useState(false);

  const scrollToLatest = () => {
    const scroller = scrollRef.current;
    if (!scroller) return;
    scroller.scrollTo?.({
      top: scroller.scrollHeight,
      behavior: shouldReduceMotion ? "auto" : "smooth",
    });
    pinnedToLatestRef.current = true;
    setShowLatest(false);
  };

  useEffect(() => {
    if (!open) return;
    if (pinnedToLatestRef.current) {
      scrollToLatest();
    } else {
      setShowLatest(true);
    }
  }, [open, traces.length, busy]);

  return (
    <aside
      className={open ? "ai-activity is-open" : "ai-activity"}
      aria-label="Activity 检索轨迹"
      aria-hidden={!open}
      inert={!open ? true : undefined}
    >
      <header className="ai-activity-header">
        <div>
          <img src={aiAgentLogo} alt="" aria-hidden="true" />
          <span>
            <strong>Activity</strong>
            <small>{preset?.name ?? "本地学习 Agent"}</small>
          </span>
        </div>
        <Button
          variant="ghost"
          size="icon"
          aria-label="关闭 Activity"
          onClick={onClose}
        >
          <X aria-hidden="true" />
        </Button>
      </header>

      <div
        className="ai-activity-scroll"
        ref={scrollRef}
        onScroll={(event) => {
          const target = event.currentTarget;
          const distanceFromBottom =
            target.scrollHeight - target.scrollTop - target.clientHeight;
          pinnedToLatestRef.current = distanceFromBottom <= 24;
          if (pinnedToLatestRef.current) setShowLatest(false);
        }}
      >
        <div className="ai-activity-section-title">
          <span>Agent 工作过程</span>
          <small>
            {traces.length > 0 || busy
              ? `${traces.length + (busy ? 1 : 0)} 轮`
              : "等待任务"}
          </small>
        </div>

        {!busy && traces.length === 0 && (
          <div className="ai-activity-empty">
            <span>
              <Search aria-hidden="true" />
            </span>
            <strong>还没有检索活动</strong>
            <p>开始提问后，这里会用简洁时间线展示检索与整理进度。</p>
          </div>
        )}

        <div className="ai-trace-list">
          {traces.map((trace, index) => {
            const hits = traceHits(trace);
            const failed = trace.status === "failed";
            return (
              <details
                className={failed ? "ai-trace-step is-failed" : "ai-trace-step"}
                key={trace.id}
              >
                <summary>
                  <span className="ai-trace-node" aria-hidden="true">
                    {failed ? <CircleSlash2 /> : <CheckCircle2 />}
                  </span>
                  <span className="ai-trace-summary-copy">
                    <strong>
                      第 {index + 1} 轮　{traceTitle(trace)}
                    </strong>
                    <small>
                      {traceStatusLabels[trace.status] ?? trace.status} · 调用{" "}
                      {trace.tool_runs.length} 次 · 命中 {hits} 项
                    </small>
                  </span>
                  <ChevronRight className="ai-trace-caret" aria-hidden="true" />
                </summary>
                <div className="ai-trace-content">
                  {trace.tool_runs.length > 0 ? (
                    <div className="ai-tool-runs">
                      {trace.tool_runs.map((run, runIndex) => (
                        <TraceRun
                          key={`${trace.id}-${run.tool_name}-${runIndex}`}
                          run={run}
                        />
                      ))}
                    </div>
                  ) : (
                    <p className="ai-trace-no-tools">本轮未调用工具。</p>
                  )}
                </div>
              </details>
            );
          })}

          {busy && (
            <article className="ai-trace-step is-running" role="status">
              <span className="ai-trace-node" aria-hidden="true">
                <LoaderCircle className="animate-spin" />
              </span>
              <span className="ai-trace-summary-copy">
                <strong>第 {traces.length + 1} 轮　正在检索</strong>
                <small>正在规划并调用本地只读工具</small>
              </span>
            </article>
          )}
        </div>
      </div>
      {showLatest && (
        <button
          type="button"
          className="ai-activity-latest"
          onClick={scrollToLatest}
        >
          ↓ 查看最新活动
        </button>
      )}
    </aside>
  );
}
