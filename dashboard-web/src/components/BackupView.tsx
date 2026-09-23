import { CloudUpload, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { ErrorState, LoadingState } from "@/components/States";
import { Button } from "@/components/ui/Button";
import { getBackupStatus, startCloudBackup } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { BackupFailure, BackupResult, BackupStatus } from "@/lib/types";

const POLL_INTERVAL_MS = 1000;

function safeText(value: string | null | undefined, fallback = "—") {
  if (!value) return fallback;
  return value
    .replace(/\/Users\/[^\s,;)}\]]+/gi, "[本地路径已隐藏]")
    .replace(/\bBearer\s+\S+/gi, "[凭据已隐藏]")
    .replace(
      /\b(?:access[_-]?token|refresh[_-]?token|token|authorization)\b\s*[:=]\s*\S+/gi,
      "[凭据已隐藏]",
    );
}

function statusLabel(status: BackupStatus["status"]) {
  if (status === "running") return "备份运行中";
  if (status === "finished") return "备份已完成";
  return "等待备份";
}

function ResultSummary({ result }: { result: BackupResult }) {
  const items = [
    ["已上传", result.uploaded ?? 0],
    ["云端已存在", result.skipped_existing ?? 0],
    ["已安全移除本地文件", result.local_removed ?? 0],
    ["本地缺失跳过", result.skipped_missing_local ?? 0],
    ["失败", result.failed ?? 0],
  ] as const;

  return (
    <section className="backup-result" aria-labelledby="backup-result-title">
      <div className="section-header">
        <div>
          <h2 id="backup-result-title">最近完成结果</h2>
          {result.finished_at && (
            <p>完成于 {formatDateTime(result.finished_at)}</p>
          )}
        </div>
      </div>
      <dl className="backup-result-grid">
        {items.map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
      {(result.failures?.length ?? 0) > 0 && (
        <div className="backup-failures">
          <h3>失败项</h3>
          <ul>
            {result.failures?.map((failure, index) => (
              <FailureItem
                key={`${failure.source}-${failure.remote_path}-${index}`}
                failure={failure}
              />
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function FailureItem({ failure }: { failure: BackupFailure }) {
  return (
    <li>
      <strong>{safeText(failure.name, "未命名文件")}</strong>
      <dl>
        <div>
          <dt>来源</dt>
          <dd>{safeText(failure.source)}</dd>
        </div>
        <div>
          <dt>云端路径</dt>
          <dd>{safeText(failure.remote_path)}</dd>
        </div>
        <div>
          <dt>原因</dt>
          <dd>{safeText(failure.error, "未知错误")}</dd>
        </div>
      </dl>
    </li>
  );
}

export function BackupView() {
  const [backup, setBackup] = useState<BackupStatus | null>(null);
  const [loadError, setLoadError] = useState("");
  const [operationMessage, setOperationMessage] = useState("");
  const [operationFailed, setOperationFailed] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [starting, setStarting] = useState(false);
  const [watching, setWatching] = useState(false);

  const load = useCallback(async () => {
    setRefreshing(true);
    setLoadError("");
    try {
      const next = await getBackupStatus();
      setBackup(next);
      if (next.status === "running") setWatching(true);
      if (next.status === "finished") setWatching(false);
    } catch (reason) {
      setLoadError(
        reason instanceof Error ? reason.message : "备份状态读取失败",
      );
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!watching && backup?.status !== "running") return;
    const timer = window.setInterval(() => void load(), POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [backup?.status, load, watching]);

  const start = async () => {
    if (!backup?.available || backup.status === "running" || starting) return;
    setStarting(true);
    setOperationFailed(false);
    setOperationMessage("正在提交云盘备份请求…");
    try {
      const result = await startCloudBackup();
      setWatching(true);
      setOperationMessage(
        result.status === "already_running"
          ? "已有云盘备份任务正在运行，已继续跟踪进度。"
          : "备份请求已接受，正在上传到 SJTU Pan。",
      );
      await load();
    } catch (reason) {
      setWatching(false);
      setOperationFailed(true);
      setOperationMessage(
        reason instanceof Error ? reason.message : "云盘备份启动失败",
      );
    } finally {
      setStarting(false);
    }
  };

  if (!backup && loadError)
    return <ErrorState message={loadError} retry={load} />;
  if (!backup) return <LoadingState label="正在读取云盘备份状态…" />;

  const running = backup.status === "running" || watching;
  const progress = backup.progress;
  const progressTotal = Math.max(progress?.total ?? 0, 1);
  const progressDone = Math.min(progress?.done ?? 0, progressTotal);

  return (
    <div className="section-stack backup-page">
      <div className="view-intro backup-intro">
        <div>
          <h2>Canvas 与邮件资料云端副本</h2>
          <p>备份范围仅包含已下载的 Canvas 文件和邮件附件，目标为 SJTU Pan。</p>
        </div>
        <Button
          type="button"
          disabled={!backup.available || running || starting}
          loading={starting}
          loadingLabel="正在启动…"
          onClick={() => void start()}
        >
          <CloudUpload aria-hidden="true" />
          {running ? "备份进行中" : "立即备份"}
        </Button>
      </div>

      <section
        className="backup-status-card"
        aria-labelledby="backup-status-title"
      >
        <div className="backup-status-heading">
          <div role="status" aria-live="polite">
            <span
              className={`status-dot ${running ? "status-running" : ""}`}
              aria-hidden="true"
            />
            <div>
              <h2 id="backup-status-title">
                {statusLabel(running ? "running" : backup.status)}
              </h2>
              <p>
                {backup.available
                  ? "SJTU Pan 已就绪。"
                  : safeText(
                      backup.availability_message,
                      "SJTU Pan 当前不可用。",
                    )}
              </p>
            </div>
          </div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={refreshing}
            aria-label="刷新备份状态"
            onClick={() => void load()}
          >
            <RefreshCw
              aria-hidden="true"
              className={refreshing ? "animate-spin" : ""}
            />
            {refreshing ? "刷新中" : "刷新状态"}
          </Button>
        </div>

        {loadError && (
          <p className="backup-inline-error" role="alert">
            {safeText(loadError)}
          </p>
        )}
        {operationMessage && (
          <p
            className="backup-operation"
            role={operationFailed ? "alert" : "status"}
          >
            {safeText(operationMessage)}
          </p>
        )}

        <div className="backup-credentials">
          <div>
            <strong>交大云盘连接</strong>
            <small>UserToken 已统一移至“系统设置 → 交大云盘”管理。</small>
          </div>
        </div>

        <dl className="backup-counts" aria-label="备份候选统计">
          <div>
            <dt>全部候选</dt>
            <dd>{backup.counts.total}</dd>
          </div>
          <div>
            <dt>Canvas 文件</dt>
            <dd>{backup.counts.canvas}</dd>
          </div>
          <div>
            <dt>邮件附件</dt>
            <dd>{backup.counts.mail}</dd>
          </div>
          <div>
            <dt>可备份</dt>
            <dd>{backup.counts.ready}</dd>
          </div>
          <div>
            <dt>仅云端</dt>
            <dd>{backup.counts.cloud_only}</dd>
          </div>
          <div>
            <dt>本地缺失</dt>
            <dd>{backup.counts.missing_local}</dd>
          </div>
        </dl>

        {running && progress && (
          <div className="backup-progress-block">
            <div className="backup-progress-copy">
              <strong>
                {progress.done} / {progress.total}
              </strong>
              <span>
                {safeText(progress.current_name, "正在准备下一个文件…")}
              </span>
            </div>
            <div
              className="backup-progress"
              role="progressbar"
              aria-label="云盘备份进度"
              aria-valuemin={0}
              aria-valuemax={progressTotal}
              aria-valuenow={progressDone}
              aria-valuetext={`已完成 ${progress.done}，共 ${progress.total}`}
            >
              <span
                style={{ width: `${(progressDone / progressTotal) * 100}%` }}
              />
            </div>
          </div>
        )}
      </section>

      {backup.last_result && <ResultSummary result={backup.last_result} />}
    </div>
  );
}
