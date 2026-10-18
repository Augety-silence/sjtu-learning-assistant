import {
  Activity,
  CheckCircle2,
  ChevronRight,
  CircleSlash2,
  LoaderCircle,
} from "lucide-react";
import { useState } from "react";
import type { AIAgentTrace, AIToolRun } from "@/lib/types";

const toolLabels: Record<string, string> = {
  list_courses: "读取课程列表",
  search_course_files: "检索课程文件",
  list_course_files: "读取课程文件",
  get_deadlines: "检索截止日期",
  search_messages: "检索课程消息",
  get_message_detail: "读取消息详情",
  get_material_tree: "读取资料目录",
};

export function toolLabel(toolName: string) {
  return toolLabels[toolName] ?? toolName;
}

export function compactJson(value: unknown) {
  if (value === null || value === undefined) return "无";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export function toolResultLabel(run: AIToolRun) {
  if (run.status === "rejected") {
    // 后端对参数错误、数据源不可用和 preset 授权拒绝都标为 rejected，
    // 这里展示其已脱敏的 error（≤240 字），避免一律误标为"安全策略拒绝"。
    const summary = run.result_summary;
    const error =
      summary && typeof summary === "object"
        ? (summary as Record<string, unknown>).error
        : null;
    return typeof error === "string" && error.trim()
      ? `未执行：${error.trim()}`
      : "调用被拒绝";
  }
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

export function traceHitCount(toolRuns: AIToolRun[]) {
  return toolRuns.reduce((total, run) => {
    if (!run.result_summary || typeof run.result_summary !== "object") {
      return total;
    }
    const summary = run.result_summary as Record<string, unknown>;
    const count = summary.count ?? summary.items_count;
    return total + (typeof count === "number" ? count : 0);
  }, 0);
}

export function traceFailed(trace: AIAgentTrace) {
  return (
    !["completed", "complete", "ok"].includes(trace.status) ||
    trace.tool_runs.some((run) => run.status === "rejected")
  );
}

export function TraceRun({ run }: { run: AIToolRun }) {
  const ok = run.status === "ok";
  const [open, setOpen] = useState(false);
  return (
    <details
      className="ai-tool-run"
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
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
          <strong>{toolLabel(run.tool_name)}</strong>
          <span>{toolResultLabel(run)}</span>
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

export function InlineAgentExecution({
  trace,
  toolRuns,
  running = false,
  agentName,
  onOpenActivity,
}: {
  trace?: AIAgentTrace | null;
  toolRuns?: AIToolRun[];
  running?: boolean;
  agentName?: string;
  onOpenActivity: () => void;
}) {
  const [open, setOpen] = useState(false);

  if (running) {
    return (
      <section
        className="ai-execution-card is-running"
        role="status"
        aria-label="Agent 执行状态"
      >
        <div className="ai-execution-agent">
          <LoaderCircle className="animate-spin" aria-hidden="true" />
          <span>
            <strong>{agentName ?? "学习 Agent"}</strong>
            <small>请求已发送，正在等待 Agent 返回</small>
          </span>
        </div>
        <p>工具事件返回后会在这里显示，并同步到 Activity。</p>
      </section>
    );
  }

  const runs = trace?.tool_runs ?? toolRuns ?? [];
  if (!trace && runs.length === 0) return null;

  const failed = trace
    ? traceFailed(trace)
    : runs.some((run) => run.status === "rejected");
  const hits = traceHitCount(runs);
  const firstAction = runs[0] ? toolLabel(runs[0].tool_name) : "整理回答";
  const summary = failed
    ? `未完成 · ${runs.length} 项可观察操作`
    : `已完成 · ${runs.length} 项操作${hits > 0 ? ` · 命中 ${hits} 项` : ""}`;

  return (
    <details
      className={
        failed ? "ai-execution-card is-failed" : "ai-execution-card is-complete"
      }
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>
        <span className="ai-execution-status" aria-hidden="true">
          {failed ? <CircleSlash2 /> : <CheckCircle2 />}
        </span>
        <span className="ai-execution-summary-copy">
          <strong>{firstAction}</strong>
          <small>{summary}</small>
        </span>
        <span className="ai-execution-disclosure">查看过程</span>
        <ChevronRight className="ai-execution-caret" aria-hidden="true" />
      </summary>
      <div className="ai-execution-detail">
        {runs.length > 0 ? (
          <div className="ai-tool-runs">
            {runs.map((run, index) => (
              <TraceRun
                key={`${run.tool_name}-${run.phase}-${index}`}
                run={run}
              />
            ))}
          </div>
        ) : (
          <p className="ai-trace-no-tools">本轮没有可观察的工具调用。</p>
        )}
        <button type="button" onClick={onOpenActivity}>
          <Activity aria-hidden="true" />在 Activity 查看完整记录
        </button>
      </div>
    </details>
  );
}
