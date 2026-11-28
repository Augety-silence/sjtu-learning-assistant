import {
  BookOpenText,
  CalendarClock,
  FilePen,
  FileSearch,
  Files,
  FileText,
  FolderOpen,
  FolderTree,
  GraduationCap,
  Mail,
  MailOpen,
  Paperclip,
  Wrench,
} from "lucide-react";
import type { ComponentType } from "react";
import type { AIAgentTrace, AIToolRun } from "@/lib/types";

export const toolIcons: Record<
  string,
  ComponentType<{ "aria-hidden"?: boolean }>
> = {
  list_compiled_knowledge: Files,
  browse_knowledge_wiki: FolderTree,
  get_knowledge_status: FileSearch,
  search_compiled_knowledge: FileSearch,
  read_compiled_knowledge: BookOpenText,
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
  list_workspace_files: FolderOpen,
  read_workspace_file: FileText,
  propose_file_change: FilePen,
};

const toolLabels: Record<string, string> = {
  native_calendar_status: "检查日历连接",
  native_calendar_calendars: "读取系统日历列表",
  native_calendar_events: "读取 Mac 日程",
  native_calendar_get: "核对日程详情",
  native_calendar_save: "保存 Mac 日程",
  native_calendar_delete: "删除 Mac 日程",
  list_compiled_knowledge: "列出编译知识库",
  browse_knowledge_wiki: "浏览课程知识地图",
  get_knowledge_status: "读取知识库进度",
  search_compiled_knowledge: "检索编译知识点",
  read_compiled_knowledge: "读取编译知识正文",
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
  list_workspace_files: "浏览工作区",
  read_workspace_file: "读取工作区文件",
  propose_file_change: "生成文件修改",
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
  const modeLabels: Record<string, string> = {
    create: "新建",
    overwrite: "整体重写",
    replace: "局部替换",
    append: "追加",
  };
  if (typeof args.mode === "string" && modeLabels[args.mode])
    parts.push(modeLabels[args.mode]);
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
    if (typeof summary.returned_chars === "number") {
      return typeof summary.total_chars === "number"
        ? `已读 ${summary.returned_chars} 字符 / 全文 ${summary.total_chars} 字符`
        : `已读 ${summary.returned_chars} 字符`;
    }
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
    if (typeof summary.change_id === "string") {
      const added = typeof summary.added === "number" ? summary.added : 0;
      const removed = typeof summary.removed === "number" ? summary.removed : 0;
      return `待你确认 · +${added} −${removed} 行`;
    }
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
