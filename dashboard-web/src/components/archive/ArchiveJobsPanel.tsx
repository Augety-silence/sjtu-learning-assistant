import { ChevronDown, RotateCcw } from "lucide-react";
import { useState } from "react";
import { ArchiveStatusChip } from "@/components/archive/ArchiveStatusChip";
import {
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
  const [eventError, setEventError] = useState("");
  const [retrying, setRetrying] = useState<string | null>(null);

  const toggleEvents = async (jobId: string) => {
    if (openJob === jobId) {
      setOpenJob(null);
      return;
    }
    setOpenJob(jobId);
    if (events[jobId]) return;
    setEventError("");
    try {
      const result = await getArchiveJobEvents(jobId);
      setEvents((current) => ({ ...current, [jobId]: result.items }));
    } catch (reason) {
      setEventError(
        reason instanceof Error ? reason.message : "任务事件读取失败",
      );
    }
  };

  const retry = async (jobId: string) => {
    setRetrying(jobId);
    try {
      await onRetry(jobId);
    } finally {
      setRetrying(null);
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
                    aria-controls={"archive-events-" + job.id}
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
                      loading={retrying === job.id}
                      loadingLabel="重试中…"
                      onClick={() => void retry(job.id)}
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
                  <p className="archive-job-error" role="alert">
                    {job.last_error}
                  </p>
                )}

                {expanded && (
                  <div
                    className="archive-events"
                    id={"archive-events-" + job.id}
                  >
                    {eventError && <p role="alert">{eventError}</p>}
                    {!events[job.id] ? (
                      <p role="status">正在读取事件…</p>
                    ) : events[job.id].length ? (
                      <ol>
                        {events[job.id].map((event) => (
                          <li key={event.id}>
                            <span>{archiveStatusLabel(event.status)}</span>
                            <div>
                              <strong>{event.event_type}</strong>
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
