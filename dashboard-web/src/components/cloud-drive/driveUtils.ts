import {
  File,
  FileArchive,
  FileAudio,
  FileCode,
  FileImage,
  FileSpreadsheet,
  FileText,
  FileVideo,
  Folder,
  type LucideIcon,
  Presentation,
} from "lucide-react";
import { BridgeInvocationError } from "@/lib/api";
import type { DriveItem } from "@/lib/types";

export const ROOT_LABEL = "个人空间";

export function pathKey(path: readonly string[]) {
  return path.join("/");
}

export function itemKey(item: Pick<DriveItem, "path">) {
  return pathKey(item.path);
}

/** 云盘容量使用两位小数，与交大云盘网页端「已使用 233.01GB」保持一致。 */
export function formatCapacity(value: number | null | undefined) {
  if (
    value === null ||
    value === undefined ||
    !Number.isFinite(value) ||
    value < 0
  )
    return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let amount = value;
  let unit = 0;
  while (amount >= 1024 && unit < units.length - 1) {
    amount /= 1024;
    unit += 1;
  }
  return unit === 0 ? `${amount}B` : `${amount.toFixed(2)}${units[unit]}`;
}

export function formatModified(value: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  const parts = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(date);
  const pick = (type: string) =>
    parts.find((part) => part.type === type)?.value ?? "";
  return `${pick("year")}-${pick("month")}-${pick("day")} ${pick("hour")}:${pick("minute")}`;
}

export function extensionOf(name: string) {
  const index = name.lastIndexOf(".");
  return index > 0 ? name.slice(index + 1).toLowerCase() : "";
}

const TYPE_GROUPS: Array<{
  extensions: string[];
  label: string;
  icon: LucideIcon;
  tone: string;
}> = [
  {
    extensions: [
      "png",
      "jpg",
      "jpeg",
      "gif",
      "webp",
      "bmp",
      "svg",
      "heic",
      "tif",
      "tiff",
    ],
    label: "图片",
    icon: FileImage,
    tone: "image",
  },
  {
    extensions: ["mp4", "mov", "mkv", "avi", "wmv", "flv", "webm", "m4v"],
    label: "视频",
    icon: FileVideo,
    tone: "video",
  },
  {
    extensions: ["mp3", "wav", "flac", "aac", "m4a", "ogg"],
    label: "音频",
    icon: FileAudio,
    tone: "audio",
  },
  {
    extensions: ["zip", "rar", "7z", "tar", "gz", "bz2", "xz", "tgz"],
    label: "压缩包",
    icon: FileArchive,
    tone: "archive",
  },
  {
    extensions: ["xls", "xlsx", "csv", "numbers", "tsv"],
    label: "表格",
    icon: FileSpreadsheet,
    tone: "sheet",
  },
  {
    extensions: ["ppt", "pptx", "key", "odp"],
    label: "演示文稿",
    icon: Presentation,
    tone: "slides",
  },
  {
    extensions: [
      "pdf",
      "doc",
      "docx",
      "txt",
      "md",
      "rtf",
      "odt",
      "pages",
      "tex",
    ],
    label: "文档",
    icon: FileText,
    tone: "doc",
  },
  {
    extensions: [
      "py",
      "js",
      "ts",
      "tsx",
      "jsx",
      "java",
      "c",
      "cpp",
      "h",
      "hpp",
      "go",
      "rs",
      "json",
      "yaml",
      "yml",
      "html",
      "css",
      "sh",
      "ipynb",
      "m",
      "r",
    ],
    label: "代码",
    icon: FileCode,
    tone: "code",
  },
];

export function describeItem(item: Pick<DriveItem, "name" | "is_directory">) {
  if (item.is_directory)
    return { label: "文件夹", icon: Folder, tone: "folder" } as const;
  const extension = extensionOf(item.name);
  const group = TYPE_GROUPS.find((entry) =>
    entry.extensions.includes(extension),
  );
  if (group) {
    const label = extension === "pdf" ? "PDF 文档" : group.label;
    return { label, icon: group.icon, tone: group.tone };
  }
  return {
    label: extension ? `${extension.toUpperCase()} 文件` : "文件",
    icon: File,
    tone: "file",
  };
}

/** 与后端 validate_item_name 对齐的本地预校验，失败时返回提示文案。 */
export function validateName(raw: string, label = "名称"): string | null {
  const value = raw.normalize("NFC").trim();
  if (!value) return `${label}不能为空。`;
  if (value.length > 255) return `${label}不能超过 255 个字符。`;
  if (value === "." || value === "..") return `${label}不能是 . 或 ..。`;
  if (/[\\/]/.test(value)) return `${label}不能包含 / 或 \\。`;
  if (/[\u0000-\u001f\u007f]/.test(value)) return `${label}不能包含控制字符。`;
  return null;
}

let transferCounter = 0;

/** 生成符合后端 ^[A-Za-z0-9_-]{8,64}$ 约束的传输任务标识。 */
export function newTransferId(prefix: "u" | "d") {
  transferCounter = (transferCounter + 1) % 1_000_000;
  const random = Math.random().toString(36).slice(2, 8).padEnd(6, "0");
  return `${prefix}${Date.now().toString(36)}${transferCounter.toString(36)}${random}`;
}

export function errorCode(reason: unknown) {
  return reason instanceof BridgeInvocationError
    ? reason.code
    : "unknown_error";
}

export function errorMessage(reason: unknown, fallback: string) {
  return reason instanceof Error && reason.message ? reason.message : fallback;
}

export function isSamePath(left: readonly string[], right: readonly string[]) {
  return (
    left.length === right.length &&
    left.every((part, index) => part === right[index])
  );
}

export function isInside(child: readonly string[], parent: readonly string[]) {
  return (
    child.length >= parent.length &&
    parent.every((part, index) => part === child[index])
  );
}
