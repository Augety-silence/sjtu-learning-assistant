import type { CalendarEventItem } from "@/components/CalendarView";

/** Keeps the first occurrence of each event identity while merging sources. */
export function mergeCalendarEvents(
  canvasEvents: CalendarEventItem[],
  courseEvents: CalendarEventItem[],
): CalendarEventItem[] {
  const seen = new Map<string, Set<CalendarEventItem["eventType"]>>();
  const merged: CalendarEventItem[] = [];

  for (const event of [...canvasEvents, ...courseEvents]) {
    let types = seen.get(event.id);
    if (!types) {
      types = new Set();
      seen.set(event.id, types);
    }
    if (types.has(event.eventType)) continue;
    types.add(event.eventType);
    merged.push(event);
  }

  return merged;
}

/** Keeps the first event for each strict ID match, regardless of event type. */
export function uniqueCalendarEventsById(
  events: CalendarEventItem[],
): CalendarEventItem[] {
  const seen = new Set<string>();
  const unique: CalendarEventItem[] = [];

  for (const event of events) {
    if (seen.has(event.id)) continue;
    seen.add(event.id);
    unique.push(event);
  }

  return unique;
}
