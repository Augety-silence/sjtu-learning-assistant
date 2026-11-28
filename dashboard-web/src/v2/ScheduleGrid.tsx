import { Plus } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { CalendarEventItem } from "@/components/CalendarView";
import { dateKey } from "./calendarData";

export type ScheduleMode = "day" | "week" | "month";
export const HOUR_HEIGHT = 64;
export function deadlineLabel(event: CalendarEventItem) {
  if (event.eventType !== "assignment") return null;
  const date = new Date(event.deadlineAt || event.startAt);
  const minutes = date.getHours() * 60 + date.getMinutes();
  return minutes === 0 ? "24:00 截止" : minutes === 1439 ? "23:59 截止" : null;
}
export function deadlineDay(event: CalendarEventItem) {
  const date = new Date(event.deadlineAt || event.startAt);
  if (date.getHours() === 0 && date.getMinutes() === 0)
    date.setDate(date.getDate() - 1);
  return date;
}
export function initialScrollMinutes(date: Date) {
  const minutes = date.getHours() * 60 + date.getMinutes();
  if (minutes < 180 || minutes >= 1380) return null;
  return Math.max(0, Math.floor((minutes - 150) / 30) * 30);
}
export function isAllDay(event: CalendarEventItem) {
  return (
    deadlineLabel(event) !== null ||
    event.allDay === true ||
    ("native" in event &&
      Boolean((event.native as { all_day?: boolean }).all_day))
  );
}
export function onDay(event: CalendarEventItem, day: Date) {
  if (deadlineLabel(event)) return dateKey(deadlineDay(event)) === dateKey(day);
  const begin = new Date(day.getFullYear(), day.getMonth(), day.getDate());
  const next = new Date(day.getFullYear(), day.getMonth(), day.getDate() + 1);
  const start = new Date(event.startAt);
  const end = new Date(event.endAt || event.startAt);
  return start < next && (end > begin || dateKey(start) === dateKey(begin));
}
function stamp(minutes: number) {
  return `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
}
function ended(event: CalendarEventItem) {
  return new Date(event.endAt || event.startAt).getTime() < Date.now();
}
function daySegments(events: CalendarEventItem[], day: Date) {
  const begin = new Date(
    day.getFullYear(),
    day.getMonth(),
    day.getDate(),
  ).getTime();
  const parts = events
    .filter((e) => !isAllDay(e) && onDay(e, day))
    .map((event) => {
      const start = Math.max(
        0,
        (new Date(event.startAt).getTime() - begin) / 60000,
      );
      const end = Math.min(
        1440,
        Math.max(
          start + 30,
          (new Date(event.endAt || event.startAt).getTime() - begin) / 60000,
        ),
      );
      return {
        event,
        start,
        end,
        lane: 0,
        lanes: 1,
        summary: false,
        count: 0,
        groupStart: 0,
        groupEnd: 0,
      };
    })
    .sort((a, b) => a.start - b.start || b.end - a.end);
  let group: typeof parts = [];
  let groupEnd = -1;
  const finish = () => {
    const count = Math.max(1, ...group.map((p) => p.lane + 1));
    for (const part of group) {
      part.lanes = count;
      part.count = group.length;
      part.groupStart = Math.min(...group.map((p) => p.start));
      part.groupEnd = Math.max(...group.map((p) => p.end));
    }
    if (count > 1) group[0].summary = true;
    group = [];
  };
  for (const part of parts) {
    if (part.start >= groupEnd) finish();
    const occupied = group.filter((p) => p.end > part.start).map((p) => p.lane);
    while (occupied.includes(part.lane)) part.lane++;
    group.push(part);
    groupEnd = Math.max(...group.map((p) => p.end));
  }
  finish();
  return parts;
}
export function ScheduleGrid({
  mode,
  days,
  monthDays,
  events,
  selected,
  onChoose,
  onCreate,
  onOpen,
  onOpenDay,
}: {
  mode: ScheduleMode;
  days: Date[];
  monthDays: Date[];
  events: CalendarEventItem[];
  selected: string;
  onChoose: (day: Date) => void;
  onCreate: (day: Date, minutes: number, allDay?: boolean) => void;
  onOpen: (event: CalendarEventItem) => void;
  onOpenDay: (day: Date) => void;
}) {
  const scroll = useRef<HTMLDivElement>(null);
  const allDayRef = useRef<HTMLDivElement>(null);
  const [gutters, setGutters] = useState({ time: 0, allDay: 0 });
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const tick = window.setInterval(() => setNow(new Date()), 60000);
    return () => window.clearInterval(tick);
  }, []);
  const periodKey = days.map(dateKey).join("|");
  useLayoutEffect(() => {
    if (mode === "month") return;
    const minutes = initialScrollMinutes(new Date());
    if (minutes !== null && scroll.current)
      scroll.current.scrollTop = (minutes / 60) * HOUR_HEIGHT;
  }, [mode, periodKey]);
  useLayoutEffect(() => {
    if (mode === "month") return;
    const measure = () => {
      const time = scroll.current
        ? scroll.current.offsetWidth - scroll.current.clientWidth
        : 0;
      const allDay = allDayRef.current
        ? allDayRef.current.offsetWidth - allDayRef.current.clientWidth
        : 0;
      setGutters((previous) =>
        previous.time === time && previous.allDay === allDay
          ? previous
          : { time, allDay },
      );
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    if (scroll.current) observer.observe(scroll.current);
    if (allDayRef.current) observer.observe(allDayRef.current);
    return () => observer.disconnect();
  }, [mode]);
  if (mode === "month")
    return (
      <section
        className="v2-calendar-main v2-calendar-month"
        aria-label="月视图"
      >
        <div className="v2-month-weekdays">
          {["一", "二", "三", "四", "五", "六", "日"].map((d) => (
            <span key={d}>周{d}</span>
          ))}
        </div>
        <div className="v2-full-month-scroll">
          <div className="v2-full-month-grid">
            {monthDays.map((day) => (
              <div className="v2-full-month-day" key={dateKey(day)}>
                <button
                  className="v2-month-date"
                  type="button"
                  aria-pressed={dateKey(day) === selected}
                  onClick={() => onChoose(day)}
                >
                  {day.getDate()}
                </button>
                {events
                  .filter((e) => onDay(e, day))
                  .map((e) => (
                    <button
                      type="button"
                      key={`${e.source}:${e.id}`}
                      className={`v2-calendar-event ${ended(e) ? "is-past" : ""}`}
                      onClick={() => onOpen(e)}
                    >
                      {!isAllDay(e) && (
                        <time>
                          {new Date(e.startAt).toLocaleTimeString("zh-CN", {
                            hour: "2-digit",
                            minute: "2-digit",
                          })}
                        </time>
                      )}
                      <strong>{e.title}</strong>
                      {deadlineLabel(e) && <time>{deadlineLabel(e)}</time>}
                    </button>
                  ))}
                <button
                  type="button"
                  className="v2-month-create"
                  aria-label={`在${dateKey(day)}创建日程`}
                  onClick={() => onCreate(day, 9 * 60)}
                >
                  <Plus size={13} />
                </button>
              </div>
            ))}
          </div>
        </div>
      </section>
    );
  return (
    <section
      className={`v2-calendar-main v2-calendar-${mode}`}
      aria-label={mode === "day" ? "日视图" : "周视图"}
      style={
        {
          "--calendar-days": days.length,
          "--calendar-gutter": `${gutters.time}px`,
          "--calendar-allday-gutter": `${Math.max(0, gutters.time - gutters.allDay)}px`,
        } as React.CSSProperties
      }
    >
      <div className="v2-time-heading">
        <span>GMT+8</span>
        {days.map((day) => (
          <button
            className="v2-day-heading"
            type="button"
            key={dateKey(day)}
            aria-pressed={dateKey(day) === selected}
            onClick={() => onChoose(day)}
          >
            <small>
              周{["日", "一", "二", "三", "四", "五", "六"][day.getDay()]}
            </small>
            <strong
              className={dateKey(day) === dateKey(new Date()) ? "is-today" : ""}
            >
              {day.getDate()}
            </strong>
          </button>
        ))}
      </div>
      <div className="v2-all-day-band" ref={allDayRef} aria-label="全天日程">
        <span>全天</span>
        {days.map((day) => (
          <div key={dateKey(day)} className="v2-all-day-cell">
            {events
              .filter((e) => isAllDay(e) && onDay(e, day))
              .map((e) => (
                <button
                  type="button"
                  className={`v2-calendar-event ${ended(e) ? "is-past" : ""}`}
                  key={`${e.source}:${e.id}`}
                  onClick={() => onOpen(e)}
                >
                  <strong>{e.title}</strong>
                  {deadlineLabel(e) && <time>{deadlineLabel(e)}</time>}
                </button>
              ))}
            <button
              type="button"
              className="v2-all-day-create"
              aria-label={`在${dateKey(day)}创建全天日程`}
              onClick={() => onCreate(day, 0, true)}
            >
              添加
            </button>
          </div>
        ))}
      </div>
      <div
        className="v2-time-scroll"
        ref={scroll}
        tabIndex={0}
        aria-label="00:00至24:00时间网格，可上下滚动"
      >
        <div className="v2-time-grid">
          <div className="v2-time-axis">
            {Array.from({ length: 25 }, (_, h) => (
              <time key={h} style={{ top: h * HOUR_HEIGHT }}>
                {stamp(h * 60)}
              </time>
            ))}
          </div>
          {days.map((day) => (
            <div className="v2-time-day" key={dateKey(day)}>
              {Array.from({ length: 48 }, (_, slot) => (
                <button
                  type="button"
                  key={slot}
                  className="v2-time-slot"
                  aria-label={`${dateKey(day)} ${stamp(slot * 30)} 创建日程`}
                  onClick={() => onCreate(day, slot * 30)}
                />
              ))}
              {daySegments(events, day).map(
                ({ event: e, start, end, lane, lanes }) => (
                  <button
                    type="button"
                    key={`${e.source}:${e.id}`}
                    className={`v2-calendar-event v2-timed-event ${lanes > 1 ? "is-conflicting" : ""} ${e.eventType || "personal"} ${ended(e) ? "is-past" : ""}`}
                    title={`${e.title} · ${stamp(Math.floor(start))}–${stamp(Math.floor(end))}`}
                    style={{
                      top: (start / 60) * HOUR_HEIGHT,
                      height: Math.max(30, ((end - start) / 60) * HOUR_HEIGHT),
                      left: `calc(${(lane / lanes) * 100}% + 3px)`,
                      width: `calc(${100 / lanes}% - 6px)`,
                    }}
                    onClick={() => onOpen(e)}
                  >
                    <strong>{e.title}</strong>
                    <time>
                      {stamp(Math.floor(start))} – {stamp(Math.floor(end))}
                    </time>
                    {end - start >= 60 && (
                      <small>{e.location || e.courseName}</small>
                    )}
                  </button>
                ),
              )}
              {mode === "week" &&
                daySegments(events, day)
                  .filter((p) => p.summary)
                  .map((p) => (
                    <button
                      type="button"
                      key={`conflict-${p.event.id}`}
                      className={`v2-calendar-event v2-timed-event v2-conflict-event ${new Date(day.getFullYear(), day.getMonth(), day.getDate(), 0, p.groupEnd).getTime() < now.getTime() ? "is-past" : ""}`}
                      aria-label={`${dateKey(day)} ${stamp(Math.floor(p.groupStart))}至${stamp(Math.floor(p.groupEnd))}，${p.count}项安排，查看日视图`}
                      style={{
                        top: (p.groupStart / 60) * HOUR_HEIGHT,
                        height: Math.max(
                          40,
                          ((p.groupEnd - p.groupStart) / 60) * HOUR_HEIGHT,
                        ),
                        left: 3,
                        right: 3,
                      }}
                      onClick={() => onOpenDay(day)}
                    >
                      <strong>{p.count} 项安排</strong>
                      <time>
                        {stamp(Math.floor(p.groupStart))}–
                        {stamp(Math.floor(p.groupEnd))}
                      </time>
                    </button>
                  ))}
              {dateKey(day) === dateKey(new Date()) && (
                <div
                  className="v2-now-line"
                  style={{
                    top: (now.getHours() + now.getMinutes() / 60) * HOUR_HEIGHT,
                  }}
                  aria-label="当前时间"
                />
              )}
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
