export function formatDateTime(value: string | null): string {
  if (!value) return "时间未知";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间未知";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

export function formatSize(value: number | null): string {
  if (value === null) return "未知";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

export function deadlineDistance(value: string | null): string {
  if (!value) return "截止时间未知";
  const milliseconds = new Date(value).getTime() - Date.now();
  if (Number.isNaN(milliseconds)) return "截止时间未知";
  const hours = Math.max(0, Math.ceil(milliseconds / 3_600_000));
  if (hours < 24) return `${hours} 小时后截止`;
  return `${Math.ceil(hours / 24)} 天后截止`;
}
