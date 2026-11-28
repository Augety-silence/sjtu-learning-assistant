import { Sparkles } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import type { AssignmentTarget } from "@/components/AssignmentsView";
import type { CalendarEventItem } from "@/components/CalendarView";
import { getCalendar, getTimetableSchedule, invoke } from "@/lib/api";
import { deadlineDistance, formatDateTime } from "@/lib/format";
import type { Deadline, OverviewData, ViewName } from "@/lib/types";
import { calendarEvent, dateKey, usePersonalEvents } from "./calendarData";
import { V2Button, V2Section } from "./primitives";
export function V2Overview({
  navigate,
  onAssignment,
  onAskAI,
}: {
  navigate: (view: ViewName) => void;
  onAssignment?: (target: AssignmentTarget) => void;
  onAskAI?: (prompt: string) => void;
}) {
  const [data, setData] = useState<OverviewData | null>(null),
    [error, setError] = useState("");
  const [schedule, setSchedule] = useState<CalendarEventItem[]>([]),
    [scheduleError, setScheduleError] = useState(""),
    [scheduleLoading, setScheduleLoading] = useState(true);
  const [personal] = usePersonalEvents();
  const load = useCallback(async () => {
    setError("");
    try {
      setData(await invoke<OverviewData>("overview"));
    } catch (e) {
      setError(e instanceof Error ? e.message : "概览读取失败");
    }
  }, []);
  const loadSchedule = useCallback(async () => {
    setScheduleError("");
    setScheduleLoading(true);
    const now = new Date(),
      start = new Date(now.getFullYear(), now.getMonth(), now.getDate()),
      end = new Date(start.getTime() + 86400000);
    const results = await Promise.allSettled([
      getCalendar(now.getFullYear(), now.getMonth() + 1),
      getTimetableSchedule(start.toISOString(), end.toISOString()),
    ]);
    const collected: CalendarEventItem[] = [];
    for (const r of results) {
      if (r.status === "fulfilled" && r.value && typeof r.value === "object") {
        if ("events" in r.value)
          collected.push(
            ...r.value.events.map((e) => ({
              ...e,
              eventType: "course" as const,
            })),
          );
        else
          collected.push(
            ...r.value.month_events
              .map(calendarEvent)
              .filter((e): e is CalendarEventItem => e !== null),
          );
      } else setScheduleError("部分日程未能读取");
    }
    setSchedule(collected);
    setScheduleLoading(false);
  }, []);
  useEffect(() => {
    void load();
    void loadSchedule();
  }, [load, loadSchedule]);
  const today = [...schedule, ...personal]
    .filter((e) => dateKey(e.startAt) === dateKey(new Date()))
    .sort((a, b) => a.startAt.localeCompare(b.startAt));
  const deadlines = [...(data?.deadlines ?? [])].sort((a, b) =>
    (a.due_at ?? "9999").localeCompare(b.due_at ?? "9999"),
  );
  const urgent = deadlines.filter(
    (d) =>
      d.due_at &&
      new Date(d.due_at).getTime() >= Date.now() &&
      new Date(d.due_at).getTime() <= Date.now() + 86400000,
  );
  const openAssignment = (item: Deadline) => {
    const match = /\/courses\/(\d+)\/assignments\/(\d+)/.exec(item.url ?? "");
    if (match && onAssignment)
      onAssignment({
        courseId: Number(match[1]),
        assignmentId: Number(match[2]),
      });
    else navigate("assignments");
  };
  if (error)
    return (
      <div className="v2-state" role="alert">
        <p>{error}</p>
        <V2Button onClick={() => void load()}>重试</V2Button>
      </div>
    );
  if (!data)
    return (
      <div
        className="v2-overview-skeleton"
        role="status"
        aria-label="正在汇总学习信息"
      >
        <div />
        <div />
        <div />
      </div>
    );
  return (
    <div className="v2-overview">
      <div className="v2-summary" aria-label="学习概览">
        <button type="button" onClick={() => navigate("assignments")}>
          <span>24 小时内截止</span>
          <strong>
            {urgent.length === deadlines.length && deadlines.length >= 5
              ? `${urgent.length}+`
              : urgent.length}
          </strong>
          <small>
            {urgent[0]
              ? `${urgent[0].title} · ${formatDateTime(urgent[0].due_at)}`
              : "24 小时内暂无截止事项"}
          </small>
        </button>
        <button type="button" onClick={() => navigate("messages")}>
          <span>未读邮件</span>
          <strong>{data.unread_emails}</strong>
          <small>
            {data.unread_emails ? "课程邮件与通知等待查看" : "邮件已全部阅读"}
          </small>
        </button>
        <button type="button" onClick={() => navigate("calendar")}>
          <span>今日日程</span>
          <strong>
            {scheduleLoading
              ? "…"
              : scheduleError && today.length === 0
                ? "—"
                : today.length}
          </strong>
          <small>课表 · Canvas · 个人日程</small>
        </button>
      </div>
      <div className="v2-overview-grid">
        <V2Section
          title={
            <button type="button" onClick={() => navigate("assignments")}>
              最近截止
            </button>
          }
          action={
            <V2Button onClick={() => navigate("assignments")}>
              查看全部
            </V2Button>
          }
        >
          <div>
            {deadlines.length ? (
              deadlines.map((item) => (
                <button
                  className="v2-content-row"
                  type="button"
                  key={item.source_id}
                  onClick={() => openAssignment(item)}
                >
                  <div>
                    <strong>{item.title}</strong>
                    <small>{item.course}</small>
                  </div>
                  <div className="v2-row-end">
                    <span className={urgent.includes(item) ? "v2-urgent" : ""}>
                      {formatDateTime(item.due_at)}
                    </span>
                    <small>{deadlineDistance(item.due_at)}</small>
                  </div>
                </button>
              ))
            ) : (
              <div className="v2-empty">暂无待截止事项</div>
            )}
          </div>
        </V2Section>
        <V2Section
          title={
            <button type="button" onClick={() => navigate("messages")}>
              最新消息
            </button>
          }
          action={
            <V2Button onClick={() => navigate("messages")}>查看全部</V2Button>
          }
        >
          <div>
            {data.messages.length ? (
              data.messages.slice(0, 4).map((item) => (
                <button
                  type="button"
                  className="v2-content-row"
                  key={`${item.kind}:${item.source_id}`}
                  aria-label={`打开消息详情：${item.title}`}
                  onClick={() => {
                    navigate("messages");
                    window.location.hash = `/messages?kind=${item.kind}&source_id=${encodeURIComponent(item.source_id)}`;
                  }}
                >
                  <div>
                    <span className="v2-badge">
                      {item.kind === "email"
                        ? "邮件"
                        : item.kind === "assignment"
                          ? "Canvas 作业"
                          : "Canvas 公告"}
                    </span>
                    <strong>{item.title}</strong>
                    <small>
                      {item.source_label} · {formatDateTime(item.occurred_at)}
                    </small>
                  </div>
                </button>
              ))
            ) : (
              <div className="v2-empty">暂无最新消息</div>
            )}
          </div>
        </V2Section>
        <aside className="v2-overview-rail">
          <V2Section
            title={
              <button type="button" onClick={() => navigate("calendar")}>
                今日日程
              </button>
            }
          >
            <div
              className="v2-agenda-scroll"
              tabIndex={0}
              aria-label="今日日程，可上下滚动"
            >
              {today.map((e) => (
                <button
                  type="button"
                  className="v2-agenda-item"
                  key={`${e.eventType}:${e.id}`}
                  onClick={() => {
                    try {
                      sessionStorage.setItem("sjtu-v2-selected-event", e.id);
                    } catch {}
                    navigate("calendar");
                  }}
                >
                  <time>
                    {new Date(e.startAt).toLocaleTimeString("zh-CN", {
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                    {e.endAt
                      ? `–${new Date(e.endAt).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}`
                      : ""}
                  </time>
                  <strong>{e.title}</strong>
                  <small>
                    {e.location || e.courseName} ·{" "}
                    {"personal" in e
                      ? "个人日程"
                      : e.eventType === "course"
                        ? "课表"
                        : "Canvas"}
                  </small>
                </button>
              ))}
              {!today.length && (
                <div className="v2-empty">
                  {scheduleError || "今天暂无日程"}
                  {scheduleError && (
                    <V2Button onClick={() => void loadSchedule()}>
                      重试
                    </V2Button>
                  )}
                </div>
              )}
            </div>
          </V2Section>
          <V2Section title="本周学习安排">
            <div className="v2-planning">
              <p>结合截止事项与完整日程，整理任务优先级。</p>
              <V2Button
                onClick={() =>
                  onAskAI?.(
                    "根据本周课表、个人日程和待交作业，总结待办，估算任务耗时并寻找空档。",
                  )
                }
              >
                <Sparkles size={15} />
                交给 AI 整理
              </V2Button>
            </div>
          </V2Section>
        </aside>
      </div>
    </div>
  );
}
