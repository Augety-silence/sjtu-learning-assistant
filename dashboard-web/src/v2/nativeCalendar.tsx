import { ChevronDown } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { AIFloatingMenu } from "@/components/AIFloatingMenu";
import { invoke } from "@/lib/api";
import type { PersonalEvent } from "./calendarData";
import { V2Button } from "./primitives";

export type NativeEvent = {
  id: string;
  calendar_id: string;
  calendar_title: string;
  title: string;
  start_at: string;
  end_at: string;
  all_day: boolean;
  timezone: string;
  location: string;
  notes: string;
  revision: string;
  writable: boolean;
  recurring: boolean;
  alarms: number[];
};
type Calendar = {
  id: string;
  title: string;
  writable: boolean;
  source: string;
};
type Status = {
  supported: boolean;
  enabled: boolean;
  authorization: string;
  generation?: number;
  platform?: string;
  fallback?: string;
};
export type NativePersonalEvent = PersonalEvent & { native: NativeEvent };
export function asPersonal(event: NativeEvent): NativePersonalEvent {
  return {
    id: `native:${event.id}:${event.start_at}`,
    personal: true,
    source: "native",
    native: event,
    title: event.title,
    startAt: event.start_at,
    endAt: event.end_at,
    courseName: event.calendar_title,
    location: event.location,
    description: event.notes,
  };
}
export function useNativeCalendar(month: Date) {
  const [status, setStatus] = useState<Status | null>(null);
  const [events, setEvents] = useState<NativePersonalEvent[]>([]);
  const [calendars, setCalendars] = useState<Calendar[]>([]);
  const [calendarId, setCalendarId] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const monthKey = `${month.getFullYear()}-${month.getMonth()}`;
  const generation = useRef(0);
  const ready = status?.enabled && status.authorization === "full_access";
  const refresh = useCallback(async () => {
    const token = ++generation.current;
    try {
      const next = await invoke<Status>("native_calendar_status");
      if (token !== generation.current) return;
      setStatus(next);
      if (!next.enabled || next.authorization !== "full_access") {
        setEvents([]);
        return;
      }
      const start = new Date(month.getFullYear(), month.getMonth(), -6);
      const end = new Date(month.getFullYear(), month.getMonth() + 1, 8);
      const [list, data] = await Promise.all([
        invoke<{ items: Calendar[] }>("native_calendar_calendars"),
        invoke<{ items: NativeEvent[]; truncated: boolean }>(
          "native_calendar_events",
          { start_at: start.toISOString(), end_at: end.toISOString() },
        ),
      ]);
      if (token !== generation.current) return;
      setCalendars(list.items);
      setCalendarId((previous) =>
        list.items.some((c) => c.id === previous && c.writable)
          ? previous
          : (list.items.find((c) => c.writable)?.id ?? ""),
      );
      setEvents(data.items.map(asPersonal));
      setError(
        data.truncated ? "日程超过 2000 项，仅展示部分，请缩小范围。" : "",
      );
    } catch (reason) {
      if (token === generation.current)
        setError(reason instanceof Error ? reason.message : "Mac 日历读取失败");
    }
  }, [monthKey]);
  useEffect(() => {
    let pending = false;
    const load = async () => {
      if (pending || document.visibilityState === "hidden") return;
      pending = true;
      try {
        await refresh();
      } finally {
        pending = false;
      }
    };
    void load();
    const timer = window.setInterval(() => void load(), 5000);
    window.addEventListener("focus", load);
    document.addEventListener("visibilitychange", load);
    return () => {
      ++generation.current;
      window.clearInterval(timer);
      window.removeEventListener("focus", load);
      document.removeEventListener("visibilitychange", load);
    };
  }, [refresh]);
  const connect = async () => {
    setBusy(true);
    setError("");
    try {
      await invoke("native_calendar_connect");
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "日历授权失败");
    } finally {
      setBusy(false);
    }
  };
  const disconnect = async () => {
    setBusy(true);
    try {
      await invoke("native_calendar_disconnect");
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "断开失败");
    } finally {
      setBusy(false);
    }
  };
  const save = async (
    event: PersonalEvent,
    existing?: NativeEvent,
    options?: { allDay?: boolean; alarm?: number | null },
  ) => {
    if (!calendarId && !existing)
      throw new Error("没有可写入的日历，请先在系统日历中添加日历。");
    const result = await invoke<NativeEvent>("native_calendar_save", {
      request_id: crypto.randomUUID(),
      ...(existing
        ? {
            id: existing.id,
            revision: existing.revision,
            occurrence_start: existing.start_at,
          }
        : {}),
      calendar_id: existing?.calendar_id ?? calendarId,
      title: event.title,
      start_at: event.startAt,
      end_at: event.endAt,
      timezone:
        existing?.timezone === "local"
          ? Intl.DateTimeFormat().resolvedOptions().timeZone
          : (existing?.timezone ??
            Intl.DateTimeFormat().resolvedOptions().timeZone),
      all_day: options?.allDay ?? existing?.all_day ?? false,
      location: event.location ?? "",
      notes: event.description ?? "",
      alarms:
        options?.alarm === undefined
          ? (existing?.alarms ?? [-900])
          : options.alarm === null
            ? []
            : [options.alarm],
      span: "this",
    });
    await refresh();
    return asPersonal(result);
  };
  const remove = async (event: NativeEvent) => {
    await invoke("native_calendar_delete", {
      id: event.id,
      revision: event.revision,
      occurrence_start: event.start_at,
      request_id: crypto.randomUUID(),
      span: "this",
    });
    await refresh();
  };
  const importLegacy = async (
    event: PersonalEvent,
    options?: { allDay?: boolean; alarm?: number | null },
  ) => {
    // 固定迁移请求 ID；即便窗口关闭后重新迁移，EventKit 不会再创建副本。
    const digest = await crypto.subtle.digest(
      "SHA-256",
      new TextEncoder().encode(`sjtu-v2-import:${event.id}`),
    );
    const hex = Array.from(new Uint8Array(digest))
      .map((x) => x.toString(16).padStart(2, "0"))
      .join("");
    const requestId = `${hex.slice(0, 8)}-${hex.slice(8, 12)}-5${hex.slice(13, 16)}-a${hex.slice(17, 20)}-${hex.slice(20, 32)}`;
    return invoke<NativeEvent>("native_calendar_save", {
      request_id: requestId,
      calendar_id: calendarId,
      title: event.title,
      start_at: event.startAt,
      end_at: event.endAt,
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      notes: event.description ?? "",
      location: event.location ?? "",
      all_day: options?.allDay ?? event.allDay ?? false,
      alarms:
        options?.alarm === undefined
          ? [-900]
          : options.alarm === null
            ? []
            : [options.alarm],
    });
  };
  return {
    status,
    ready,
    events,
    calendars,
    calendarId,
    setCalendarId,
    error,
    busy,
    connect,
    disconnect,
    refresh,
    save,
    remove,
    importLegacy,
  };
}
export function NativeCalendarConnection({
  calendar,
  onImport,
  count,
  migrating = false,
  onExport,
}: {
  calendar: ReturnType<typeof useNativeCalendar>;
  onImport: () => void;
  count: number;
  migrating?: boolean;
  onExport?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const windows = calendar.status?.platform === "win32";
  if (calendar.status?.supported === false && !windows) return null;
  if (windows)
    return (
      <section className="v2-native-calendar" aria-label="Windows 日程">
        <header>
          <strong>Windows 日程</strong>
          <button
            type="button"
            ref={trigger}
            className="v2-native-status"
            aria-label="配置 Windows 日程"
            aria-haspopup="dialog"
            aria-expanded={open}
            onClick={() => setOpen(!open)}
          >
            本机保存 <ChevronDown size={13} />
          </button>
        </header>
        {open && (
          <AIFloatingMenu
            anchorRef={trigger}
            onClose={() => setOpen(false)}
            width={300}
            className="v2-native-popover"
          >
            <div
              className="v2-native-settings"
              role="dialog"
              aria-label="Windows 日程配置"
            >
              <strong>Windows 日程</strong>
              <p>
                日程保存在本机。可导出 ICS 后手动导入 Outlook，提醒在 Outlook
                中设置。
              </p>
              <p>
                系统日历双向同步尚未接入；导出文件不会自动更新，也不会自动创建系统提醒。
              </p>
              <V2Button disabled={!count || !onExport} onClick={onExport}>
                导出 {count} 项本机日程
              </V2Button>
            </div>
          </AIFloatingMenu>
        )}
      </section>
    );
  return (
    <section className="v2-native-calendar" aria-label="Mac 日历连接">
      <header>
        <strong>Mac 日历</strong>
        <button
          type="button"
          ref={trigger}
          className="v2-native-status"
          aria-label="配置 Mac 日历连接"
          aria-haspopup="dialog"
          aria-expanded={open}
          onClick={() => setOpen(!open)}
        >
          {calendar.ready ? "双向连接" : "未连接"}
          <ChevronDown size={13} />
        </button>
      </header>
      {open && (
        <AIFloatingMenu
          anchorRef={trigger}
          onClose={() => setOpen(false)}
          width={300}
          className="v2-native-popover"
        >
          <div
            className="v2-native-settings"
            role="dialog"
            aria-label="Mac 日历配置"
          >
            <strong>Mac 日历连接</strong>
            <p>
              {calendar.ready
                ? "系统修改每 5 秒回读；新建日程和 AI 修改直接保存到系统日历。"
                : "连接后可读取系统安排、保存日程并设置原生提醒。"}
            </p>
            {calendar.ready ? (
              <>
                <label>
                  新日程保存到
                  <select
                    value={calendar.calendarId}
                    onChange={(e) => calendar.setCalendarId(e.target.value)}
                  >
                    <option value="" disabled>
                      请选择可写日历
                    </option>
                    {calendar.calendars
                      .filter((c) => c.writable)
                      .map((c) => (
                        <option key={c.id} value={c.id}>
                          {c.title} · {c.source}
                        </option>
                      ))}
                  </select>
                </label>
                <div>
                  {count > 0 && (
                    <V2Button
                      disabled={
                        calendar.busy || migrating || !calendar.calendarId
                      }
                      onClick={onImport}
                    >
                      迁移 {count} 项本机日程
                    </V2Button>
                  )}
                  <V2Button
                    disabled={calendar.busy}
                    onClick={() => void calendar.disconnect()}
                  >
                    断开连接
                  </V2Button>
                </div>
              </>
            ) : (
              <V2Button
                disabled={calendar.busy}
                onClick={() => void calendar.connect()}
              >
                {calendar.busy ? "等待系统授权…" : "连接 Mac 日历"}
              </V2Button>
            )}
            {calendar.error && (
              <p role="alert">
                {calendar.error}
                <V2Button onClick={() => void calendar.refresh()}>
                  重试
                </V2Button>
              </p>
            )}
          </div>
        </AIFloatingMenu>
      )}
      {calendar.error && !open && <p role="alert">{calendar.error}</p>}
    </section>
  );
}
