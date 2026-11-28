import {
  ChevronLeft,
  ChevronRight,
  Plus,
  Sparkles,
  Upload,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CalendarEventItem } from "@/components/CalendarView";
import { dateKey, type PersonalEvent, usePersonalEvents } from "./calendarData";
import { downloadPersonalCalendar } from "./calendarExport";
import { V2DatePicker } from "./DatePicker";
import {
  asPersonal,
  NativeCalendarConnection,
  type NativePersonalEvent,
  useNativeCalendar,
} from "./nativeCalendar";
import { V2Button, V2Dialog, V2Tabs } from "./primitives";
import {
  deadlineDay,
  deadlineLabel,
  onDay,
  ScheduleGrid,
  type ScheduleMode,
} from "./ScheduleGrid";

type Event = CalendarEventItem | PersonalEvent;
const dayNames = ["一", "二", "三", "四", "五", "六", "日"];
function shifted(d: Date, days: number) {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate() + days);
}
function monday(d: Date) {
  return shifted(d, -((d.getDay() + 6) % 7));
}
function occursOn(event: Event, date: Date) {
  return onDay(event, date);
}
function time(s: string) {
  return new Date(s).toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  });
}
export function V2Schedule({
  events,
  month,
  onMonthChange,
  onOpenEvent,
  onAskAI,
  onImport,
  importing,
  status,
  error,
  onRetry,
}: {
  events: CalendarEventItem[];
  month: Date;
  onMonthChange: (date: Date) => void;
  onOpenEvent?: (event: CalendarEventItem) => void;
  onAskAI?: (prompt: string) => void;
  onImport: () => void;
  importing: boolean;
  status: string;
  error?: string | null;
  onRetry: () => void;
}) {
  const [personal, save] = usePersonalEvents();
  const nativeCalendar = useNativeCalendar(month);
  const [saving, setSaving] = useState(false);
  const [allDay, setAllDay] = useState(false);
  const [endDate, setEndDate] = useState("");
  const [alarm, setAlarm] = useState("-900");
  const [selected, setSelected] = useState(() => dateKey(new Date()));
  const [mode, setMode] = useState<ScheduleMode>("week");
  const [detail, setDetail] = useState<Event | null>(null);
  const [editing, setEditing] = useState<PersonalEvent | null>(null);
  const [create, setCreate] = useState(false);
  const [formError, setFormError] = useState("");
  const [draft, setDraft] = useState({
    title: "",
    date: selected,
    start: "09:00",
    end: "09:30",
    location: "",
    description: "",
  });
  const createDraft = useRef<{
    draft: typeof draft;
    allDay: boolean;
    endDate: string;
    alarm: string;
  } | null>(null);
  const closeEditor = () => {
    if (saving) return;
    if (!editing) createDraft.current = { draft, allDay, endDate, alarm };
    setCreate(false);
    setEditing(null);
  };
  const all = useMemo(
    () =>
      [...events, ...personal, ...nativeCalendar.events].sort((a, b) =>
        a.startAt.localeCompare(b.startAt),
      ),
    [events, personal, nativeCalendar.events],
  );
  const chosen = new Date(`${selected}T12:00:00`),
    weekStart = monday(chosen);
  const days = Array.from({ length: 7 }, (_, i) => shifted(weekStart, i));
  const choose = (date: Date) => {
    setSelected(dateKey(date));
    if (
      date.getMonth() !== month.getMonth() ||
      date.getFullYear() !== month.getFullYear()
    )
      onMonthChange(new Date(date.getFullYear(), date.getMonth(), 1));
  };
  const openCreate = useCallback(() => {
    const saved = createDraft.current;
    setEditing(null);
    setAllDay(saved?.allDay ?? false);
    setEndDate(saved?.endDate ?? "");
    setAlarm(saved?.alarm ?? "-900");
    setDraft(
      saved?.draft ?? {
        title: "",
        date: selected,
        start: "09:00",
        end: "09:30",
        location: "",
        description: "",
      },
    );
    setFormError("");
    setCreate(true);
  }, [selected]);
  useEffect(() => {
    window.addEventListener("sjtu-v2-create-event", openCreate);
    return () => window.removeEventListener("sjtu-v2-create-event", openCreate);
  }, [openCreate]);
  useEffect(() => {
    const id = sessionStorage.getItem("sjtu-v2-selected-event");
    if (id) {
      const event = all.find((e) => e.id === id);
      if (event) {
        setDetail(event);
        setSelected(dateKey(event.startAt));
        sessionStorage.removeItem("sjtu-v2-selected-event");
      }
    }
  }, [all]);
  const first = new Date(month.getFullYear(), month.getMonth(), 1),
    gridStart = monday(first);
  const calendar = Array.from({ length: 42 }, (_, i) => shifted(gridStart, i));
  const visibleDays = mode === "day" ? [chosen] : days;
  const openAt = (day: Date, minutes: number, fullDay = false) => {
    const start = new Date(
      day.getFullYear(),
      day.getMonth(),
      day.getDate(),
      0,
      minutes,
    );
    const end = new Date(start.getTime() + (fullDay ? 24 * 60 : 30) * 60000);
    setEditing(null);
    setAllDay(fullDay);
    setEndDate(dateKey(end));
    setAlarm("-900");
    setDraft({
      title: "",
      date: dateKey(start),
      start: time(start.toISOString()),
      end: time(end.toISOString()),
      location: "",
      description: "",
    });
    setFormError("");
    setCreate(true);
  };
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const start = new Date(`${draft.date}T${allDay ? "00:00" : draft.start}`),
      end = new Date(
        `${endDate || draft.date}T${allDay ? "00:00" : draft.end}`,
      );
    if (!draft.title.trim() || end <= start) {
      setFormError("请填写标题，并让结束时间晚于开始时间。");
      return;
    }
    const next: PersonalEvent = {
      id:
        editing?.id ??
        `personal-${Date.now()}-${Math.random().toString(36).slice(2)}`,
      personal: true,
      allDay,
      title: draft.title.trim(),
      startAt: start.toISOString(),
      endAt: end.toISOString(),
      courseName: "个人日程",
      source: "personal",
      location: draft.location,
      description: draft.description,
    };
    setSaving(true);
    try {
      let result: PersonalEvent = next;
      const native =
        editing && "native" in editing
          ? (editing as NativePersonalEvent).native
          : undefined;
      const options = {
        allDay,
        alarm: alarm === "none" ? null : Number(alarm),
      };
      if (native) result = await nativeCalendar.save(next, native, options);
      else if (nativeCalendar.ready && editing) {
        result = asPersonal(await nativeCalendar.importLegacy(next, options));
        save(personal.filter((p) => p.id !== editing.id));
        await nativeCalendar.refresh();
      } else if (nativeCalendar.ready)
        result = await nativeCalendar.save(next, undefined, options);
      else save([...personal.filter((p) => p.id !== next.id), next]);
      setCreate(false);
      setEditing(null);
      setDraft({
        title: "",
        date: dateKey(start),
        start: "09:00",
        end: "09:30",
        location: "",
        description: "",
      });
      createDraft.current = null;
      setDetail(result);
      choose(start);
    } catch (reason) {
      setFormError(
        reason instanceof Error
          ? reason.message
          : "日程保存失败，请检查本机存储空间。",
      );
    } finally {
      setSaving(false);
    }
  };
  const viewEvent = (event: Event) => {
    setFormError("");
    setDetail(event);
  };
  return (
    <div className="v2-schedule">
      <div className="v2-schedule-toolbar">
        <div>
          <V2Button
            aria-label={
              mode === "month" ? "上个月" : mode === "day" ? "上一天" : "上一周"
            }
            onClick={() =>
              choose(
                mode === "month"
                  ? new Date(chosen.getFullYear(), chosen.getMonth() - 1, 1)
                  : shifted(chosen, mode === "day" ? -1 : -7),
              )
            }
          >
            <ChevronLeft />
          </V2Button>
          <V2Button
            aria-label={
              mode === "month" ? "下个月" : mode === "day" ? "下一天" : "下一周"
            }
            onClick={() =>
              choose(
                mode === "month"
                  ? new Date(chosen.getFullYear(), chosen.getMonth() + 1, 1)
                  : shifted(chosen, mode === "day" ? 1 : 7),
              )
            }
          >
            <ChevronRight />
          </V2Button>
          <V2Button onClick={() => choose(new Date())}>今天</V2Button>
          <strong>
            {mode === "month"
              ? `${month.getFullYear()}年 ${month.getMonth() + 1}月`
              : mode === "day"
                ? chosen.toLocaleDateString("zh-CN", {
                    month: "long",
                    day: "numeric",
                  })
                : `${days[0].toLocaleDateString("zh-CN", { month: "long", day: "numeric" })} – ${days[6].toLocaleDateString("zh-CN", { month: "long", day: "numeric" })}`}
          </strong>
        </div>
        <div>
          <V2Tabs
            label="日历视图"
            value={mode}
            onChange={setMode}
            items={[
              { id: "day", label: "日" },
              { id: "week", label: "周" },
              { id: "month", label: "月" },
            ]}
          />
          <V2Button disabled={importing} onClick={onImport}>
            <Upload />
            {importing ? "导入中…" : "导入 ICS"}
          </V2Button>
          <V2Button className="primary" onClick={openCreate}>
            <Plus />
            添加日程
          </V2Button>
        </div>
      </div>
      {error && (
        <div className="v2-inline-error" role="alert">
          {error}
          <V2Button onClick={onRetry}>重试</V2Button>
        </div>
      )}
      <div className="v2-schedule-layout">
        <aside className="v2-month">
          <header>
            <V2Button
              aria-label="上个月"
              onClick={() =>
                onMonthChange(
                  new Date(month.getFullYear(), month.getMonth() - 1, 1),
                )
              }
            >
              <ChevronLeft />
            </V2Button>
            <strong>
              {month.getFullYear()}年 {month.getMonth() + 1}月
            </strong>
            <V2Button
              aria-label="下个月"
              onClick={() =>
                onMonthChange(
                  new Date(month.getFullYear(), month.getMonth() + 1, 1),
                )
              }
            >
              <ChevronRight />
            </V2Button>
          </header>
          <div className="v2-month-grid">
            {dayNames.map((day) => (
              <span key={day}>{day}</span>
            ))}
            {calendar.map((date) => (
              <button
                type="button"
                key={dateKey(date)}
                aria-label={dateKey(date)}
                aria-pressed={dateKey(date) === selected}
                className={
                  date.getMonth() === month.getMonth() ? "" : "outside"
                }
                onClick={() => choose(date)}
              >
                {date.getDate()}
                {all.some((e) => occursOn(e, date)) && <i />}
              </button>
            ))}
          </div>
          <NativeCalendarConnection
            calendar={nativeCalendar}
            migrating={saving}
            count={personal.length}
            onExport={() => {
              try {
                downloadPersonalCalendar(personal);
                setFormError("");
              } catch (reason) {
                setFormError(
                  reason instanceof Error ? reason.message : "日程导出失败",
                );
              }
            }}
            onImport={() => {
              void (async () => {
                setSaving(true);
                try {
                  let remaining = [...personal];
                  for (const event of personal) {
                    await nativeCalendar.importLegacy(event);
                    remaining = remaining.filter((p) => p.id !== event.id);
                    save(remaining);
                  }
                  await nativeCalendar.refresh();
                } catch (reason) {
                  setFormError(
                    reason instanceof Error
                      ? reason.message
                      : "迁移失败，已保留未迁移日程",
                  );
                } finally {
                  setSaving(false);
                }
              })();
            }}
          />
          {formError && !create && !detail && <p role="alert">{formError}</p>}
          <V2Button
            onClick={() =>
              onAskAI?.(
                "结合本周课程、个人日程与待交作业，整理本周待办，估算任务耗时并寻找空档。",
              )
            }
          >
            <Sparkles />
            AI 整理本周
          </V2Button>
          <small className="v2-source-status">{status}</small>
          <p>日程来源</p>
          <div className="v2-schedule-sources">
            <span>
              <i className="course" />
              课程表
            </span>
            <span>
              <i className="assignment" />
              Canvas 作业
            </span>
            <span>
              <i className="personal" />
              个人日程
            </span>
          </div>
        </aside>
        <ScheduleGrid
          mode={mode}
          days={visibleDays}
          monthDays={calendar}
          events={all}
          selected={selected}
          onChoose={choose}
          onCreate={openAt}
          onOpen={viewEvent}
          onOpenDay={(day) => {
            choose(day);
            setMode("day");
          }}
        />
      </div>
      {detail && (
        <V2Dialog title="日程详情" onClose={() => setDetail(null)}>
          <div className="v2-event-detail">
            <span className="v2-badge">
              {"native" in detail
                ? "Mac 日历"
                : "personal" in detail
                  ? "个人日程"
                  : detail.eventType === "course"
                    ? "课程表"
                    : "Canvas 作业"}
            </span>
            <h2>{detail.title}</h2>
            {formError && <p role="alert">{formError}</p>}
            <dl>
              <dt>时间</dt>
              <dd>
                {deadlineLabel(detail)
                  ? `${deadlineDay(detail).toLocaleDateString("zh-CN")} ${deadlineLabel(detail)}`
                  : `${formatScheduleTime(detail.startAt)}${detail.endAt ? ` – ${time(detail.endAt)}` : ""}`}
              </dd>
              <dt>地点</dt>
              <dd>{detail.location || "未设置"}</dd>
              <dt>来源</dt>
              <dd>{detail.courseName}</dd>
              {"personal" in detail && detail.description && (
                <>
                  <dt>备注</dt>
                  <dd>{detail.description}</dd>
                </>
              )}
            </dl>
            {"personal" in detail ? (
              <footer>
                <V2Button
                  onClick={() => {
                    const p = detail as PersonalEvent;
                    if (
                      "native" in p &&
                      !(p as NativePersonalEvent).native.writable
                    ) {
                      setFormError("该系统日历只读。");
                      return;
                    }
                    const system =
                      "native" in p
                        ? (p as NativePersonalEvent).native
                        : undefined;
                    setAllDay(system?.all_day ?? p.allDay ?? false);
                    setEndDate(dateKey(p.endAt ?? p.startAt));
                    setAlarm(system?.alarms[0]?.toString() ?? "none");
                    setEditing(p);
                    setDraft({
                      title: p.title,
                      date: dateKey(p.startAt),
                      start: time(p.startAt),
                      end: time(p.endAt ?? p.startAt),
                      location: p.location ?? "",
                      description: p.description ?? "",
                    });
                    setDetail(null);
                    setCreate(true);
                  }}
                >
                  编辑
                </V2Button>
                <V2Button
                  disabled={saving}
                  onClick={async () => {
                    if (window.confirm("删除这个日程？重复日程仅删除本次。")) {
                      setSaving(true);
                      try {
                        if ("native" in detail)
                          await nativeCalendar.remove(
                            (detail as NativePersonalEvent).native,
                          );
                        else save(personal.filter((e) => e.id !== detail.id));
                        setDetail(null);
                      } catch (reason) {
                        setFormError(
                          reason instanceof Error
                            ? reason.message
                            : "日程删除失败，请重试。",
                        );
                      } finally {
                        setSaving(false);
                      }
                    }
                  }}
                >
                  删除
                </V2Button>
              </footer>
            ) : detail.eventType !== "course" ? (
              <V2Button
                onClick={() => {
                  onOpenEvent?.(detail);
                  setDetail(null);
                }}
              >
                打开作业中心
              </V2Button>
            ) : null}
          </div>
        </V2Dialog>
      )}
      {create && (
        <V2Dialog
          title={editing ? "编辑日程" : "创建日程"}
          className="v2-event-dialog"
          onClose={closeEditor}
        >
          <form className="v2-event-form" onSubmit={submit}>
            <label>
              标题
              <input
                value={draft.title}
                required
                maxLength={200}
                onChange={(e) => setDraft({ ...draft, title: e.target.value })}
              />
            </label>
            <div className="v2-event-dates">
              <label>
                日期
                <V2DatePicker
                  label="日期"
                  value={draft.date}
                  onChange={(value) => setDraft({ ...draft, date: value })}
                />
              </label>
              <label>
                结束日期
                <V2DatePicker
                  label="结束日期"
                  value={endDate || draft.date}
                  min={draft.date}
                  onChange={setEndDate}
                />
              </label>
            </div>
            {
              <label className="v2-event-allday">
                <input
                  type="checkbox"
                  checked={allDay}
                  onChange={(e) => {
                    setAllDay(e.target.checked);
                    if (
                      e.target.checked &&
                      (!endDate || endDate === draft.date)
                    )
                      setEndDate(
                        dateKey(shifted(new Date(`${draft.date}T12:00:00`), 1)),
                      );
                  }}
                />
                全天（结束日期不包含在内）
              </label>
            }
            <div>
              <label>
                开始
                <input
                  type="time"
                  disabled={allDay}
                  required
                  value={draft.start}
                  onChange={(e) =>
                    setDraft({ ...draft, start: e.target.value })
                  }
                />
              </label>
              <label>
                结束
                <input
                  type="time"
                  disabled={allDay}
                  required
                  value={draft.end}
                  onChange={(e) => setDraft({ ...draft, end: e.target.value })}
                />
              </label>
            </div>
            <label>
              地点
              <input
                value={draft.location}
                onChange={(e) =>
                  setDraft({ ...draft, location: e.target.value })
                }
              />
            </label>
            <label>
              备注
              <textarea
                value={draft.description}
                rows={2}
                onChange={(e) =>
                  setDraft({ ...draft, description: e.target.value })
                }
              />
            </label>
            {formError && <p role="alert">{formError}</p>}
            {(nativeCalendar.ready || (editing && "native" in editing)) && (
              <label>
                原生提醒
                <select
                  value={alarm}
                  onChange={(e) => setAlarm(e.target.value)}
                >
                  <option value="none">无提醒</option>
                  <option value="0">开始时</option>
                  <option value="-300">提前 5 分钟</option>
                  <option value="-900">提前 15 分钟</option>
                  <option value="-1800">提前 30 分钟</option>
                  <option value="-3600">提前 1 小时</option>
                  <option value="-86400">提前 1 天</option>
                </select>
              </label>
            )}
            <footer>
              <small>
                {nativeCalendar.ready
                  ? "保存到 Mac 日历 · 重复日程仅本次"
                  : "保存到学习助手本机日程"}
              </small>
              <V2Button className="primary" type="submit" disabled={saving}>
                {saving ? "保存中…" : "保存"}
              </V2Button>
            </footer>
          </form>
        </V2Dialog>
      )}
    </div>
  );
}
function formatScheduleTime(s: string) {
  return new Date(s).toLocaleString("zh-CN", {
    month: "long",
    day: "numeric",
    weekday: "long",
    hour: "2-digit",
    minute: "2-digit",
  });
}
