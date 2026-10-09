import {
  CalendarDays,
  Check,
  FileUp,
  RefreshCw,
  ShieldCheck,
  Unplug,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { type CalendarEventItem } from "@/components/CalendarView";
import { CalmSchedule } from "@/components/calm/CalmSchedule";
import { Button } from "@/components/ui/Button";
import {
  commitTimetableImport,
  getTimetableSchedule,
  getTimetableStatus,
  previewTimetableFile,
  previewTimetableSample,
} from "@/lib/api";
import type { TimetableImportPreview, TimetableStatus } from "@/lib/types";
import { useModalFocus } from "@/lib/useModalFocus";

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

type ImportStep = "idle" | "picking" | "preview" | "committing";

function messageFrom(reason: unknown, fallback: string) {
  return reason instanceof Error ? reason.message : fallback;
}

function formatSyncTime(value: string | null) {
  if (!value) return "尚未同步";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
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

function courseCount(preview: TimetableImportPreview) {
  return Array.isArray(preview.courses)
    ? preview.courses.length
    : preview.courses;
}

function courseNames(preview: TimetableImportPreview) {
  if (!Array.isArray(preview.courses)) return [];
  return preview.courses.map((course) =>
    typeof course === "string" ? course : course.name,
  );
}

function ImportDialog({
  preview,
  step,
  error,
  onChoose,
  onSample,
  onCommit,
  onClose,
}: {
  preview: TimetableImportPreview | null;
  step: ImportStep;
  error: string | null;
  onChoose: () => void;
  onSample: () => void;
  onCommit: () => void;
  onClose: () => void;
}) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  useModalFocus(dialogRef, onClose, {
    initialFocusRef: closeRef,
    dismissible: step !== "committing",
  });
  const names = preview ? courseNames(preview) : [];

  return (
    <div className="schedule-dialog-layer" data-modal-layer>
      <div
        ref={dialogRef}
        className="schedule-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="schedule-import-title"
        tabIndex={-1}
      >
        <div className="schedule-dialog-heading">
          <div>
            <h2 id="schedule-import-title">导入本地课表</h2>
            <p>.json 与 .ics 文件仅用于生成本机日程。</p>
          </div>
          <Button
            ref={closeRef}
            variant="ghost"
            size="sm"
            onClick={onClose}
            disabled={step === "committing"}
          >
            取消
          </Button>
        </div>

        <div className="schedule-file-choice">
          <FileUp aria-hidden="true" />
          <div>
            <strong>{preview ? "已读取课表" : "选择课表文件"}</strong>
            <p>支持 .json / .ics；确认前不会写入日程。</p>
          </div>
          <Button
            variant={preview ? "outline" : "default"}
            onClick={onChoose}
            loading={step === "picking"}
            loadingLabel="正在解析…"
          >
            {preview ? "重新选择" : "选择文件"}
          </Button>
        </div>

        {error && (
          <div className="schedule-import-error" role="alert">
            <strong>未能读取课表</strong>
            <span>{error}</span>
            <Button variant="outline" size="sm" onClick={onChoose}>
              重新选择
            </Button>
          </div>
        )}

        {preview ? (
          <div className="schedule-preview" aria-live="polite">
            <dl>
              <div>
                <dt>课程</dt>
                <dd>{courseCount(preview)} 门</dd>
              </div>
              <div>
                <dt>课次</dt>
                <dd>{preview.sessions} 节</dd>
              </div>
              <div>
                <dt>格式</dt>
                <dd>{preview.format.toUpperCase()}</dd>
              </div>
            </dl>
            {preview.warnings.length > 0 && (
              <div className="schedule-warnings">
                <strong>导入提示</strong>
                <ul>
                  {preview.warnings.map((warning) => (
                    <li key={warning}>{warning}</li>
                  ))}
                </ul>
              </div>
            )}
            {names.length > 0 && (
              <div className="schedule-course-preview">
                <strong>课程预览</strong>
                <ul>
                  {names.slice(0, 6).map((name) => (
                    <li key={name}>{name}</li>
                  ))}
                </ul>
                {names.length > 6 && (
                  <span>另有 {names.length - 6} 门课程</span>
                )}
              </div>
            )}
            <div className="schedule-dialog-actions">
              <Button variant="outline" onClick={onChoose}>
                重新选择
              </Button>
              <Button
                onClick={onCommit}
                loading={step === "committing"}
                loadingLabel="正在导入…"
              >
                确认导入
              </Button>
            </div>
          </div>
        ) : (
          <button
            className="schedule-sample"
            type="button"
            onClick={onSample}
            disabled={step === "picking"}
          >
            <CalendarDays aria-hidden="true" />
            <span>
              <strong>加载匿名示例课表</strong>
              <small>无需凭据，立即体验课程日程</small>
            </span>
          </button>
        )}
      </div>
    </div>
  );
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
  const [loading, setLoading] = useState(true);
  const [offline, setOffline] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [preview, setPreview] = useState<TimetableImportPreview | null>(null);
  const [importStep, setImportStep] = useState<ImportStep>("idle");
  const [importError, setImportError] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [syncing, setSyncing] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
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
    } finally {
      setLoading(false);
    }
  }, [month]);

  useEffect(() => void load(), [load]);

  const openImport = () => {
    setPreview(null);
    setImportError(null);
    setImportStep("idle");
    setDialogOpen(true);
  };

  const chooseFile = async () => {
    setImportStep("picking");
    setImportError(null);
    try {
      const next = await previewTimetableFile();
      if ("cancelled" in next) {
        setImportStep(preview ? "preview" : "idle");
        return;
      }
      setPreview(next);
      setImportStep("preview");
    } catch (reason) {
      setPreview(null);
      setImportError(messageFrom(reason, "文件解析失败，请检查格式后重试。"));
      setImportStep("idle");
    }
  };

  const loadSample = async () => {
    setImportStep("picking");
    setImportError(null);
    try {
      setPreview(await previewTimetableSample());
      setImportStep("preview");
    } catch (reason) {
      setImportError(messageFrom(reason, "示例课表加载失败。"));
      setImportStep("idle");
    }
  };

  const commit = async () => {
    if (!preview) return;
    setImportStep("committing");
    setImportError(null);
    try {
      const result = await commitTimetableImport(preview.previewId);
      setNotice(
        `已导入 ${result.importedCourses} 门课程、${result.importedSessions} 节课${result.updatedSessions ? `，更新 ${result.updatedSessions} 节` : ""}。`,
      );
      setDialogOpen(false);
      setPreview(null);
      await load();
    } catch (reason) {
      setImportError(messageFrom(reason, "导入失败，本地课表未更改。"));
      setImportStep("preview");
    }
  };

  const sync = async () => {
    setSyncing(true);
    setNotice("");
    try {
      setNotice(
        status?.state === "awaiting_configuration"
          ? "已刷新本地课表。"
          : "课表已同步。",
      );
      await load();
    } catch (reason) {
      setNotice(messageFrom(reason, "同步失败，请稍后重试。"));
    } finally {
      setSyncing(false);
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

  return (
    <div className="section-stack schedule-workspace calm-schedule">
      {loading && !status ? (
        <div
          className="schedule-status-skeleton"
          aria-label="正在加载课表状态"
        />
      ) : hasCourseData ? (
        <section className="calm-source-row" aria-label="课表来源与同步状态">
          <div className="schedule-status-main">
            <span className={`calm-source-icon ${offline ? "is-offline" : ""}`}>
              {offline ? (
                <Unplug aria-hidden="true" />
              ) : (
                <Check aria-hidden="true" />
              )}
            </span>
            <div>
              <strong>{status?.provider || "本地课表"}</strong>
              <p>
                {offline
                  ? "离线缓存"
                  : status?.state === "awaiting_configuration"
                    ? "本地课表 · jAccount 待配置"
                    : "已连接"}{" "}
                · 上次同步 {formatSyncTime(status?.lastSyncedAt ?? null)}
              </p>
            </div>
          </div>
          <div className="calm-source-actions">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => void sync()}
              loading={syncing}
              loadingLabel="同步中…"
              title={
                status?.state === "awaiting_configuration"
                  ? "开放平台配置完成前，仅刷新本地缓存"
                  : undefined
              }
            >
              <RefreshCw aria-hidden="true" />
              立即同步
            </Button>
            <Button variant="outline" size="sm" onClick={openImport}>
              重新导入
            </Button>
          </div>
          {status?.state === "awaiting_configuration" && (
            <p className="calm-source-note">
              开放平台配置完成前，“立即同步”仅刷新本地缓存。
            </p>
          )}
        </section>
      ) : (
        <section
          className="calm-connect"
          aria-labelledby="schedule-connect-title"
        >
          <div className="schedule-connect-copy">
            <span className="calm-connect-icon">
              <CalendarDays aria-hidden="true" />
            </span>
            <div>
              <h2 id="schedule-connect-title">连接上海交通大学</h2>
              <p>
                自动同步课程时间、节次与上课地点，和 Canvas 截止日一起查看。
              </p>
            </div>
          </div>
          <div className="calm-connect-actions">
            <Button disabled title="等待上海交通大学开放平台配置">
              使用 jAccount 连接
            </Button>
            <span className="calm-awaiting">等待开放平台配置</span>
            <Button variant="outline" onClick={openImport}>
              导入本地课表
            </Button>
          </div>
          <div className="calm-privacy">
            <ShieldCheck aria-hidden="true" />
            <span>不保存 jAccount 密码，可随时取消授权。</span>
          </div>
          <button
            className="calm-inline-sample"
            type="button"
            onClick={() => {
              openImport();
              window.setTimeout(() => void loadSample(), 0);
            }}
          >
            加载匿名示例课表
          </button>
        </section>
      )}

      {loadError && !hasCourseData && (
        <div className="schedule-load-error" role="alert">
          <span>{loadError}</span>
          <Button variant="outline" size="sm" onClick={() => void load()}>
            重试
          </Button>
        </div>
      )}
      {(notice || status?.message) && (
        <p className="schedule-live" aria-live="polite">
          {notice || status?.message}
        </p>
      )}

      {canvasLoading && !calendarError ? (
        <p className="calm-source-note">正在同步 Canvas 日历…</p>
      ) : null}
      {calendarError ? (
        <div className="calm-source-row" role="alert">
          <p className="calm-source-note">{calendarError}</p>
          <div className="calm-source-actions">
            <Button size="sm" onClick={() => void onRetryCanvas?.()}>
              重试
            </Button>
          </div>
        </div>
      ) : null}
      <CalmSchedule
        events={allEvents}
        month={month}
        onMonthChange={onMonthChange}
        onOpenEvent={(event) =>
          event.eventType !== "course" ? onOpenCanvasEvent?.(event) : undefined
        }
        onAskAI={onAskAI}
      />

      {dialogOpen && (
        <ImportDialog
          preview={preview}
          step={importStep}
          error={importError}
          onChoose={() => void chooseFile()}
          onSample={() => void loadSample()}
          onCommit={() => void commit()}
          onClose={() => setDialogOpen(false)}
        />
      )}
    </div>
  );
}
