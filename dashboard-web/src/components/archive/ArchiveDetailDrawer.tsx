import { X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { ArchiveStatusChip } from "@/components/archive/ArchiveStatusChip";
import { archiveStatusLabel } from "@/components/archive/archiveUtils";
import { Button } from "@/components/ui/Button";
import { getArchiveDetail } from "@/lib/api";
import { formatDateTime, formatSize } from "@/lib/format";
import type { ArchiveEntry, ArchiveJob, ArchiveVersion } from "@/lib/types";
import { useModalFocus } from "@/lib/useModalFocus";

function shortHash(value: string) {
  return value.length > 20 ? value.slice(0, 12) + "…" + value.slice(-6) : value;
}

export function ArchiveDetailDrawer({
  entry,
  jobs,
  onClose,
  onRestore,
}: {
  entry: ArchiveEntry;
  jobs: ArchiveJob[];
  onClose: () => void;
  onRestore: (entry: ArchiveEntry, version?: ArchiveVersion) => void;
}) {
  const [detail, setDetail] = useState<ArchiveEntry | null>(null);
  const [error, setError] = useState("");
  const drawerRef = useRef<HTMLElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  useModalFocus(drawerRef, onClose, { initialFocusRef: closeRef });

  useEffect(() => {
    let active = true;
    setDetail(null);
    setError("");
    void getArchiveDetail(entry.id)
      .then((value) => active && setDetail(value))
      .catch(
        (reason) =>
          active &&
          setError(reason instanceof Error ? reason.message : "详情读取失败"),
      );
    return () => {
      active = false;
    };
  }, [entry.id]);

  const current = detail ?? entry;
  const recentJobs = jobs
    .filter((job) => job.entry_id === entry.id)
    .slice(0, 3);

  return (
    <div className="archive-overlay" data-modal-layer>
      <button
        type="button"
        className="archive-overlay-backdrop"
        aria-label="点击遮罩关闭档案详情"
        onClick={onClose}
      />
      <section
        ref={drawerRef}
        className="archive-detail-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="archive-detail-title"
        tabIndex={-1}
      >
        <header>
          <div>
            <ArchiveStatusChip status={current.status} />
            <h3 id="archive-detail-title">{current.filename}</h3>
          </div>
          <Button
            ref={closeRef}
            variant="ghost"
            size="icon"
            aria-label="关闭档案详情"
            onClick={onClose}
          >
            <X aria-hidden="true" />
          </Button>
        </header>

        {error && (
          <p className="archive-inline-error" role="alert">
            {error}
          </p>
        )}
        {!detail && !error ? (
          <p className="archive-drawer-loading" role="status">
            正在读取版本信息…
          </p>
        ) : (
          <>
            <dl className="archive-detail-meta">
              <div>
                <dt>原始路径</dt>
                <dd>{current.original_abs_path ?? "旧记录未保留可信原路径"}</dd>
              </div>
              <div>
                <dt>归档根快照</dt>
                <dd>{current.archive_root_snapshot ?? "—"}</dd>
              </div>
              <div>
                <dt>相对路径</dt>
                <dd>{current.relative_path ?? "—"}</dd>
              </div>
              <div>
                <dt>最近更新</dt>
                <dd>{formatDateTime(current.updated_at)}</dd>
              </div>
            </dl>

            <section
              className="archive-drawer-section"
              aria-labelledby="archive-versions-title"
            >
              <h4 id="archive-versions-title">版本</h4>
              {current.versions?.length ? (
                <ul className="archive-version-list">
                  {current.versions.map((version) => (
                    <li key={version.id}>
                      <div>
                        <strong>版本 {version.version_number}</strong>
                        <ArchiveStatusChip status={version.status} />
                      </div>
                      <dl>
                        <div>
                          <dt>云端路径</dt>
                          <dd>{version.cloud_remote_path?.join("/") ?? "—"}</dd>
                        </div>
                        <div>
                          <dt>大小</dt>
                          <dd>{formatSize(version.size)}</dd>
                        </div>
                        <div>
                          <dt>归档时间</dt>
                          <dd>{formatDateTime(version.archived_at)}</dd>
                        </div>
                        <div>
                          <dt>SHA-256</dt>
                          <dd>
                            <code title={version.sha256}>
                              {shortHash(version.sha256)}
                            </code>
                          </dd>
                        </div>
                      </dl>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        disabled={
                          !["archived", "needs_reconcile"].includes(
                            version.status,
                          )
                        }
                        onClick={() => onRestore(current, version)}
                      >
                        恢复此版本
                      </Button>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="archive-muted">暂无可用版本。</p>
              )}
            </section>

            <section
              className="archive-drawer-section"
              aria-labelledby="archive-recent-title"
            >
              <h4 id="archive-recent-title">最近操作</h4>
              {recentJobs.length ? (
                <ul className="archive-recent-list">
                  {recentJobs.map((job) => (
                    <li key={job.id}>
                      <span>{job.kind === "restore" ? "恢复" : "上传"}</span>
                      <strong>{archiveStatusLabel(job.status)}</strong>
                      <time>
                        {formatDateTime(job.finished_at ?? job.created_at)}
                      </time>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="archive-muted">暂无任务记录。</p>
              )}
            </section>
          </>
        )}
      </section>
    </div>
  );
}
