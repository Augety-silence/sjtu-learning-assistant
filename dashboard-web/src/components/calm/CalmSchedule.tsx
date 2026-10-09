import { ChevronLeft, ChevronRight, Sparkles } from "lucide-react";
import { motion, useReducedMotion } from "motion/react";
import {
  type KeyboardEvent as ReactKeyboardEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { CalendarEventItem } from "@/components/CalendarView";
import "./calm-schedule.css";

export interface CalmScheduleProps {
  events: CalendarEventItem[];
  month: Date;
  now?: Date;
  onMonthChange?: (month: Date) => void;
  onOpenEvent?: (event: CalendarEventItem) => void;
  onAskAI?: (prompt: string) => void;
}

const WEEKDAY_LONG = ["日", "一", "二", "三", "四", "五", "六"];

function toDate(value: Date | string): Date {
  const date = value instanceof Date ? new Date(value) : new Date(value);
  return Number.isNaN(date.getTime()) ? new Date() : date;
}

function startOfDay(value: Date): Date {
  return new Date(value.getFullYear(), value.getMonth(), value.getDate());
}

function dateKey(value: Date): string {
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${value.getFullYear()}-${month}-${day}`;
}

function dayDiff(later: Date, earlier: Date): number {
  const ms = startOfDay(later).getTime() - startOfDay(earlier).getTime();
  return Math.round(ms / 86_400_000);
}

function hhmm(value: Date): string {
  return `${String(value.getHours()).padStart(2, "0")}:${String(
    value.getMinutes(),
  ).padStart(2, "0")}`;
}

function weekdayAt(value: Date): string {
  return `周${WEEKDAY_LONG[value.getDay()]}`;
}

function buildMonthGrid(month: Date): Date[] {
  const first = new Date(month.getFullYear(), month.getMonth(), 1);
  const offset = (first.getDay() + 6) % 7;
  const start = new Date(first);
  start.setDate(first.getDate() - offset);
  return Array.from({ length: 42 }, (_, index) => {
    const date = new Date(start);
    date.setDate(start.getDate() + index);
    return date;
  });
}

type DueTone = "overdue" | "soon" | "normal" | "done";

interface DueInfo {
  text: string;
  tone: DueTone;
}

function dueInfo(event: CalendarEventItem, now: Date): DueInfo {
  if (event.status === "graded") {
    return { text: "已出分", tone: "done" };
  }
  if (event.status === "submitted") {
    return { text: "已提交", tone: "done" };
  }
  const at = toDate(event.startAt);
  const ms = at.getTime() - now.getTime();
  if (ms < 0) {
    const late = dayDiff(now, at);
    return {
      text: late >= 1 ? `逾期 ${late} 天` : "已过截止",
      tone: "overdue",
    };
  }
  const diff = dayDiff(at, now);
  const time = hhmm(at);
  if (diff === 0) return { text: `今天 ${time}`, tone: "soon" };
  if (diff === 1) return { text: `明天 ${time}`, tone: "soon" };
  if (diff <= 6) return { text: `${weekdayAt(at)} ${time}`, tone: "normal" };
  return {
    text: `${at.getMonth() + 1}月${at.getDate()}日 ${time}`,
    tone: "normal",
  };
}

function toneClass(tone: DueTone): string {
  if (tone === "overdue") return "tone-danger";
  if (tone === "soon") return "tone-warm";
  if (tone === "done") return "tone-done";
  return "";
}

function isAssignment(event: CalendarEventItem): boolean {
  return event.eventType !== "course";
}

function buildPlanningPrompt(events: CalendarEventItem[], now: Date): string {
  const horizon = startOfDay(now);
  horizon.setDate(horizon.getDate() + 7);
  const items = events
    .filter((event) => {
      if (event.status === "submitted" || event.status === "graded") {
        return false;
      }
      const at = toDate(event.startAt).getTime();
      return at >= startOfDay(now).getTime() && at < horizon.getTime();
    })
    .sort(
      (left, right) =>
        toDate(left.startAt).getTime() - toDate(right.startAt).getTime(),
    );
  if (items.length === 0) {
    return [
      "我本周的日程列表是空的，没有检测到课程或作业截止。",
      "请帮我制定一份通用的本周学习计划，并告诉我如何把课程日程导入应用。",
    ].join("\n");
  }
  const lines = items.map((event) => {
    const at = toDate(event.startAt);
    const kind = event.eventType === "course" ? "课程" : "截止";
    return `- ${at.getMonth() + 1}月${at.getDate()}日 ${weekdayAt(at)} ${hhmm(
      at,
    )} ${kind} 《${event.title}》（${event.courseName}）`;
  });
  return [
    "这是我未来 7 天的课程日程与作业截止：",
    ...lines,
    "",
    "请帮我：",
    "1. 按紧急程度排序，指出最该优先处理的事项；",
    "2. 检查这些安排之间是否有时间冲突；",
    "3. 给出本周每天的学习时间安排建议。",
  ].join("\n");
}

export function CalmSchedule({
  events,
  month,
  now: nowProp,
  onMonthChange,
  onOpenEvent,
  onAskAI,
}: CalmScheduleProps) {
  const reduceMotion = useReducedMotion();
  const [internalNow, setInternalNow] = useState(() => new Date());
  useEffect(() => {
    if (nowProp) return;
    const timer = window.setInterval(() => setInternalNow(new Date()), 15_000);
    return () => window.clearInterval(timer);
  }, [nowProp]);
  const now = nowProp ?? internalNow;
  const visibleMonth = month;
  const [selectedKey, setSelectedKey] = useState(() =>
    dateKey(startOfDay(now)),
  );
  const [direction, setDirection] = useState(1);
  const dayRefs = useRef(new Map<string, HTMLButtonElement>());

  const dates = useMemo(() => buildMonthGrid(visibleMonth), [visibleMonth]);
  const todayKey = dateKey(startOfDay(now));
  const monthKey = `${visibleMonth.getFullYear()}-${visibleMonth.getMonth()}`;

  const eventsByDate = useMemo(() => {
    const grouped = new Map<string, CalendarEventItem[]>();
    for (const event of events) {
      const key = dateKey(startOfDay(toDate(event.startAt)));
      grouped.set(key, [...(grouped.get(key) ?? []), event]);
    }
    for (const [key, items] of grouped) {
      grouped.set(
        key,
        [...items].sort(
          (left, right) =>
            toDate(left.startAt).getTime() - toDate(right.startAt).getTime(),
        ),
      );
    }
    return grouped;
  }, [events]);

  const marksByDate = useMemo(() => {
    const marks = new Map<string, { event: boolean; deadline: boolean }>();
    for (const event of events) {
      const key = dateKey(startOfDay(toDate(event.startAt)));
      const current = marks.get(key) ?? { event: false, deadline: false };
      if (isAssignment(event)) current.deadline = true;
      else current.event = true;
      marks.set(key, current);
    }
    return marks;
  }, [events]);

  const dueCount = useMemo(() => {
    const end = startOfDay(now);
    end.setDate(end.getDate() + 7);
    return events.filter((event) => {
      if (!isAssignment(event)) return false;
      if (event.status === "submitted" || event.status === "graded") {
        return false;
      }
      const at = toDate(event.startAt).getTime();
      return at >= startOfDay(now).getTime() && at < end.getTime();
    }).length;
  }, [events, now]);

  const selectedDate =
    dates.find((date) => dateKey(date) === selectedKey) ?? startOfDay(now);
  const selectedIndex = dates.findIndex(
    (date) => dateKey(date) === selectedKey,
  );
  const selectedEvents = eventsByDate.get(selectedKey) ?? [];
  const selectedIsToday = selectedKey === todayKey;

  const monthIsCurrent =
    visibleMonth.getFullYear() === now.getFullYear() &&
    visibleMonth.getMonth() === now.getMonth();
  const showTodayEntry = !(monthIsCurrent && selectedIsToday);

  const moveToMonth = useCallback(
    (next: Date, nextSelected: Date, nextDirection: number) => {
      setDirection(nextDirection);
      setSelectedKey(dateKey(startOfDay(nextSelected)));
      if (
        next.getFullYear() !== visibleMonth.getFullYear() ||
        next.getMonth() !== visibleMonth.getMonth()
      ) {
        onMonthChange?.(new Date(next.getFullYear(), next.getMonth(), 1));
      }
    },
    [onMonthChange, visibleMonth],
  );

  const selectDay = useCallback(
    (date: Date) => {
      const nextDirection = date >= selectedDate ? 1 : -1;
      moveToMonth(
        date,
        date,
        date.getMonth() === visibleMonth.getMonth() &&
          date.getFullYear() === visibleMonth.getFullYear()
          ? nextDirection
          : date > visibleMonth
            ? 1
            : -1,
      );
    },
    [moveToMonth, selectedDate, visibleMonth],
  );

  const shiftMonth = useCallback(
    (offset: number) => {
      const next = new Date(
        visibleMonth.getFullYear(),
        visibleMonth.getMonth() + offset,
        1,
      );
      moveToMonth(next, next, offset > 0 ? 1 : -1);
    },
    [moveToMonth, visibleMonth],
  );

  const backToToday = useCallback(() => {
    const today = startOfDay(now);
    moveToMonth(today, today, today > visibleMonth ? 1 : -1);
  }, [moveToMonth, now, visibleMonth]);

  const handleDayKeyDown = (
    event: ReactKeyboardEvent<HTMLButtonElement>,
    date: Date,
  ) => {
    const step = {
      ArrowLeft: -1,
      ArrowRight: 1,
      ArrowUp: -7,
      ArrowDown: 7,
    }[event.key];
    if (!step) return;
    event.preventDefault();
    const next = startOfDay(date);
    next.setDate(next.getDate() + step);
    moveToMonth(next, next, step > 0 ? 1 : -1);
    window.setTimeout(() => {
      dayRefs.current.get(dateKey(next))?.focus();
    }, 0);
  };

  const askAI = useCallback(() => {
    onAskAI?.(buildPlanningPrompt(events, now));
  }, [events, now, onAskAI]);

  const heroTime = hhmm(now);
  const heroSuffix = now.getHours() >= 12 ? "下午" : "上午";

  return (
    <div className="calm-schedule">
      <section className="calm-hero" aria-label="今日概览">
        <div className="calm-hero-clock" aria-hidden="true">
          <span>{heroTime}</span>
          <span className="calm-hero-suffix">{heroSuffix}</span>
        </div>
        <div className="calm-hero-date">
          <strong>
            {now.getMonth() + 1}月{now.getDate()}日，{weekdayAt(now)}
          </strong>
          <p className="calm-hero-count">
            7 天内<em>{dueCount}</em>项要交
          </p>
        </div>
        <button type="button" className="calm-ai-btn" onClick={askAI}>
          <Sparkles size={15} aria-hidden="true" />
          AI 本周规划
        </button>
      </section>

      <div className="calm-body">
        <div className="calm-month-col">
          <div className="calm-month-head">
            <h2>
              {visibleMonth.getFullYear()}年{visibleMonth.getMonth() + 1}月
            </h2>
            <div className="calm-month-nav">
              {showTodayEntry ? (
                <button
                  type="button"
                  className="calm-today-pill"
                  onClick={backToToday}
                >
                  今天
                </button>
              ) : null}
              <button
                type="button"
                className="calm-icon-btn"
                aria-label="上个月"
                onClick={() => shiftMonth(-1)}
              >
                <ChevronLeft size={17} aria-hidden="true" />
              </button>
              <button
                type="button"
                className="calm-icon-btn"
                aria-label="下个月"
                onClick={() => shiftMonth(1)}
              >
                <ChevronRight size={17} aria-hidden="true" />
              </button>
            </div>
          </div>

          <div className="calm-weekdays" aria-hidden="true">
            {["一", "二", "三", "四", "五", "六", "日"].map((day) => (
              <span key={day}>周{day}</span>
            ))}
          </div>
          <div className="calm-grid-viewport">
            <motion.div
              key={monthKey}
              className="calm-grid"
              initial={
                reduceMotion ? false : { opacity: 0.2, x: direction * 18 }
              }
              animate={{ opacity: 1, x: 0 }}
              transition={{
                duration: 0.3,
                ease: [0.22, 0.8, 0.22, 1],
              }}
              role="grid"
              aria-label="月历"
            >
              {selectedIndex >= 0 ? (
                <span
                  className="calm-selection"
                  style={{
                    transform: `translate3d(${
                      (selectedIndex % 7) * 100
                    }%, ${Math.floor(selectedIndex / 7) * 100}%, 0)`,
                  }}
                  aria-hidden="true"
                >
                  <span />
                </span>
              ) : null}
              {dates.map((date) => {
                const key = dateKey(date);
                const outside = date.getMonth() !== visibleMonth.getMonth();
                const marks = marksByDate.get(key);
                const classNames = [
                  "calm-day",
                  outside ? "is-outside" : "",
                  key === todayKey ? "is-today" : "",
                  key === selectedKey ? "is-selected" : "",
                ]
                  .filter(Boolean)
                  .join(" ");
                return (
                  <button
                    key={key}
                    type="button"
                    ref={(node) => {
                      if (node) dayRefs.current.set(key, node);
                      else dayRefs.current.delete(key);
                    }}
                    className={classNames}
                    aria-label={`${date.getMonth() + 1}月${date.getDate()}日${
                      key === todayKey ? "（今天）" : ""
                    }`}
                    aria-current={key === todayKey ? "date" : undefined}
                    aria-pressed={key === selectedKey}
                    onClick={() => selectDay(date)}
                    onKeyDown={(event) => handleDayKeyDown(event, date)}
                  >
                    {date.getDate()}
                    {marks ? (
                      <span className="calm-day-marks">
                        {marks.event ? <i className="event" /> : null}
                        {marks.deadline ? <i className="deadline" /> : null}
                      </span>
                    ) : null}
                  </button>
                );
              })}
            </motion.div>
          </div>
        </div>

        <div className="calm-agenda-col">
          <div className="calm-agenda-head">
            <span className="calm-agenda-title">
              {selectedDate.getMonth() + 1}月{selectedDate.getDate()}日{" "}
              {weekdayAt(selectedDate)}
              {selectedIsToday ? (
                <span className="is-today-tag"> · 今天</span>
              ) : null}
            </span>
            <span className="calm-agenda-count">
              {selectedEvents.length} 项
            </span>
          </div>

          <motion.div
            key={selectedKey}
            className="calm-agenda-list"
            initial={reduceMotion ? false : { opacity: 0.3, x: direction * 10 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ duration: 0.26, ease: [0.22, 0.8, 0.22, 1] }}
          >
            {selectedEvents.map((event) => {
              const due = dueInfo(event, now);
              const barClass = [
                "calm-item-bar",
                due.tone === "done"
                  ? "is-done"
                  : due.tone === "overdue"
                    ? "is-overdue"
                    : isAssignment(event)
                      ? "is-deadline"
                      : "",
              ]
                .filter(Boolean)
                .join(" ");
              const openable = Boolean(onOpenEvent);
              return (
                <div
                  key={`${event.eventType}-${event.id}`}
                  className={`calm-agenda-item${
                    openable ? " is-openable" : ""
                  }`}
                  role={openable ? "button" : undefined}
                  tabIndex={openable ? 0 : undefined}
                  onClick={() => onOpenEvent?.(event)}
                  onKeyDown={(keyEvent) => {
                    if (keyEvent.key === "Enter") onOpenEvent?.(event);
                  }}
                >
                  <span className="calm-item-time">
                    {hhmm(toDate(event.startAt))}
                  </span>
                  <span className={barClass} aria-hidden="true" />
                  <span className="calm-item-body">
                    <span className="calm-item-title">{event.title}</span>
                    {isAssignment(event) ? (
                      <span className="calm-item-meta">
                        {event.courseName} · 截止 ·{" "}
                        <span className={toneClass(due.tone)}>{due.text}</span>
                      </span>
                    ) : (
                      <span className="calm-item-meta">
                        {[
                          event.courseName,
                          event.periodLabel,
                          event.location,
                          event.endAt
                            ? `${hhmm(toDate(event.startAt))}–${hhmm(
                                toDate(event.endAt),
                              )}`
                            : null,
                        ]
                          .filter(Boolean)
                          .join(" · ")}
                      </span>
                    )}
                  </span>
                </div>
              );
            })}
            {selectedEvents.length === 0 ? (
              <p className="calm-agenda-empty">这一天没有安排。</p>
            ) : null}
          </motion.div>
        </div>
      </div>
    </div>
  );
}
