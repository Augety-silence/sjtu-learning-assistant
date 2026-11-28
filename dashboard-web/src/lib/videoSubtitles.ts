type SubtitleCue = { startMs: number; endMs: number; text: string };

function parseVttTime(value: string) {
  const parts = value.trim().replace(",", ".").split(":");
  if (parts.length < 2 || parts.length > 3) return null;
  const seconds = Number(parts.pop());
  const minutes = Number(parts.pop());
  const hours = parts.length ? Number(parts.pop()) : 0;
  if (![hours, minutes, seconds].every(Number.isFinite)) return null;
  return Math.round(((hours * 60 + minutes) * 60 + seconds) * 1000);
}

export function parseVttCues(value: string | null) {
  if (!value) return [] as SubtitleCue[];
  return value
    .replace(/^WEBVTT[^\n]*\n+/i, "")
    .split(/\n\s*\n/)
    .flatMap((block) => {
      const lines = block.trim().split(/\r?\n/);
      const timingIndex = lines.findIndex((line) => line.includes("-->"));
      if (timingIndex < 0) return [];
      const [start, end] = lines[timingIndex].split("-->");
      const startMs = parseVttTime(start);
      const endMs = parseVttTime(end.trim().split(/\s+/)[0]);
      const text = lines
        .slice(timingIndex + 1)
        .join(" ")
        .replace(/<[^>]*>/g, "")
        .trim();
      return startMs === null || endMs === null || endMs <= startMs || !text
        ? []
        : [{ startMs, endMs, text }];
    });
}
