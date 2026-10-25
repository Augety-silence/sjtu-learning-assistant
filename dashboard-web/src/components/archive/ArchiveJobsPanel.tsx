import { ChevronDown, RotateCcw } from "lucide-react";
import { useRef, useState } from "react";
import { ArchiveStatusChip } from "@/components/archive/ArchiveStatusChip";
import {
  archiveEventLabel,
  archiveStatusLabel,
  jobProgress,
} from "@/components/archive/archiveUtils";
import { Button } from "@/components/ui/Button";
import { getArchiveJobEvents } from "@/lib/api";
import { formatDateTime, formatSize } from "@/lib/format";
import type { ArchiveJob, ArchiveJobEvent } from "@/lib/types";

const activeStatuses = new Set([
  "running",
  "uploading",
  "downloading",
  "verifying",
]);

export function ArchiveJobsPanel({
  jobs,
  loading,
  onRetry,
}: {
  jobs: ArchiveJob[];
  loading: boolean;
  onRetry: (jobId: string) => Promise<void>;
}) {
  const [openJob, setOpenJob] = useState<string | null>(null);
  const [events, setEvents] = useState<Record<string, ArchiveJobEvent[]>>({});
  const [eventErrors, setEventErrors] = useState<Record<string, string>>({});
  const [loadingEvents, setLoadingEvents] = useState<Set<string>>(new Set());
  // 按任务记录在途重试：单一 id 会在并发重试时被后完成的请求清空，导致仍在执行的
  // 恢复/上传任务按钮重新可点、重复提交；ref 同步拦截同一帧内的连击。
  const [retrying, setRetrying] = useState<Set<string>>(new Set());
  const retryingRef = useRef<Set<string>>(new Set());

  const toggleEvents = async (jobId: string) => {
    if (openJob === jobId) {
      setOpenJob(null);
      return;
    }
    setOpenJob(jobId);
    if (loadingEvents.has(jobId)) return;
    setEvents((current) => {
      const next = { ...current };
      delete next[jobId];
      return next;
    });
    setEventErrors((current) => {
      const next = { ...current };
      delete next[jobId];
      return next;
    });
    setLoadingEvents((current) => new Set(current).add(jobId));
    try {
      const result = await getArchiveJobEvents(jobId);
      setEvents((current) => ({ ...current, [jobId]: result.items }));
    } catch (reason) {
      setEventErrors((current) => ({
        ...current,
        [jobId]: reason instanceof Error ? reason.message : "任务事件读取失败",
      }));
    } finally {
      setLoadingEvents((current) => {
        const next = new Set(current);
        next.delete(jobId);
        return next;
      });
    }
  };

  const retry = async (job: ArchiveJob) => {
    if (
      job.kind === "restore" &&
      job.conflict_policy === "overwrite" &&
      !window.confirm(
        `确认重试覆盖恢复${job.target_filename ? `“${job.target_filename}”` : ""}？请先关闭正在打开该文件的程序。系统会再次替换目标文件，并在成功前保留同目录回滚快照。`,
      )
    )
      return;
    const jobId = job.id;
    if (retryingRef.current.has(jobId)) return;
    retryingRef.current.add(jobId);
    setRetrying(new Set(retryingRef.current));
    try {
      await onRetry(jobId);
    } finally {
      retryingRef.current.delete(jobId);
      setRetrying(new Set(retryingRef.current));
    }
  };

  return (
    <section
      className="archive-jobs"
      aria-labelledby="archive-jobs-title"
      aria-busy={loading}
    >
      <div className="archive-section-heading">
        <div>
          <h3 id="archive-jobs-title">任务与事件</h3>
          <p>显示最近的上传、下载、校验和恢复状态。</p>
        </div>
        {loading && <span role="status">正在更新…</span>}
      </div>

      {jobs.length === 0 ? (
        <p className="archive-empty-inline">
          暂无归档或恢复任务。开始归档后可在这里跟踪进度。
        </p>
      ) : (
        <ul className="archive-job-list">
          {jobs.map((job) => {
            const progress = jobProgress(job);
            const expanded = openJob === job.id;
            return (
              <li key={job.id}>
                <div className="archive-job-main">
                  <button
                    type="button"
                    className="archive-job-toggle"
                    aria-expanded={expanded}
                    aria-controls={
                      expanded ? "archive-events-" + job.id : undefined
                    }
                    onClick={() => void toggleEvents(job.id)}
                  >
                    <ChevronDown aria-hidden="true" />
                    <span>
                      <strong>
                        {job.kind === "restore" ? "恢复任务" : "上传任务"}
                      </strong>
                      <small>
                        {formatDateTime(job.started_at ?? job.created_at)}
                      </small>
                    </span>
                  </button>
                  <ArchiveStatusChip status={job.status} />
                  {job.retryable === true && (
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      loading={retrying.has(job.id)}
                      loadingLabel="重试中…"
                      aria-label={`重试${job.kind === "restore" ? "恢复" : "上传"}${job.target_filename ? ` ${job.target_filename}` : ""} ${formatDateTime(job.started_at ?? job.created_at)}`}
                      onClick={() => void retry(job)}
                    >
                      <RotateCcw aria-hidden="true" />
                      重试
                    </Button>
                  )}
                </div>

                {progress !== null &&
                  (activeStatuses.has(job.status) ||
                    job.status === "completed") && (
                    <div className="archive-job-progress">
                      <div>
                        <span>{archiveStatusLabel(job.status)}</span>
                        <strong>{progress}%</strong>
                      </div>
                      <div
                        role="progressbar"
                        aria-label={
                          job.kind === "restore" ? "恢复进度" : "上传进度"
                        }
                        aria-valuemin={0}
                        aria-valuemax={100}
                        aria-valuenow={progress}
                      >
                        <span style={{ width: progress + "%" }} />
                      </div>
                      <small>
                        {formatSize(job.bytes_done)} /{" "}
                        {formatSize(job.bytes_total)}
                      </small>
                    </div>
                  )}

                {job.last_error && (
                  <p className="archive-job-error">{job.last_error}</p>
                )}

                {expanded && (
                  <div
                    className="archive-events"
                    id={"archive-events-" + job.id}
                  >
                    {eventErrors[job.id] && (
                      <p role="alert">{eventErrors[job.id]}</p>
                    )}
                    {!events[job.id] ? (
                      eventErrors[job.id] ? null : (
                        <p role="status">正在读取事件…</p>
                      )
                    ) : events[job.id].length ? (
                      <ol>
                        {events[job.id].map((event) => (
                          <li key={event.id}>
                            <span>{archiveStatusLabel(event.status)}</span>
                            <div>
                              <strong>
                                {archiveEventLabel(event.event_type)}
                              </strong>
                              <small>
                                {event.message ??
                                  formatDateTime(event.created_at)}
                              </small>
                            </div>
                          </li>
                        ))}
                      </ol>
                    ) : (
                      <p>暂无事件。</p>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
