import { ChevronLeft, ChevronRight } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  Section,
} from "@/components/States";
import { Button } from "@/components/ui/Button";

export interface CalendarEventItem {
  id: string;
  title: string;
  courseName: string;
  startAt: string;
  endAt?: string | null;
  status?: "upcoming" | "submitted" | "graded" | "overdue";
  url?: string | null;
  location?: string | null;
  periodLabel?: string | null;
  eventType?: "assignment" | "course";
  source?: string;
  canonicalCourseId?: string | null;
}

export interface CalendarViewProps {
  events: CalendarEventItem[];
  month?: Date | string;
  now?: Date | string;
  loading?: boolean;
  error?: string | null;
  permissionDenied?: boolean;
  onRetry?: () => void | Promise<void>;
  onMonthChange?: (month: Date) => void;
  onOpenEvent?: (event: CalendarEventItem) => void;
  embedded?: boolean;
}

const weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];

function asDate(value: Date | string) {
  const date = value instanceof Date ? new Date(value) : new Date(value);
  return Number.isNaN(date.getTime()) ? new Date() : date;
}

function dateKey(date: Date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function startOfDay(value: Date) {
  return new Date(value.getFullYear(), value.getMonth(), value.getDate());
}

function monthGrid(month: Date) {
  const first = new Date(month.getFullYear(), month.getMonth(), 1);
  const mondayOffset = (first.getDay() + 6) % 7;
  const start = new Date(first);
  start.setDate(first.getDate() - mondayOffset);
  return Array.from({ length: 42 }, (_, index) => {
    const date = new Date(start);
    date.setDate(start.getDate() + index);
    return date;
  });
}

function formatEventTime(value: string, includeDate = true) {
  const date = asDate(value);
  return new Intl.DateTimeFormat("zh-CN", {
    ...(includeDate ? { month: "numeric", day: "numeric" } : {}),
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function eventMeta(event: CalendarEventItem) {
  if (event.eventType !== "course") return event.courseName;
  return [
    event.courseName,
    event.location,
    event.periodLabel,
    `${formatEventTime(event.startAt, false)}${event.endAt ? `–${formatEventTime(event.endAt, false)}` : ""}`,
  ]
    .filter(Boolean)
    .join(" · ");
}

export function CalendarView({
  events,
  month,
  now = new Date(),
  loading = false,
  error,
  permissionDenied = false,
  onRetry,
  onMonthChange,
  onOpenEvent,
  embedded = false,
}: CalendarViewProps) {
  const today = startOfDay(asDate(now));
  const initialMonth = month ? asDate(month) : today;
  const [internalMonth, setInternalMonth] = useState(
    () => new Date(initialMonth.getFullYear(), initialMonth.getMonth(), 1),
  );
  const visibleMonth = month ? asDate(month) : internalMonth;
  const [selectedKey, setSelectedKey] = useState(() => dateKey(today));
  const dayRefs = useRef(new Map<string, HTMLButtonElement>());
  const dates = useMemo(() => monthGrid(visibleMonth), [visibleMonth]);
  const eventsByDate = useMemo(() => {
    const grouped = new Map<string, CalendarEventItem[]>();
    for (const event of events) {
      const key = dateKey(asDate(event.startAt));
      grouped.set(key, [...(grouped.get(key) ?? []), event]);
    }
    for (const [key, items] of grouped) {
      grouped.set(
        key,
        items.sort(
          (left, right) =>
            asDate(left.startAt).getTime() - asDate(right.startAt).getTime(),
        ),
      );
    }
    return grouped;
  }, [events]);
  const weekEnd = new Date(today);
  weekEnd.setDate(today.getDate() + 7);
  const reminders = useMemo(
    () =>
      events
        .filter((event) => {
          const at = asDate(event.startAt);
          return at >= today && at < weekEnd;
        })
        .sort(
          (left, right) =>
            asDate(left.startAt).getTime() - asDate(right.startAt).getTime(),
        ),
    [events, today.getTime(), weekEnd.getTime()],
  );
  const selectedEvents = eventsByDate.get(selectedKey) ?? [];

  const moveMonth = (offset: number) => {
    const next = new Date(
      visibleMonth.getFullYear(),
      visibleMonth.getMonth() + offset,
      1,
    );
    if (!month) setInternalMonth(next);
    setSelectedKey(dateKey(next));
    onMonthChange?.(next);
  };

  const focusDate = (current: Date, offset: number) => {
    const next = new Date(current);
    next.setDate(current.getDate() + offset);
    const nextKey = dateKey(next);
    setSelectedKey(nextKey);
    if (
      next.getMonth() !== visibleMonth.getMonth() ||
      next.getFullYear() !== visibleMonth.getFullYear()
    ) {
      const nextMonth = new Date(next.getFullYear(), next.getMonth(), 1);
      if (!month) setInternalMonth(nextMonth);
      onMonthChange?.(nextMonth);
    }
    window.setTimeout(() => dayRefs.current.get(nextKey)?.focus(), 0);
  };

  if (permissionDenied) {
    return (
      <EmptyState
        title="没有日历访问权限"
        description="当前账号无法读取课程日历，请检查 Canvas 令牌或课程权限。"
        action={
          onRetry ? (
            <Button variant="outline" onClick={() => void onRetry()}>
              重新检查
            </Button>
          ) : undefined
        }
      />
    );
  }
  if (error) return <ErrorState message={error} retry={onRetry} />;
  if (loading) return <LoadingState label="正在加载课程日历…" />;

  return (
    <div className="section-stack">
      <div className={embedded ? "message-toolbar" : "view-intro"}>
        {!embedded && (
          <div>
            <h2>日历与 DDL</h2>
            <p>按月查看课程事项，并优先处理未来七日内的截止任务。</p>
          </div>
        )}
        <div className="message-toolbar">
          <Button
            variant="outline"
            size="sm"
            aria-label="上个月"
            onClick={() => moveMonth(-1)}
          >
            <ChevronLeft aria-hidden="true" />
            上个月
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              const next = new Date(today.getFullYear(), today.getMonth(), 1);
              if (!month) setInternalMonth(next);
              setSelectedKey(dateKey(today));
              onMonthChange?.(next);
            }}
          >
            回到本月
          </Button>
          <Button
            variant="outline"
            size="sm"
            aria-label="下个月"
            onClick={() => moveMonth(1)}
          >
            下个月
            <ChevronRight aria-hidden="true" />
          </Button>
        </div>
      </div>

      <dl className="summary-strip" aria-label="日历概览">
        <div className="summary-item summary-secondary">
          <dt>本月事项</dt>
          <dd>
            {
              events.filter((event) => {
                const at = asDate(event.startAt);
                return (
                  at.getMonth() === visibleMonth.getMonth() &&
                  at.getFullYear() === visibleMonth.getFullYear()
                );
              }).length
            }
          </dd>
          <span>课程日历</span>
        </div>
        <div className="summary-item summary-urgent">
          <dt>七日提醒</dt>
          <dd>{reminders.length}</dd>
          <span>即将到期</span>
        </div>
        <div className="summary-item summary-priority">
          <dt>所选日期</dt>
          <dd>{selectedEvents.length}</dd>
          <span>{selectedKey}</span>
        </div>
      </dl>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,2fr)_minmax(260px,0.7fr)]">
        <Section
          title={`${visibleMonth.getFullYear()} 年 ${visibleMonth.getMonth() + 1} 月`}
        >
          <div
            className="table-surface p-2"
            role="grid"
            aria-label={`${visibleMonth.getFullYear()} 年 ${visibleMonth.getMonth() + 1} 月日历`}
          >
            <div
              className="grid grid-cols-7 border-b border-neutral-divider"
              role="row"
            >
              {weekdays.map((label) => (
                <span
                  className="p-2 text-center text-xs text-caption"
                  role="columnheader"
                  key={label}
                >
                  {label}
                </span>
              ))}
            </div>
            <div className="grid grid-cols-7">
              {dates.map((date) => {
                const key = dateKey(date);
                const items = eventsByDate.get(key) ?? [];
                const outside = date.getMonth() !== visibleMonth.getMonth();
                const selected = key === selectedKey;
                return (
                  <button
                    key={key}
                    ref={(node) => {
                      if (node) dayRefs.current.set(key, node);
                      else dayRefs.current.delete(key);
                    }}
                    type="button"
                    role="gridcell"
                    aria-selected={selected}
                    aria-label={`${key}，${items.length} 个事项`}
                    tabIndex={selected ? 0 : -1}
                    className={`list-row-button min-h-24 border-b border-r border-neutral-divider p-2 text-left align-top focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary ${selected ? "finder-row-selected" : ""} ${outside ? "text-muted" : ""}`}
                    onClick={() => setSelectedKey(key)}
                    onKeyDown={(event) => {
                      const offsets: Record<string, number> = {
                        ArrowLeft: -1,
                        ArrowRight: 1,
                        ArrowUp: -7,
                        ArrowDown: 7,
                      };
                      const offset = offsets[event.key];
                      if (offset) {
                        event.preventDefault();
                        focusDate(date, offset);
                      }
                    }}
                  >
                    <strong className="text-sm font-medium">
                      {date.getDate()}
                    </strong>
                    <span className="mt-1 grid gap-1">
                      {items.slice(0, 2).map((item) => (
                        <span
                          className={`calendar-event-chip ${item.eventType === "course" ? "calendar-event-course" : "calendar-event-assignment"}`}
                          key={`${item.eventType ?? "assignment"}:${item.id}`}
                        >
                          {item.eventType === "course" && (
                            <span
                              className="calendar-event-dot"
                              aria-hidden="true"
                            />
                          )}
                          <span className="truncate">{item.title}</span>
                        </span>
                      ))}
                      {items.length > 2 && (
                        <span className="text-xs text-caption">
                          另有 {items.length - 2} 项
                        </span>
                      )}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
        </Section>

        <div className="grid content-start gap-6">
          <Section title="未来七日">
            {reminders.length === 0 ? (
              <EmptyState
                title="七日内暂无日程"
                description="当前没有课程或需要优先处理的截止任务。"
              />
            ) : (
              <div className="list-surface">
                {reminders.map((event) => (
                  <button
                    type="button"
                    className={`list-row list-row-button calendar-event-row ${event.eventType === "course" ? "calendar-course-row" : ""}`}
                    key={event.id}
                    onClick={() => onOpenEvent?.(event)}
                    disabled={!onOpenEvent}
                  >
                    <div className="min-w-0">
                      <p className="truncate font-medium">{event.title}</p>
                      <span className="calendar-event-meta">
                        {eventMeta(event)}
                      </span>
                    </div>
                    <span className="row-time">
                      {formatEventTime(event.startAt)}
                    </span>
                  </button>
                ))}
              </div>
            )}
          </Section>
          <Section title={`${selectedKey} 事项`}>
            {selectedEvents.length === 0 ? (
              <EmptyState
                title="当天没有事项"
                description="选择其他日期继续查看。"
              />
            ) : (
              <div className="list-surface">
                {selectedEvents.map((event) => (
                  <button
                    type="button"
                    className={`list-row list-row-button calendar-event-row ${event.eventType === "course" ? "calendar-course-row" : ""}`}
                    key={event.id}
                    onClick={() => onOpenEvent?.(event)}
                    disabled={!onOpenEvent}
                  >
                    <div className="min-w-0">
                      <p className="truncate font-medium">{event.title}</p>
                      <span className="calendar-event-meta">
                        {eventMeta(event)}
                      </span>
                    </div>
                    <span className="row-time">
                      {formatEventTime(event.startAt)}
                    </span>
                  </button>
                ))}
              </div>
            )}
          </Section>
        </div>
      </div>
    </div>
  );
}
