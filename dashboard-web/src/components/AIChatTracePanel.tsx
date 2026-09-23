import {
  Activity,
  Bot,
  CheckCircle2,
  ChevronRight,
  CircleSlash2,
  LoaderCircle,
  Search,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/Button";
import type { AIAgentPreset, AIAgentTrace, AIToolRun } from "@/lib/types";

const toolLabels: Record<string, string> = {
  list_courses: "查询课程",
  search_course_files: "搜索课程文件",
  list_course_files: "读取课程文件",
  get_deadlines: "查询截止日期",
  search_messages: "搜索消息",
  get_message_detail: "读取消息详情",
  get_material_tree: "读取资料树",
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
  const newestFirst = [...traces].reverse();
  return (
    <aside
      className={open ? "ai-activity is-open" : "ai-activity"}
      aria-label="Activity 检索轨迹"
    >
      <header className="ai-activity-header">
        <div>
          <Activity aria-hidden="true" />
          <strong>Activity</strong>
        </div>
        <Button
          variant="ghost"
          size="icon"
          aria-label="关闭 Activity"
          onClick={onClose}
        >
          <X />
        </Button>
      </header>

      <div className="ai-activity-scroll">
        <section className="ai-active-agent" aria-label="正在使用的 Agent">
          <span className="ai-active-agent-icon">
            <Bot aria-hidden="true" />
          </span>
          <div>
            <span>正在使用</span>
            <strong>{preset?.name ?? "本地学习 Agent"}</strong>
            <p>{preset?.description ?? "仅访问已同步到本机的学习数据。"}</p>
          </div>
        </section>

        <div className="ai-activity-section-title">
          <span>检索轨迹</span>
          {traces.length > 0 && <small>{traces.length} 轮</small>}
        </div>

        {busy && (
          <article className="ai-trace-card is-running" role="status">
            <header>
              <LoaderCircle className="animate-spin" aria-hidden="true" />
              <div>
                <strong>Agent 正在检索</strong>
                <span>正在规划并调用本地只读工具</span>
              </div>
            </header>
          </article>
        )}

        {!busy && newestFirst.length === 0 && (
          <div className="ai-activity-empty">
            <span>
              <Search aria-hidden="true" />
            </span>
            <strong>还没有检索活动</strong>
            <p>
              Agent
              的工具调用会显示在这里，包括检索阶段、状态、命中数量与安全摘要。
            </p>
          </div>
        )}

        <div className="ai-trace-list">
          {newestFirst.map((trace, index) => (
            <article className="ai-trace-card" key={trace.id}>
              <header>
                <span
                  className={`ai-trace-index ${trace.status === "failed" ? "is-failed" : ""}`}
                >
                  {newestFirst.length - index}
                </span>
                <div>
                  <strong>第 {newestFirst.length - index} 轮</strong>
                  <span>
                    {traceStatusLabels[trace.status] ?? trace.status} ·{" "}
                    {trace.steps} 步 · {trace.tool_runs.length} 次调用
                  </span>
                </div>
              </header>
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
            </article>
          ))}
        </div>
      </div>
    </aside>
  );
}
