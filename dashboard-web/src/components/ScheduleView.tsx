import { Upload } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { type CalendarEventItem } from "@/components/CalendarView";
import { CalmSchedule } from "@/components/calm/CalmSchedule";
import { Button } from "@/components/ui/Button";
import {
  getTimetableSchedule,
  getTimetableStatus,
  importTimetableIcs,
} from "@/lib/api";
import type { TimetableStatus } from "@/lib/types";

const CACHE_KEY = "sjtu-learning-timetable-cache-v1";

interface ScheduleViewProps {
  canvasEvents: CalendarEventItem[];
  canvasLoading?: boolean;
  canvasError?: string | null;
  month: Date;
  onMonthChange: (month: Date) => void;
  onRetryCanvas?: () => void | Promise<void>;
  onOpenCanvasEvent?: (event: CalendarEventItem) => void;
  onAskAI?: (prompt: string) => void;
}

function messageFrom(reason: unknown, fallback: string) {
  return reason instanceof Error ? reason.message : fallback;
}

function visibleCalendarRange(month: Date) {
  const first = new Date(month.getFullYear(), month.getMonth(), 1);
  const start = new Date(first);
  start.setDate(first.getDate() - ((first.getDay() + 6) % 7));
  start.setHours(0, 0, 0, 0);
  const end = new Date(start);
  end.setDate(start.getDate() + 42);
  return { startAt: start.toISOString(), endAt: end.toISOString() };
}

export function ScheduleView({
  canvasEvents,
  canvasLoading = false,
  canvasError,
  month,
  onMonthChange,
  onRetryCanvas,
  onOpenCanvasEvent,
  onAskAI,
}: ScheduleViewProps) {
  const [status, setStatus] = useState<TimetableStatus | null>(null);
  const [courseEvents, setCourseEvents] = useState<CalendarEventItem[]>([]);
  const [offline, setOffline] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    setLoadError(null);
    const range = visibleCalendarRange(month);
    try {
      const [nextStatus, schedule] = await Promise.all([
        getTimetableStatus(),
        getTimetableSchedule(range.startAt, range.endAt),
      ]);
      const nextEvents: CalendarEventItem[] = schedule.events.map((event) => ({
        ...event,
        eventType: "course",
      }));
      setStatus(nextStatus);
      setCourseEvents(nextEvents);
      setOffline(false);
      localStorage.setItem(
        CACHE_KEY,
        JSON.stringify({ status: nextStatus, events: nextEvents, range }),
      );
    } catch (reason) {
      const cached = localStorage.getItem(CACHE_KEY);
      if (cached) {
        try {
          const parsed = JSON.parse(cached) as {
            status: TimetableStatus;
            events: CalendarEventItem[];
            range?: { startAt: string; endAt: string };
          };
          if (
            parsed.range?.startAt !== range.startAt ||
            parsed.range?.endAt !== range.endAt
          ) {
            throw new Error("缓存月份不匹配");
          }
          setStatus({
            ...parsed.status,
            state: "offline",
            message: "当前离线，显示上次保存在本机的课表。",
            hasLocalData: true,
          });
          setCourseEvents(parsed.events);
          setOffline(true);
        } catch {
          localStorage.removeItem(CACHE_KEY);
          setLoadError(messageFrom(reason, "课表加载失败"));
        }
      } else {
        setLoadError(messageFrom(reason, "课表加载失败"));
      }
    }
  }, [month]);

  useEffect(() => void load(), [load]);

  const importIcs = async () => {
    setImporting(true);
    setNotice("");
    try {
      const result = await importTimetableIcs();
      if ("cancelled" in result) return;
      setNotice(
        `已导入 ${result.importedCourses} 门课程、${result.importedSessions} 节课${result.updatedSessions ? `，更新 ${result.updatedSessions} 节` : ""}。`,
      );
      await load();
    } catch (reason) {
      setNotice(messageFrom(reason, "ICS 导入失败，本地课表未更改。"));
    } finally {
      setImporting(false);
    }
  };

  const hasCourseData = Boolean(status?.hasLocalData || courseEvents.length);
  const allEvents = [...canvasEvents, ...courseEvents].filter(
    (event, index, items) =>
      items.findIndex(
        (candidate) =>
          candidate.id === event.id && candidate.eventType === event.eventType,
      ) === index,
  );
  const calendarError = allEvents.length === 0 ? canvasError : null;
  const displayError = loadError || calendarError;
  const statusLabel = hasCourseData
    ? offline
      ? "离线日历"
      : "本地日历"
    : status?.state === "awaiting_configuration"
      ? "等待开放平台配置"
      : "日历";
  const liveStatus = displayError
    ? displayError
    : canvasLoading
      ? "正在同步 Canvas 日历…"
      : notice;

  return (
    <div className="section-stack schedule-workspace calm-schedule">
      <div className="calm-utility-row">
        <div className="calm-utility-meta">
          <span className="calm-utility-label">{statusLabel}</span>
          <span className="calm-utility-live" aria-live="polite">
            {liveStatus}
          </span>
          {displayError ? (
            <button
              type="button"
              className="calm-utility-retry"
              onClick={() => void (calendarError ? onRetryCanvas?.() : load())}
            >
              重试
            </button>
          ) : null}
        </div>
        <Button
          variant="outline"
          size="sm"
          className="calm-ics-button"
          onClick={() => void importIcs()}
          loading={importing}
          loadingLabel="导入中…"
        >
          <Upload aria-hidden="true" />
          {hasCourseData ? "重新导入 ICS" : "导入 ICS 日历"}
        </Button>
      </div>
      <CalmSchedule
        events={allEvents}
        month={month}
        onMonthChange={onMonthChange}
        onOpenEvent={(event) =>
          event.eventType !== "course" ? onOpenCanvasEvent?.(event) : undefined
        }
        onAskAI={onAskAI}
      />
    </div>
  );
}
