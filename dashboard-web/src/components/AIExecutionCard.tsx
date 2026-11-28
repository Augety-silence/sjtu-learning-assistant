import {
  Activity,
  CheckCircle2,
  ChevronRight,
  CircleSlash2,
  LoaderCircle,
} from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { ExecutionNote } from "@/components/AIExecutionNote";
import { ToolRunGroup } from "@/components/AIExecutionToolGroup";
import { groupToolRuns } from "@/components/AIExecutionToolGroups";
import {
  formatDuration,
  traceFailed,
  traceRejectedCount,
} from "@/components/AIExecutionTools";
import type { AIAgentLiveStatus, AIAgentTrace, AIToolRun } from "@/lib/types";

export { ExecutionNote } from "@/components/AIExecutionNote";
export { TraceRun } from "@/components/AIExecutionToolCard";
export * from "@/components/AIExecutionTools";

// 不带时区的 ISO 时间（旧版后端从 SQLite 读回的 UTC）按 UTC 解析，
// 否则 Date.parse 会当成本地时间，在 UTC+8 下多算 8 小时。
const ISO_WITHOUT_ZONE = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/;

export function parseStartedAt(
  value: number | string | null | undefined,
  now: number = Date.now(),
) {
  let parsed: number | null = null;
  if (typeof value === "number" && Number.isFinite(value)) parsed = value;
  else if (typeof value === "string" && value.trim()) {
    const text = value.trim();
    const normalized = ISO_WITHOUT_ZONE.test(text)
      ? `${text.replace(" ", "T")}Z`
      : text;
    const time = Date.parse(normalized);
    if (Number.isFinite(time)) parsed = time;
  }
  if (parsed === null) return null;
  // 开始时刻不可能晚于现在（允许 1 分钟时钟抖动）；异常值不显示计时，
  // 不能把错误的时间当成“已用 N 小时”展示。
  if (parsed > now + 60_000) return null;
  return parsed;
}

/** 运行中每秒刷新一次已用时间；页面不可见或已结束时不计时。 */
function useElapsed(startedAt: number | null, active: boolean) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active || startedAt === null) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active, startedAt]);
  if (startedAt === null) return null;
  return Math.max(0, now - startedAt);
}

/** 运行中最后一行的进度文案：区分“请求模型 / 等待较久 / 自动重试 / 生成回答”。 */
export function liveStepText({
  streaming,
  status,
  waitedMs,
  hasTools,
}: {
  streaming: boolean;
  status?: AIAgentLiveStatus | null;
  waitedMs: number | null;
  hasTools: boolean;
}) {
  if (streaming) {
    return status?.state === "continuing" && status.part
      ? `回答较长，正在继续生成第 ${status.part} 段…`
      : "正在生成回答…";
  }
  if (!status) {
    return hasTools ? "正在规划下一步…" : "请求已发送，正在等待 Agent 返回";
  }
  const waited = waitedMs === null ? 0 : Math.floor(waitedMs / 1000);
  const step = status.step && status.step > 1 ? `（第 ${status.step} 步）` : "";
  if (status.state === "output_loop_blocked") return "检测到重复输出，正在纠正";
  if (status.state === "recovering") return "已接收调整，继续执行";
  if (status.state === "retrying") {
    const delay = Math.max(0, Math.ceil(status.delay_seconds ?? 0) - waited);
    const attempt =
      status.attempt && status.max_attempts
        ? `第 ${status.attempt}/${status.max_attempts} 次`
        : "再次";
    const reason = status.reason ?? "模型请求失败";
    return delay > 0
      ? `${reason}，${delay} 秒后${attempt}请求模型…`
      : `${reason}，正在${attempt}请求模型…`;
  }
  const retry =
    status.attempt && status.attempt > 1
      ? `（第 ${status.attempt} 次尝试）`
      : "";
  if (waited >= 30) {
    return `正在等待模型响应${step}${retry} · 已等待 ${formatDuration(waited * 1000)}，响应较慢，超时会自动重试`;
  }
  if (waited >= 5) {
    return `正在等待模型响应${step}${retry} · 已等待 ${waited} 秒`;
  }
  return `正在请求模型${step}${retry}…`;
}

export function turnSummaryText({
  running,
  failed,
  durationMs,
  toolCount,
  rejected,
}: {
  running: boolean;
  failed: boolean;
  durationMs: number | null;
  toolCount: number;
  rejected: number;
}) {
  const duration = formatDuration(durationMs);
  const tools = toolCount > 0 ? `${toolCount} 个工具调用` : "未调用工具";
  const parts: string[] = [];
  if (running) {
    parts.push("正在执行");
    if (duration) parts.push(`已用 ${duration}`);
    if (toolCount > 0) parts.push(tools);
  } else {
    parts.push(
      failed
        ? duration
          ? `未完成 · 执行了 ${duration}`
          : "未完成"
        : duration
          ? `执行了 ${duration}`
          : "已完成",
    );
    parts.push(tools);
  }
  if (rejected > 0) parts.push(`${rejected} 项未执行`);
  return parts.join(" · ");
}

