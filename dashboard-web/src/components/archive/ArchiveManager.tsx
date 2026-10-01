import {
  Archive,
  ChevronLeft,
  ChevronRight,
  FolderUp,
  RefreshCw,
  Search,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import "@/components/archive/archive.css";
import { ArchiveDetailDrawer } from "@/components/archive/ArchiveDetailDrawer";
import { ArchiveJobsPanel } from "@/components/archive/ArchiveJobsPanel";
import { ArchiveStatusChip } from "@/components/archive/ArchiveStatusChip";
import { RestoreDialog } from "@/components/archive/RestoreDialog";
import { Button } from "@/components/ui/Button";
import {
  getArchiveJobs,
  getArchiveList,
  retryArchive,
  startArchive,
} from "@/lib/api";
import { formatDateTime, formatSize } from "@/lib/format";
import type {
  ArchiveEntry,
  ArchiveJob,
  ArchiveListItem,
  ArchiveVersion,
} from "@/lib/types";

const PAGE_SIZE = 20;
const SEARCH_DEBOUNCE_MS = 300;
const runningStatuses = new Set([
  "running",
  "uploading",
  "downloading",
  "verifying",
]);
const restorableStatuses = new Set([
  "archived",
  "needs_reconcile",
  "needs_verification",
]);
type RestoreVersion = Pick<ArchiveVersion, "id">;

function isOfflineMessage(message: string) {
  return /Bridge|不可用|尚未就绪|网络|连接/.test(message);
}

function currentVersion(entry: ArchiveListItem): RestoreVersion | undefined {
  return entry.current_version_id
    ? { id: entry.current_version_id }
    : undefined;
}

export function ArchiveManager() {
  const [entries, setEntries] = useState<ArchiveListItem[] | null>(null);
  const [jobs, setJobs] = useState<ArchiveJob[]>([]);
  const [cursor, setCursor] = useState<string>();
  const [cursorHistory, setCursorHistory] = useState<Array<string | undefined>>(
    [],
  );
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState<boolean>();
  const [total, setTotal] = useState<number | null>(null);
  const [queryInput, setQueryInput] = useState("");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [sort, setSort] = useState("updated_desc");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [detailEntry, setDetailEntry] = useState<ArchiveListItem | null>(null);
  const [restoreTarget, setRestoreTarget] = useState<ArchiveEntry | null>(null);
  const [restoreVersion, setRestoreVersion] = useState<RestoreVersion>();
  const [restoreQueue, setRestoreQueue] = useState<ArchiveListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [jobsLoading, setJobsLoading] = useState(true);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const pageRequest = useRef(0);

  const resetPagination = useCallback(() => {
    setCursor(undefined);
    setCursorHistory([]);
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      const normalized = queryInput.trim();
      setQuery((current) => {
        if (current === normalized) return current;
        resetPagination();
        return normalized;
      });
    }, SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [queryInput, resetPagination]);

  const loadPage = useCallback(async () => {
    const request = ++pageRequest.current;
    setLoading(true);
    setError("");
    try {
      const result = await getArchiveList({
        limit: PAGE_SIZE,
        query: query || undefined,
        status: status === "all" ? undefined : status,
        sort,
        cursor,
      });
      if (request !== pageRequest.current) return;
      setEntries(result.items);
      setSelected(new Set());
      setNextCursor(result.next_cursor ?? null);
      setHasMore(result.has_more);
      setTotal(typeof result.total === "number" ? result.total : null);
    } catch (reason) {
      if (request !== pageRequest.current) return;
      setError(reason instanceof Error ? reason.message : "云盘档案读取失败");
      setEntries((current) => current ?? []);
      setNextCursor(null);
      setHasMore(false);
    } finally {
      if (request === pageRequest.current) setLoading(false);
    }
  }, [cursor, query, sort, status]);

  const loadJobs = useCallback(async (quiet = false) => {
    if (!quiet) setJobsLoading(true);
    try {
      const result = await getArchiveJobs(20);
      setJobs(result.items);
    } catch (reason) {
      if (!quiet)
        setError(reason instanceof Error ? reason.message : "任务读取失败");
    } finally {
      if (!quiet) setJobsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadPage();
  }, [loadPage]);

  useEffect(() => {
    void loadJobs();
  }, [loadJobs]);

  const hasRunningJob = jobs.some((job) => runningStatuses.has(job.status));
  useEffect(() => {
    if (!hasRunningJob) return;
    const timer = window.setInterval(() => void loadJobs(true), 1500);
    return () => window.clearInterval(timer);
  }, [hasRunningJob, loadJobs]);

  const visibleEntries = entries ?? [];

  const toggleSelected = (entryId: string) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(entryId)) next.delete(entryId);
      else next.add(entryId);
      return next;
    });
  };

  const selectAllVisible = () => {
    setSelected((current) => {
      const allSelected =
        visibleEntries.length > 0 &&
        visibleEntries.every((entry) => current.has(entry.id));
      if (allSelected) return new Set();
      return new Set(visibleEntries.map((entry) => entry.id));
    });
  };

  const addArchive = async () => {
    setStarting(true);
    setError("");
    setNotice("已打开原生文件选择器。选择文件后将开始归档。");
    try {
      const result = await startArchive();
      setNotice(
        result.deduplicated ? "相同内容已归档，未重复上传。" : "文件归档完成。",
      );
      await Promise.all([loadPage(), loadJobs()]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "归档启动失败");
    } finally {
      setStarting(false);
    }
  };

  const retry = async (jobId: string) => {
    setError("");
    try {
      await retryArchive(jobId);
      setNotice("失败任务已重新执行。");
      await Promise.all([loadPage(), loadJobs()]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "重试失败");
    }
  };

  const openRestore = (entry: ArchiveEntry, version?: RestoreVersion) => {
    setRestoreQueue([]);
    setRestoreTarget(entry);
    setRestoreVersion(version);
  };

  const restoreSelected = () => {
    const queue = visibleEntries.filter((entry) => selected.has(entry.id));
    if (!queue.length) return;
    setRestoreQueue(queue.slice(1));
    setRestoreTarget(queue[0]);
    setRestoreVersion(currentVersion(queue[0]));
  };

  const completeRestore = (job: ArchiveJob) => {
    setJobs((current) => [
      job,
      ...current.filter((item) => item.id !== job.id),
    ]);
    setNotice(
      job.status === "compare"
        ? "文件对照已完成。"
        : job.status === "skipped"
          ? "已按策略跳过现有文件。"
          : "恢复任务已完成。",
    );
    const next = restoreQueue[0];
    setRestoreQueue((current) => current.slice(1));
    setRestoreTarget(next ?? null);
    setRestoreVersion(next ? currentVersion(next) : undefined);
    void loadJobs(true);
  };

  const changeStatus = (value: string) => {
    setStatus(value);
    resetPagination();
  };

  const changeSort = (value: string) => {
    setSort(value);
    resetPagination();
  };

  const goToNextPage = () => {
    if (!nextCursor || hasMore === false) return;
    setCursorHistory((current) => [...current, cursor]);
    setCursor(nextCursor);
  };

  const goToPreviousPage = () => {
    if (!cursorHistory.length) return;
    const previous = cursorHistory[cursorHistory.length - 1];
    setCursorHistory((current) => current.slice(0, -1));
    setCursor(previous);
  };

  const allVisibleSelected =
    visibleEntries.length > 0 &&
    visibleEntries.every((entry) => selected.has(entry.id));
  const offline = Boolean(error && isOfflineMessage(error));
  const filtering = Boolean(query || status !== "all");

  return (
    <section
      className="archive-manager"
      aria-labelledby="archive-manager-title"
    >
      <div className="archive-manager-heading">
        <div>
          <h2 id="archive-manager-title">云盘档案</h2>
          <p>按版本查找、校验并恢复已归档文件。每页读取 {PAGE_SIZE} 条记录。</p>
        </div>
        <Button
          type="button"
          loading={starting}
          loadingLabel="正在归档…"
          onClick={() => void addArchive()}
        >
          <FolderUp aria-hidden="true" />
          选择文件归档
        </Button>
      </div>

      <div
        className="archive-live-region"
        aria-live="polite"
        aria-atomic="true"
      >
        {notice}
      </div>
      {error && (
        <div
          className={
            offline
              ? "archive-banner archive-banner--offline"
              : "archive-banner"
          }
          role="alert"
        >
          <span>
            <strong>
              {offline ? "桌面 Bridge 当前离线" : "无法更新云盘档案"}
            </strong>
            {error}
          </span>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => void Promise.all([loadPage(), loadJobs()])}
          >
            重试
          </Button>
        </div>
      )}

      <div className="archive-toolbar" role="search">
        <label className="archive-search">
          <span className="sr-only">搜索全部档案</span>
          <Search aria-hidden="true" />
          <input
            value={queryInput}
            onChange={(event) => setQueryInput(event.target.value)}
            placeholder="搜索全部文件名或相对路径"
          />
        </label>
        <label>
          <span>状态</span>
          <select
            value={status}
            onChange={(event) => changeStatus(event.target.value)}
          >
            <option value="all">筛选：全部状态</option>
            <option value="archived">筛选：已归档</option>
            <option value="local_changed">筛选：本地有变更</option>
            <option value="cloud_only">筛选：仅云端</option>
            <option value="local_only">筛选：仅本地</option>
            <option value="uploading">筛选：上传中</option>
            <option value="downloading">筛选：下载中</option>
            <option value="verifying">筛选：校验中</option>
            <option value="failed">筛选：失败</option>
            <option value="interrupted">筛选：已中断</option>
            <option value="needs_reconcile">筛选：需要校验</option>
          </select>
        </label>
        <label>
          <span>排序</span>
          <select
            value={sort}
            onChange={(event) => changeSort(event.target.value)}
          >
            <option value="updated_desc">最近更新优先</option>
            <option value="archived_desc">最近归档优先</option>
            <option value="name_asc">文件名 A–Z</option>
            <option value="size_desc">文件大小降序</option>
          </select>
        </label>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={loading}
          onClick={() => void loadPage()}
        >
          <RefreshCw aria-hidden="true" />
          刷新
        </Button>
      </div>

      {selected.size > 0 && (
        <div className="archive-selection-bar" role="status">
          <span>已选择 {selected.size} 个文件</span>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={restoreSelected}
          >
            逐项恢复所选
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => setSelected(new Set())}
          >
            清除选择
          </Button>
        </div>
      )}

      <div className="archive-table-wrap" aria-busy={loading}>
        <table className="archive-table">
          <thead>
            <tr>
              <th className="archive-check-cell">
                <input
                  type="checkbox"
                  aria-label="选择当前页结果"
                  checked={allVisibleSelected}
                  onChange={selectAllVisible}
                />
              </th>
              <th>文件</th>
              <th>云端路径</th>
              <th>大小</th>
              <th>最近归档</th>
              <th>状态</th>
              <th>
                <span className="sr-only">操作</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {loading && !entries ? (
              Array.from({ length: 4 }, (_, index) => (
                <tr
                  key={"loading-" + index}
                  className="archive-skeleton"
                  aria-hidden="true"
                >
                  <td />
                  <td>
                    <span />
                  </td>
                  <td>
                    <span />
                  </td>
                  <td>
                    <span />
                  </td>
                  <td>
                    <span />
                  </td>
                  <td>
                    <span />
                  </td>
                  <td />
                </tr>
              ))
            ) : visibleEntries.length ? (
              visibleEntries.map((entry) => (
                <tr
                  key={entry.id}
                  data-selected={selected.has(entry.id) || undefined}
                >
                  <td className="archive-check-cell">
                    <input
                      type="checkbox"
                      aria-label={"选择 " + entry.filename}
                      checked={selected.has(entry.id)}
                      onChange={() => toggleSelected(entry.id)}
                    />
                  </td>
                  <td>
                    <button
                      type="button"
                      className="archive-file-link"
                      onClick={() => setDetailEntry(entry)}
                    >
                      <strong>{entry.filename}</strong>
                      <small title={entry.original_abs_path ?? undefined}>
                        {entry.original_abs_path ?? "原始路径不可用"}
                      </small>
                    </button>
                  </td>
                  <td>
                    <span
                      className="archive-path"
                      title={entry.cloud_path?.join("/")}
                    >
                      {entry.cloud_path?.join("/") ?? "—"}
                    </span>
                  </td>
                  <td>{formatSize(entry.size_bytes)}</td>
                  <td>
                    {formatDateTime(entry.archived_at ?? entry.updated_at)}
                  </td>
                  <td>
                    <ArchiveStatusChip status={entry.version_status} />
                  </td>
                  <td>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      disabled={
                        !entry.current_version_id ||
                        !restorableStatuses.has(entry.version_status)
                      }
                      onClick={() => openRestore(entry, currentVersion(entry))}
                    >
                      恢复
                    </Button>
                  </td>
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan={7}>
                  <div className="archive-empty">
                    <Archive aria-hidden="true" />
                    <strong>
                      {filtering ? "没有匹配的云盘档案" : "还没有云盘档案"}
                    </strong>
                    <p>
                      {filtering
                        ? "调整搜索或状态筛选后重试。"
                        : "使用“选择文件归档”添加第一份档案。"}
                    </p>
                  </div>
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="archive-pagination">
        <span>
          第 {cursorHistory.length + 1} 页
          {entries ? ` · 本页 ${entries.length} 条` : ""}
          {total !== null ? ` · 共 ${total} 条` : ""}
        </span>
        <div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={cursorHistory.length === 0 || loading}
            onClick={goToPreviousPage}
          >
            <ChevronLeft aria-hidden="true" />
            上一页
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={loading || !nextCursor || hasMore === false}
            onClick={goToNextPage}
          >
            下一页
            <ChevronRight aria-hidden="true" />
          </Button>
        </div>
      </div>

      <ArchiveJobsPanel jobs={jobs} loading={jobsLoading} onRetry={retry} />

      {detailEntry && (
        <ArchiveDetailDrawer
          entry={detailEntry}
          jobs={jobs}
          onClose={() => setDetailEntry(null)}
          onRestore={(entry, version) => {
            setDetailEntry(null);
            openRestore(entry, version);
          }}
        />
      )}
      {restoreTarget && (
        <RestoreDialog
          entry={restoreTarget}
          version={restoreVersion}
          onClose={() => {
            setRestoreTarget(null);
            setRestoreVersion(undefined);
            setRestoreQueue([]);
          }}
          onComplete={completeRestore}
        />
      )}
    </section>
  );
}
