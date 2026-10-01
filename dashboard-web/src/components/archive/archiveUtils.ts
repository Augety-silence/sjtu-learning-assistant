import type { ArchiveEntry, ArchiveJob, ArchiveVersion } from "@/lib/types";

export const archiveStatusLabels: Record<string, string> = {
  archived: "已归档",
  active: "仅本地",
  local_changed: "本地有变更",
  cloud_only: "仅云端",
  local_only: "仅本地",
  legacy: "旧记录",
  pending: "等待处理",
  planned: "已规划",
  uploading: "上传中",
  downloading: "下载中",
  verifying: "校验中",
  completed: "已完成",
  failed: "失败",
  interrupted: "已中断",
  needs_verification: "需要校验",
  needs_reconcile: "需要校验",
  unavailable: "不可用",
  skipped: "已跳过",
  compare: "已对照",
};

export function archiveStatusLabel(status: string) {
  return archiveStatusLabels[status] ?? status;
}

export function archiveStatusTone(status: string) {
  if (["archived", "completed"].includes(status)) return "success";
  if (["failed", "interrupted", "unavailable"].includes(status))
    return "danger";
  if (
    ["local_changed", "needs_verification", "needs_reconcile"].includes(status)
  )
    return "warning";
  if (
    ["uploading", "downloading", "verifying", "pending", "planned"].includes(
      status,
    )
  )
    return "info";
  return "neutral";
}

export function latestVersion(entry: ArchiveEntry): ArchiveVersion | undefined {
  return entry.versions?.reduce<ArchiveVersion | undefined>(
    (latest, version) =>
      !latest || version.version_number > latest.version_number
        ? version
        : latest,
    undefined,
  );
}

export function jobProgress(job: ArchiveJob) {
  if (job.bytes_total <= 0) return null;
  return Math.min(
    100,
    Math.max(0, Math.round((job.bytes_done / job.bytes_total) * 100)),
  );
}

export function formatArchiveMtime(value: number | null | undefined) {
  if (!value) return "未知";
  const date = new Date(value / 1_000_000);
  if (Number.isNaN(date.getTime())) return "未知";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}
