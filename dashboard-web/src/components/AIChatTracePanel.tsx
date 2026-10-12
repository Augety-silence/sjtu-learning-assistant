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
import {
  TraceRun,
  toolLabel,
  traceFailed,
  traceHitCount,
} from "@/components/AIExecutionCard";
import { Button } from "@/components/ui/Button";
import type { AIAgentPreset, AIAgentTrace } from "@/lib/types";

const traceStatusLabels: Record<string, string> = {
  completed: "已完成",
  complete: "已完成",
  ok: "已完成",
  max_steps: "已达步骤上限",
  failed: "未完成",
};

function traceTitle(trace: AIAgentTrace) {
  const run = trace.tool_runs[0];
  return run ? toolLabel(run.tool_name) : "整理回答";
}

function ActivityTraceStep({
  trace,
  index,
}: {
  trace: AIAgentTrace;
  index: number;
}) {
  const [open, setOpen] = useState(false);
  const hits = traceHitCount(trace.tool_runs);
  const failed = traceFailed(trace);

  return (
    <details
      className={failed ? "ai-trace-step is-failed" : "ai-trace-step"}
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
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
      id="ai-activity-panel"
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
          {traces.map((trace, index) => (
            <ActivityTraceStep key={trace.id} trace={trace} index={index} />
          ))}

          {busy && (
            <article className="ai-trace-step is-running" role="status">
              <span className="ai-trace-node" aria-hidden="true">
                <LoaderCircle className="animate-spin" />
              </span>
              <span className="ai-trace-summary-copy">
                <strong>等待 Agent 返回</strong>
                <small>请求已发送，尚未收到新的工具事件</small>
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