/**
 * 每一轮回复上方的执行详情：运行中默认展开并实时追加工具步骤，
 * 完成后折叠为“执行了 X · N 个工具调用”一行，点击可展开逐步核对。
 */
export function InlineAgentExecution({
  trace,
  toolRuns,
  running = false,
  streaming = false,
  startedAt,
  liveStatus,
  agentName,
  onOpenActivity,
}: {
  trace?: AIAgentTrace | null;
  toolRuns?: AIToolRun[];
  running?: boolean;
  streaming?: boolean;
  startedAt?: number | string | null;
  liveStatus?: AIAgentLiveStatus | null;
  agentName?: string;
  onOpenActivity: () => void;
}) {
  const waitedMs = useElapsed(liveStatus?.received_at ?? null, running);
  const [userOpen, setUserOpen] = useState<boolean | null>(null);
  const detailId = useId();
  const listRef = useRef<HTMLOListElement>(null);
  const started = parseStartedAt(startedAt);
  const elapsed = useElapsed(started, running);
  const runs = trace?.tool_runs ?? toolRuns ?? [];
  const runCount = runs.length;
  const pendingLength = trace?.pending_text?.length ?? 0;
  const open = userOpen ?? running;

  useEffect(() => {
    // 运行中有新步骤时把列表滚到最新一步（列表有最大高度，避免顶走正文）。
    if (!running || !open || (runCount === 0 && pendingLength === 0)) return;
    const list = listRef.current;
    if (list) list.scrollTop = list.scrollHeight;
  }, [running, open, runCount, pendingLength]);

  if (!running && !trace && runs.length === 0) return null;

  const failed =
    !running &&
    Boolean(
      trace
        ? traceFailed(trace)
        : runs.some((run) => run.status === "rejected"),
    );
  const rejected = traceRejectedCount(runs);
  const durationMs = running ? elapsed : (trace?.duration_ms ?? null);
  const summary = turnSummaryText({
    running,
    failed,
    durationMs,
    toolCount: runs.length,
    rejected,
  });
  const stateClass = running
    ? "is-running"
    : failed
      ? "is-failed"
      : "is-complete";
  const liveText = liveStepText({
    streaming,
    status: liveStatus,
    waitedMs,
    hasTools: runs.length > 0,
  });

  return (
    <section
      className={`ai-turn-trace ${stateClass}${open ? " is-open" : ""}`}
      aria-label={`${agentName ?? "Agent"} 本轮执行详情`}
    >
      <button
        type="button"
        className="ai-turn-trace-toggle"
        aria-expanded={open}
        aria-controls={detailId}
        onClick={() => setUserOpen(!open)}
      >
        <span className="ai-turn-trace-status" aria-hidden="true">
          {running ? (
            <LoaderCircle className="animate-spin" />
          ) : failed ? (
            <CircleSlash2 />
          ) : (
            <CheckCircle2 />
          )}
        </span>
        <span className="ai-turn-trace-summary">{summary}</span>
        <ChevronRight className="ai-turn-trace-caret" aria-hidden="true" />
      </button>
      {open && (
        <div className="ai-turn-trace-detail" id={detailId}>
          {runs.length > 0 || trace?.pending_text ? (
            <ol className="ai-tool-runs ai-turn-steps" ref={listRef}>
              {groupToolRuns(runs).map((group) => (
                <li key={group.start}>
                  <ToolRunGroup group={group} />
                </li>
              ))}
              {trace?.pending_text && (
                <li key={`pending-${runCount}`}>
                  <ExecutionNote
                    text={trace.pending_text}
                    streaming={running}
                  />
                </li>
              )}
              {running && (
                <li className="ai-turn-step-live" aria-live="off">
                  <LoaderCircle className="animate-spin" aria-hidden="true" />
                  <span>{liveText}</span>
                </li>
              )}
            </ol>
          ) : running ? (
            <p className="ai-turn-step-live">
              <LoaderCircle className="animate-spin" aria-hidden="true" />
              <span>{liveText}</span>
            </p>
          ) : (
            <p className="ai-trace-no-tools">本轮直接作答，没有调用工具。</p>
          )}
          {!running && (
            <div className="ai-turn-trace-footer">
              {trace && trace.steps > 0 && <span>模型调用记录见 Activity</span>}
              <button type="button" onClick={onOpenActivity}>
                <Activity aria-hidden="true" />在 Activity 查看完整记录
              </button>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
