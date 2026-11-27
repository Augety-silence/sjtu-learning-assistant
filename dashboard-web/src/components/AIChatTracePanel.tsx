import { LoaderCircle, Search, X } from "lucide-react";
import { useReducedMotion } from "motion/react";
import { useEffect, useRef, useState } from "react";
import aiAgentLogo from "@/assets/ai-agent-logo.png";
import { ActivityTraceStep } from "@/components/AIExecutionTraceStep";
import { Button } from "@/components/ui/Button";
import type { AIAgentPreset, AIAgentTrace } from "@/lib/types";

export function AIChatTracePanel({
  open,
  onClose,
  preset,
  traces,
  busy,
  runningTaskId,
}: {
  open: boolean;
  onClose: () => void;
  preset: AIAgentPreset | null;
  traces: AIAgentTrace[];
  busy: boolean;
  runningTaskId: string | null;
}) {
  const shouldReduceMotion = useReducedMotion();
  const scrollRef = useRef<HTMLDivElement>(null);
  const pinnedToLatestRef = useRef(true);
  const [showLatest, setShowLatest] = useState(false);
  // 已有流式轨迹在展示时，不再额外渲染“等待”占位，也不重复计入轮数。
  // 不用 Array.prototype.at：macOS 12.0–12.2 的 WKWebView（Safari 15.0–15.3）未实现，会整页抛错。
  const lastTrace = traces.length > 0 ? traces[traces.length - 1] : undefined;
  const liveTrace = Boolean(
    runningTaskId &&
      lastTrace?.id === `stream-${runningTaskId}` &&
      lastTrace.status === "running",
  );
  const pending = busy && !liveTrace;

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
  }, [
    open,
    traces.length,
    lastTrace?.tool_runs.length,
    lastTrace?.pending_text?.length,
    busy,
  ]);

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
              ? `${traces.length + (pending ? 1 : 0)} 轮`
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
            <ActivityTraceStep
              key={trace.id}
              trace={trace}
              index={index}
              defaultOpen={index === traces.length - 1}
            />
          ))}

          {pending && (
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
