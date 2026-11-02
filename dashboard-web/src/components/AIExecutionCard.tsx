import {
  Activity,
  BookOpenText,
  CalendarClock,
  CheckCircle2,
  ChevronRight,
  CircleSlash2,
  FileSearch,
  Files,
  FileText,
  FolderTree,
  GraduationCap,
  LoaderCircle,
  Mail,
  MailOpen,
  Paperclip,
  Wrench,
} from "lucide-react";
import { type ComponentType, useEffect, useId, useRef, useState } from "react";
import type { AIAgentLiveStatus, AIAgentTrace, AIToolRun } from "@/lib/types";

const toolIcons: Record<string, ComponentType<{ "aria-hidden"?: boolean }>> = {
  list_courses: GraduationCap,
  search_course_files: FileSearch,
  list_course_files: Files,
  get_deadlines: CalendarClock,
  search_messages: Mail,
  get_message_detail: MailOpen,
  get_material_tree: FolderTree,
  search_ai_attachments: Paperclip,
  read_ai_attachment_text: FileText,
  read_course_file_text: BookOpenText,
};

const toolLabels: Record<string, string> = {
  list_courses: "读取课程列表",
  search_course_files: "检索课程文件",
  list_course_files: "读取课程文件",
  get_deadlines: "检索截止日期",
  search_messages: "检索课程消息",
  get_message_detail: "读取消息详情",
  get_material_tree: "读取资料目录",
  search_ai_attachments: "检索 AI 附件",
  read_ai_attachment_text: "读取附件正文",
  read_course_file_text: "读取课件正文",
};

export function toolLabel(toolName: string) {
  return toolLabels[toolName] ?? toolName;
}

const contentStatusLabels: Record<string, string> = {
  unavailable: "文件未下载到本机",
  unsupported: "格式暂不支持读取",
  failed: "正文读取失败",
  out_of_range: "页码超出范围",
  pending: "正文仍在处理",
};

const messageKindLabels: Record<string, string> = {
  all: "全部消息",
  email: "邮件",
  announcement: "公告",
  assignment: "作业",
};

/** 由已脱敏的参数摘要生成一句短提示；不展示原始查询词或路径。 */
export function toolArgumentHint(run: AIToolRun) {
  const args =
    run.arguments_summary && typeof run.arguments_summary === "object"
      ? (run.arguments_summary as Record<string, unknown>)
      : {};
  const parts: string[] = [];
  if (typeof args.start_page === "number")
    parts.push(`从第 ${args.start_page} 页起`);
  if (typeof args.attachment_id === "number")
    parts.push(`附件 #${args.attachment_id}`);
  if (typeof args.hours === "number") {
    parts.push(
      args.hours % 24 === 0
        ? `未来 ${args.hours / 24} 天`
        : `未来 ${args.hours} 小时`,
    );
  }
  if (typeof args.kind === "string" && messageKindLabels[args.kind])
    parts.push(messageKindLabels[args.kind]);
  if (
    args.has_query === true ||
    (typeof args.query_length === "number" && args.query_length > 0)
  )
    parts.push("按关键词");
  if (args.course_filtered === true) parts.push("限定课程");
  if (args.scope === "message") parts.push("本轮附件");
  return parts.join(" · ");
}

/** 把毫秒格式化为“不到 1 秒 / 12 秒 / 1 分 53 秒 / 1 小时 2 分”。 */
export function formatDuration(ms: number | null | undefined) {
  if (typeof ms !== "number" || !Number.isFinite(ms) || ms < 0) return null;
  if (ms < 1000) return "不到 1 秒";
  const totalSeconds = Math.round(ms / 1000);
  if (totalSeconds < 60) return `${totalSeconds} 秒`;
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0)
    return minutes > 0 ? `${hours} 小时 ${minutes} 分` : `${hours} 小时`;
  return seconds > 0 ? `${minutes} 分 ${seconds} 秒` : `${minutes} 分`;
}

