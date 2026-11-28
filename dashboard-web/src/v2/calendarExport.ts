import { invoke } from "@/lib/api";
import type { PersonalEvent } from "./calendarData";

const encoder = new TextEncoder();
function escapeText(value: string) {
  return value
    .replace(/\\/g, "\\\\")
    .replace(/\r\n|\r|\n/g, "\\n")
    .replace(/;/g, "\\;")
    .replace(/,/g, "\\,");
}
function fold(line: string) {
  let result = "";
  let size = 0;
  for (const character of line) {
    const bytes = encoder.encode(character).length;
    if (size + bytes > 75) {
      result += "\r\n ";
      size = 1;
    }
    result += character;
    size += bytes;
  }
  return result;
}
function utc(date: Date) {
  return date
    .toISOString()
    .replace(/[-:]/g, "")
    .replace(/\.\d{3}/, "");
}
function civil(date: Date) {
  return `${date.getFullYear()}${String(date.getMonth() + 1).padStart(2, "0")}${String(date.getDate()).padStart(2, "0")}`;
}

// ICS is a snapshot for explicit import, never a claim of two-way sync.
export function personalCalendarICS(events: PersonalEvent[], now = new Date()) {
  const lines = [
    "BEGIN:VCALENDAR",
    "VERSION:2.0",
    "PRODID:-//SJTU Learning Assistant//2.0.0//ZH",
    "CALSCALE:GREGORIAN",
  ];
  for (const event of events) {
    const start = new Date(event.startAt);
    const end = event.endAt
      ? new Date(event.endAt)
      : event.allDay
        ? new Date(start.getFullYear(), start.getMonth(), start.getDate() + 1)
        : new Date(start.getTime() + 30 * 60 * 1000);
    if (
      !Number.isFinite(start.getTime()) ||
      !Number.isFinite(end.getTime()) ||
      end <= start
    )
      throw new Error("日程日期无效，请修正后再导出。");
    lines.push(
      "BEGIN:VEVENT",
      `UID:${encodeURIComponent(event.id)}@sjtu-learning-assistant.local`,
      `DTSTAMP:${utc(now)}`,
      `SUMMARY:${escapeText(event.title)}`,
      event.allDay
        ? `DTSTART;VALUE=DATE:${civil(start)}`
        : `DTSTART:${utc(start)}`,
      event.allDay ? `DTEND;VALUE=DATE:${civil(end)}` : `DTEND:${utc(end)}`,
    );
    if (event.location) lines.push(`LOCATION:${escapeText(event.location)}`);
    if (event.description)
      lines.push(`DESCRIPTION:${escapeText(event.description)}`);
    lines.push("END:VEVENT");
  }
  lines.push("END:VCALENDAR");
  return `${lines.map(fold).join("\r\n")}\r\n`;
}

export async function downloadPersonalCalendar(events: PersonalEvent[]) {
  return invoke<{ filename: string; destination: "downloads" }>(
    "personal_calendar_export_ics",
    { content: personalCalendarICS(events) },
  );
}
