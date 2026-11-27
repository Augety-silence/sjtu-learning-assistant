import {
  CheckCircle2,
  ChevronRight,
  CircleSlash2,
  LoaderCircle,
} from "lucide-react";
import { useState } from "react";
import { ExecutionNote } from "@/components/AIExecutionNote";
import { ToolRunGroup } from "@/components/AIExecutionToolGroup";
import { groupToolRuns } from "@/components/AIExecutionToolGroups";
import {
  toolLabel,
  traceFailed,
  traceHitCount,
  traceRejectedCount,
} from "@/components/AIExecutionTools";
import type { AIAgentTrace } from "@/lib/types";

const traceStatusLabels: Record<string, string> = {
  completed: "已完成",
  complete: "已完成",
  ok: "已完成",
  max_steps: "本轮生成已达到上限",
  timeout: "已超时",
  failed: "未完成",
  running: "进行中",
};

function traceTitle(trace: AIAgentTrace) {
  const run = trace.tool_runs[0];
  return run ? toolLabel(run.tool_name) : "整理回答";
}

export function ActivityTraceStep({
  trace,
  index,
  defaultOpen = false,
}: {
  trace: AIAgentTrace;
  index: number;
  defaultOpen?: boolean;
}) {
  const [userOpen, setOpen] = useState<boolean | null>(null);
  const open = userOpen ?? (defaultOpen || trace.status === "running");
  const hits = traceHitCount(trace.tool_runs);
  const rejected = traceRejectedCount(trace.tool_runs);
  // 流式过程中 AIChatView 会先插入 status="running" 的临时轨迹；它不是失败轮次。
  const live = trace.status === "running";
  const failed = !live && traceFailed(trace);

  return (
    <details
      className={failed ? "ai-trace-step is-failed" : "ai-trace-step"}
      open={open}
      onToggle={(event) => {
        if (event.target === event.currentTarget)
          setOpen(event.currentTarget.open);
      }}
    >
      <summary>
        <span className="ai-trace-node" aria-hidden="true">
          {live ? (
            <LoaderCircle className="animate-spin" />
          ) : failed ? (
            <CircleSlash2 />
          ) : (
            <CheckCircle2 />
          )}
        </span>
        <span className="ai-trace-summary-copy">
          <strong>
            第 {index + 1} 轮　{traceTitle(trace)}
          </strong>
          <small>
            {traceStatusLabels[trace.status] ?? trace.status} · 调用{" "}
            {trace.tool_runs.length} 次 · 命中 {hits} 项
            {rejected > 0 && ` · ${rejected} 项未执行`}
          </small>
        </span>
        <ChevronRight className="ai-trace-caret" aria-hidden="true" />
      </summary>
      <div className="ai-trace-content">
        {trace.tool_runs.length > 0 ? (
          <div className="ai-tool-runs">
            {groupToolRuns(trace.tool_runs).map((group) => (
              <ToolRunGroup
                key={group.start}
                group={group}
                defaultOpen={defaultOpen || live}
              />
            ))}
            <ExecutionNote
              key={`pending-${trace.tool_runs.length}`}
              text={trace.pending_text}
              streaming={live && open}
            />
          </div>
        ) : trace.pending_text ? (
          <ExecutionNote text={trace.pending_text} streaming={live && open} />
        ) : (
          <p className="ai-trace-no-tools">本轮未调用工具。</p>
        )}
      </div>
    </details>
  );
}
