import { useEffect, useState } from "react";
import type { CalendarEventItem } from "@/components/CalendarView";
import type { CalendarEventDto } from "@/lib/types";
export type PersonalEvent = CalendarEventItem & {
  personal: true;
  description?: string;
};
const KEY = "sjtu-v2-personal-events";
export function dateKey(date: Date | string) {
  const d = typeof date === "string" ? new Date(date) : date;
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
export function readPersonalEvents(): PersonalEvent[] {
  try {
    const data = JSON.parse(localStorage.getItem(KEY) || "[]");
    return Array.isArray(data)
      ? data.filter(
          (e) =>
            e &&
            typeof e === "object" &&
            e.personal === true &&
            typeof e.id === "string" &&
            typeof e.title === "string" &&
            typeof e.startAt === "string" &&
            !Number.isNaN(new Date(e.startAt).getTime()) &&
            (!e.endAt ||
              (typeof e.endAt === "string" &&
                !Number.isNaN(new Date(e.endAt).getTime()))),
        )
      : [];
  } catch {
    return [];
  }
}
export function usePersonalEvents() {
  const [events, setEvents] = useState(readPersonalEvents);
  useEffect(() => {
    const load = () => setEvents(readPersonalEvents());
    window.addEventListener("sjtu-v2-personal-events", load);
    return () => window.removeEventListener("sjtu-v2-personal-events", load);
  }, []);
  const save = (next: PersonalEvent[]) => {
    localStorage.setItem(KEY, JSON.stringify(next));
    setEvents(next);
    window.dispatchEvent(new Event("sjtu-v2-personal-events"));
  };
  return [events, save] as const;
}
export function calendarEvent(
  event: CalendarEventDto,
  index: number,
): CalendarEventItem | null {
  const assignment = event.assignment ?? {};
  const start = event.start_at ?? event.end_at ?? assignment.due_at;
  if (typeof start !== "string" || Number.isNaN(new Date(start).getTime()))
    return null;
  const course = /^course_(\d+)$/.exec(event.context_code ?? "");
  return {
    id: String(event.id ?? assignment.id ?? index),
    title: event.title ?? String(assignment.name ?? "日程"),
    startAt: start,
    endAt: event.end_at,
    deadlineAt:
      typeof assignment.due_at === "string" ? assignment.due_at : start,
    allDay: event.all_day === true,
    courseName: event.context_name ?? "Canvas",
    eventType: "assignment",
    source: "canvas",
    assignmentId: Number(assignment.id) || undefined,
    assignmentCourseId: Number(course?.[1]) || undefined,
  };
}
