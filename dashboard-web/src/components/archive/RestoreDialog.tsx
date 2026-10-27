import { FolderOpen, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { formatArchiveMtime } from "@/components/archive/archiveUtils";
import { Button } from "@/components/ui/Button";
import { authorizeArchiveRoot, executeRestore, planRestore } from "@/lib/api";
import { formatSize } from "@/lib/format";
import type {
  ArchiveAuthorizedRoot,
  ArchiveConflictPolicy,
  ArchiveEntry,
  ArchiveJob,
  ArchiveVersion,
  RestorePlan,
} from "@/lib/types";
import { useModalFocus } from "@/lib/useModalFocus";

const conflictOptions: Array<{
  value: ArchiveConflictPolicy;
  label: string;
  detail: string;
}> = [
  { value: "skip", label: "跳过", detail: "保留现有文件，不下载云端版本。" },
  { value: "save_as", label: "另存为", detail: "自动生成不冲突的文件名。" },
  { value: "overwrite", label: "覆盖", detail: "校验完成后替换现有普通文件。" },
  { value: "compare", label: "仅对照", detail: "只记录差异，不写入文件。" },
];

function HashValue({ value }: { value: string | null }) {
  if (!value) return <>—</>;
  return (
    <code title={value} aria-label={value}>
      {value.slice(0, 12)}…{value.slice(-6)}
    </code>
  );
}

export function RestoreDialog({
  entry,
  version,
  onClose,
  onComplete,
  onFailed,
}: {
  entry: ArchiveEntry;
  version?: Pick<ArchiveVersion, "id">;
  onClose: () => void;
  onComplete: (job: ArchiveJob) => void;
  onFailed?: () => void;
}) {
  const originalAvailable =
    entry.restore_capability === "original_path" &&
    Boolean(entry.original_abs_path);
  const [mode, setMode] = useState<"original" | "choose_location">(
    originalAvailable ? "original" : "choose_location",
  );
  const [root, setRoot] = useState<ArchiveAuthorizedRoot | null>(null);
  const [plan, setPlan] = useState<RestorePlan | null>(null);
  const [policy, setPolicy] = useState<ArchiveConflictPolicy>("save_as");
  const [confirmDirs, setConfirmDirs] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const dialogRef = useRef<HTMLElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const planActionRef = useRef<HTMLButtonElement>(null);
  const [refocusPlanAction, setRefocusPlanAction] = useState(0);
  useModalFocus(dialogRef, onClose, {
    initialFocusRef: closeRef,
    dismissible: !busy,
  });
  useEffect(() => {
    if (refocusPlanAction) planActionRef.current?.focus();
  }, [refocusPlanAction]);

  const createPlan = async (nextMode = mode, nextRoot = root) => {
    setBusy(true);
    setError("");
    setPlan(null);
    try {
      let authorized = nextRoot;
      if (nextMode === "choose_location" && !authorized) {
        authorized = await authorizeArchiveRoot();
        setRoot(authorized);
      }
      const result = await planRestore(entry.id, {
        versionId: version?.id,
        mode: nextMode,
        authorizedRootId: authorized?.id,
      });
      setPlan(result);
      setConfirmDirs(!result.requires_directory_confirmation);
      setPolicy("save_as");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "恢复计划创建失败");
    } finally {
      setBusy(false);
    }
  };

  const changeMode = (nextMode: "original" | "choose_location") => {
    setMode(nextMode);
    setRoot(null);
    setPlan(null);
    setError("");
  };

  const runRestore = async () => {
    if (!plan) return;
    if (
      plan.existing &&
      policy === "overwrite" &&
      !window.confirm(
        `确认覆盖“${plan.target}”？请先关闭正在打开该文件的程序。原文件会先在同目录创建临时快照，恢复成功后才删除。`,
      )
    )
      return;
    setBusy(true);
    setError("");
    try {
      const job = await executeRestore(plan.job.id, policy, confirmDirs);
      onComplete(job);
    } catch (reason) {
      // 失败后后端可能已把该计划标为 failed（下载/校验失败），或目标已变化需重新核对；
      // 保留旧计划会让“开始恢复”一直可点却只得到“计划不存在或已执行”。统一要求重新检查。
      setPlan(null);
      const message =
        reason instanceof Error ? reason.message : "恢复执行失败。";
      // 云端对象已被移除等不可恢复错误再提示“重新检查目标”会误导用户反复检查本地目录。
      setError(
        /重新|重试|快照|回滚|无法恢复/.test(message)
          ? message
          : message + "请重新检查目标后再恢复。",
      );
      onFailed?.();
      // 计划区随之卸载，焦点不能掉到 body（模态内焦点丢失），移回“检查”按钮。
      setRefocusPlanAction((count) => count + 1);
    } finally {
      setBusy(false);
    }
  };

  return createPortal(
    <div className="archive-overlay archive-dialog-layer" data-modal-layer>
      <button
        type="button"
        className="archive-overlay-backdrop"
        aria-label="点击遮罩取消恢复"
        disabled={busy}
        onClick={onClose}
      />
      <section
        ref={dialogRef}
        className="archive-restore-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="restore-dialog-title"
        aria-describedby="restore-dialog-description"
        aria-busy={busy}
        tabIndex={-1}
      >
        <header>
          <div>
            <h3 id="restore-dialog-title">恢复“{entry.filename}”</h3>
            <p id="restore-dialog-description">
              先核对目标与文件指纹，再开始恢复。
            </p>
          </div>
          <Button
            ref={closeRef}
            variant="ghost"
            size="icon"
            aria-label="关闭恢复对话框"
            disabled={busy}
            onClick={onClose}
          >
            <X aria-hidden="true" />
          </Button>
        </header>

        <fieldset className="archive-choice-group">
          <legend>恢复位置</legend>
          <label className={mode === "original" ? "is-selected" : undefined}>
            <input
              type="radio"
              name="restore-mode"
              value="original"
              checked={mode === "original"}
              disabled={!originalAvailable || busy}
              onChange={() => changeMode("original")}
            />
            <span>
              <strong>恢复到原路径</strong>
              <small>{entry.original_abs_path ?? "旧记录没有可信原路径"}</small>
            </span>
          </label>
          <label
            className={mode === "choose_location" ? "is-selected" : undefined}
          >
            <input
              type="radio"
              name="restore-mode"
              value="choose_location"
              checked={mode === "choose_location"}
              disabled={busy}
              onChange={() => changeMode("choose_location")}
            />
            <span>
              <strong>另存到所选目录</strong>
              <small>只能通过系统原生目录选择器授权，不接受手输路径。</small>
            </span>
          </label>
        </fieldset>

        {!originalAvailable && (
          <p className="archive-notice" role="note">
            此旧记录不支持恢复到原路径，请选择新的恢复目录。
          </p>
        )}

        <div className="archive-plan-action">
          <Button
            ref={planActionRef}
            type="button"
            variant="outline"
            loading={busy}
            loadingLabel="正在检查…"
            onClick={() =>
              void createPlan(mode, mode === "choose_location" ? null : root)
            }
          >
            {mode === "choose_location" && <FolderOpen aria-hidden="true" />}
            {mode === "choose_location"
              ? root
                ? "重新选择并检查目录"
                : "选择目录并检查"
              : "检查原路径"}
          </Button>
          {root && <small>已授权：{root.path}</small>}
        </div>

        {error && (
          <p className="archive-inline-error" role="alert">
            {error}
          </p>
        )}

        {plan && (
          <div className="archive-restore-plan">
            <dl className="archive-plan-summary">
              <div>
                <dt>恢复目标</dt>
                <dd>{plan.target}</dd>
              </div>
              <div>
                <dt>待创建目录</dt>
                <dd>
                  {plan.missing_directories.length
                    ? plan.missing_directories.join("、")
                    : "无需创建"}
                </dd>
              </div>
            </dl>

            <div
              className="archive-compare-table"
              role="table"
              aria-label="本地与云端文件对照"
            >
              <div role="rowgroup">
                <div role="row">
                  <strong role="columnheader">属性</strong>
                  <strong role="columnheader">现有文件</strong>
                  <strong role="columnheader">云端版本</strong>
                </div>
                <div role="row">
                  <span role="rowheader">大小</span>
                  <span role="cell">
                    {formatSize(plan.existing?.size ?? null)}
                  </span>
                  <span role="cell">{formatSize(plan.expected.size)}</span>
                </div>
                <div role="row">
                  <span role="rowheader">修改时间</span>
                  <span role="cell">
                    {formatArchiveMtime(plan.existing?.mtime_ns)}
                  </span>
                  <span role="cell">
                    {formatArchiveMtime(plan.expected.mtime_ns)}
                  </span>
                </div>
                <div role="row">
                  <span role="rowheader">SHA-256</span>
                  <span role="cell">
                    <HashValue value={plan.existing?.sha256 ?? null} />
                  </span>
                  <span role="cell">
                    <HashValue value={plan.expected.sha256} />
                  </span>
                </div>
              </div>
            </div>

            {plan.existing && (
              <fieldset className="archive-conflict-options">
                <legend>目标冲突处理</legend>
                {conflictOptions.map((option) => (
                  <label
                    key={option.value}
                    className={
                      policy === option.value ? "is-selected" : undefined
                    }
                  >
                    <input
                      type="radio"
                      name="conflict-policy"
                      value={option.value}
                      checked={policy === option.value}
                      disabled={busy}
                      onChange={() => setPolicy(option.value)}
                    />
                    <span>
                      <strong>{option.label}</strong>
                      <small>{option.detail}</small>
                    </span>
                  </label>
                ))}
              </fieldset>
            )}

            {plan.existing && policy === "overwrite" && (
              <p className="archive-notice" role="note">
                将用云端版本替换现有文件；执行前会再次确认，并在同目录创建可回滚快照。
              </p>
            )}

            {plan.requires_directory_confirmation && (
              <label className="archive-directory-confirm">
                <input
                  type="checkbox"
                  checked={confirmDirs}
                  disabled={busy}
                  onChange={(event) => setConfirmDirs(event.target.checked)}
                />
                确认创建上述 {plan.missing_directories.length} 个目录
              </label>
            )}

            <div className="archive-dialog-actions">
              <Button
                type="button"
                variant="outline"
                disabled={busy}
                onClick={onClose}
              >
                取消
              </Button>
              <Button
                type="button"
                loading={busy}
                loadingLabel="正在恢复…"
                variant={policy === "overwrite" ? "outline" : "default"}
                className={
                  policy === "overwrite"
                    ? "archive-overwrite-action"
                    : undefined
                }
                disabled={plan.requires_directory_confirmation && !confirmDirs}
                onClick={() => void runRestore()}
              >
                {policy === "compare"
                  ? "执行对照"
                  : policy === "overwrite"
                    ? "覆盖并恢复"
                    : "开始恢复"}
              </Button>
            </div>
          </div>
        )}
      </section>
    </div>,
    document.body,
  );
}