/** 单个工具的耗时只在 ≥0.1 秒时显示，保留一位小数。 */
export function formatToolDuration(ms: number | null | undefined) {
  if (typeof ms !== "number" || !Number.isFinite(ms) || ms < 100) return null;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} 秒`;
  return formatDuration(ms);
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
    const contentStatus = summary.content_status;
    if (typeof contentStatus === "string" && contentStatus !== "ready") {
      return contentStatusLabels[contentStatus] ?? "正文不可用";
    }
    if (
      typeof summary.start_page === "number" &&
      typeof summary.end_page === "number"
    ) {
      const unit =
        typeof summary.unit_label === "string" ? summary.unit_label : "页";
      const range =
        summary.start_page === summary.end_page
          ? `第 ${summary.start_page} ${unit}`
          : `第 ${summary.start_page}–${summary.end_page} ${unit}`;
      return typeof summary.unit_count === "number"
        ? `已读${range} / 共 ${summary.unit_count} ${unit}`
        : `已读${range}`;
    }
    if (contentStatus === "ready") return "已读取正文";
    if (summary.status === "omitted") return "结果过长，已省略";
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
  // 个别工具调用被拒绝（参数错误、预检索预算跳过、preset 未授权）后模型仍可完成回答；
  // 整轮状态以后端 trace.status 为准，被拒绝的调用在各自行内单独标注并单独计数。
  return !["completed", "complete", "ok"].includes(trace.status);
}

export function traceRejectedCount(toolRuns: AIToolRun[]) {
  return toolRuns.filter((run) => run.status === "rejected").length;
}

export function TraceRun({ run }: { run: AIToolRun }) {
  const ok = run.status === "ok";
  const [open, setOpen] = useState(false);
  const Icon = toolIcons[run.tool_name] ?? Wrench;
  const hint = toolArgumentHint(run);
  const duration = formatToolDuration(run.duration_ms);
  return (
    <details
      className={ok ? "ai-tool-run" : "ai-tool-run is-rejected"}
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>
        <span
          className={ok ? "ai-tool-icon is-ok" : "ai-tool-icon is-rejected"}
        >
          {ok ? <Icon aria-hidden /> : <CircleSlash2 aria-hidden="true" />}
        </span>
        <span className="ai-tool-run-copy">
          <strong>
            {toolLabel(run.tool_name)}
            {hint && <em className="ai-tool-hint">{hint}</em>}
          </strong>
          <span>
            {toolResultLabel(run)}
            {duration && (
              <span className="ai-tool-duration"> · {duration}</span>
            )}
          </span>
        </span>
        <span
          className={
            run.phase === "prefetch"
              ? "ai-tool-phase is-prefetch"
              : "ai-tool-phase is-model"
          }
        >
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
  if (streaming) return "正在生成回答…";
  if (!status) {
    return hasTools ? "正在规划下一步…" : "请求已发送，正在等待 Agent 返回";
  }
  const waited = waitedMs === null ? 0 : Math.floor(waitedMs / 1000);
  const step = status.step && status.step > 1 ? `（第 ${status.step} 步）` : "";
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
  const open = userOpen ?? running;

  useEffect(() => {
    // 运行中有新步骤时把列表滚到最新一步（列表有最大高度，避免顶走正文）。
    if (!running || !open || runCount === 0) return;
    const list = listRef.current;
    if (list) list.scrollTop = list.scrollHeight;
  }, [running, open, runCount]);

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
          {runs.length > 0 ? (
            <ol className="ai-tool-runs ai-turn-steps" ref={listRef}>
              {runs.map((run, index) => (
                <li key={`${run.tool_name}-${run.phase}-${index}`}>
                  <TraceRun run={run} />
                </li>
              ))}
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
              {trace && trace.steps > 0 && (
                <span>模型请求 {trace.steps} 次</span>
              )}
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
