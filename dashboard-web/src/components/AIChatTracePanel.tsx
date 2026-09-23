import {
  CheckCircle2,
  ChevronRight,
  CircleSlash2,
  LoaderCircle,
  Search,
  X,
} from "lucide-react";
import aiAgentLogo from "@/assets/ai-agent-logo.png";
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

      <div className="ai-activity-scroll">
        <div className="ai-activity-section-title">
          <span>检索轨迹</span>
          <small>
            {traces.length > 0 ? `${traces.length} 轮` : "等待任务"}
          </small>
        </div>

        {busy && (
          <article className="ai-trace-card is-running" role="status">
            <span className="ai-trace-node is-running">
              <LoaderCircle className="animate-spin" aria-hidden="true" />
            </span>
            <header>
              <strong>Agent 正在检索</strong>
              <span>正在规划并调用本地只读工具</span>
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
          {newestFirst.map((trace, index) => {
            const round = newestFirst.length - index;
            const hits = traceHits(trace);
            return (
              <article className="ai-trace-card" key={trace.id}>
                <span
                  className={`ai-trace-node ${trace.status === "failed" ? "is-failed" : ""}`}
                  aria-hidden="true"
                >
                  {trace.status === "failed" ? (
                    <CircleSlash2 />
                  ) : (
                    <CheckCircle2 />
                  )}
                </span>
                <header>
                  <div>
                    <strong>第 {round} 轮</strong>
                    <span>
                      {traceStatusLabels[trace.status] ?? trace.status}
                    </span>
                  </div>
                  <dl>
                    <div>
                      <dt>步骤</dt>
                      <dd>{trace.steps}</dd>
                    </div>
                    <div>
                      <dt>调用</dt>
                      <dd>{trace.tool_runs.length}</dd>
                    </div>
                    <div>
                      <dt>命中</dt>
                      <dd>{hits}</dd>
                    </div>
                  </dl>
                </header>
                {trace.tool_runs.length > 0 ? (
                  <details className="ai-trace-details">
                    <summary>
                      查看执行详情
                      <ChevronRight aria-hidden="true" />
                    </summary>
                    <div className="ai-tool-runs">
                      {trace.tool_runs.map((run, runIndex) => (
                        <TraceRun
                          key={`${trace.id}-${run.tool_name}-${runIndex}`}
                          run={run}
                        />
                      ))}
                    </div>
                  </details>
                ) : (
                  <p className="ai-trace-no-tools">本轮未调用工具。</p>
                )}
              </article>
            );
          })}
        </div>
      </div>
    </aside>
  );
}
