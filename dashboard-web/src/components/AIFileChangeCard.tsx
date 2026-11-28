import { Check, ChevronRight, FilePen, LoaderCircle, X } from "lucide-react";
import { useEffect, useId, useState } from "react";
import {
  applyAiFileChange,
  getAiFileChange,
  rejectAiFileChange,
} from "@/lib/api";
import type { AIFileChange, AIToolRun } from "@/lib/types";

const statusLabels: Record<AIFileChange["status"], string> = {
  pending: "待确认",
  applied: "已写入",
  rejected: "已放弃",
  conflict: "未写入",
  failed: "写入失败",
};

/** 从本轮工具调用中取出 AI 生成的文件修改提议（同一提议多次 append 只算一次）。 */
export function fileChangeIds(runs: AIToolRun[] | undefined): string[] {
  const ids: string[] = [];
  for (const run of runs ?? []) {
    if (run.tool_name !== "propose_file_change" || run.status !== "ok")
      continue;
    const summary = run.result_summary;
    const id =
      summary && typeof summary === "object"
        ? (summary as Record<string, unknown>).change_id
        : null;
    if (
      typeof id === "string" &&
      /^[a-z0-9]{32}$/.test(id) &&
      !ids.includes(id)
    )
      ids.push(id);
  }
  return ids;
}

function DiffView({ diff }: { diff: string }) {
  const lines = diff.split("\n");
  return (
    <pre className="ai-file-diff" aria-label="修改差异">
      {lines.map((line, index) => {
        const kind =
          line.startsWith("+++") || line.startsWith("---")
            ? "meta"
            : line.startsWith("@@")
              ? "hunk"
              : line.startsWith("+")
                ? "add"
                : line.startsWith("-")
                  ? "del"
                  : "ctx";
        return (
          <span className={`ai-diff-line is-${kind}`} key={`${index}-${line}`}>
            {line || " "}
          </span>
        );
      })}
    </pre>
  );
}

export function AIFileChangeCard({ changeId }: { changeId: string }) {
  const [change, setChange] = useState<AIFileChange | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"apply" | "reject" | null>(null);
  const [open, setOpen] = useState(false);
  const [reload, setReload] = useState(0);
  const diffId = useId();

  useEffect(() => {
    let cancelled = false;
    setChange(null);
    setError(null);
    getAiFileChange(changeId)
      .then((value) => {
        if (!cancelled) setChange(value);
      })
      .catch((reason: unknown) => {
        if (!cancelled)
          setError(
            reason instanceof Error ? reason.message : "无法读取修改提议。",
          );
      });
    return () => {
      cancelled = true;
    };
  }, [changeId, reload]);

  const act = async (kind: "apply" | "reject") => {
    if (busy) return;
    setBusy(kind);
    setError(null);
    try {
      setChange(
        await (kind === "apply" ? applyAiFileChange : rejectAiFileChange)(
          changeId,
        ),
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "操作失败，请重试。");
    } finally {
      setBusy(null);
    }
  };

  if (!change) {
    return (
      <section className="ai-file-change is-loading" aria-label="文件修改提议">
        <FilePen aria-hidden="true" />
        <span role={error ? "alert" : "status"}>
          {error ?? "正在读取修改提议…"}
        </span>
        {error && (
          <button type="button" onClick={() => setReload((value) => value + 1)}>
            重新读取
          </button>
        )}
      </section>
    );
  }

  return (
    <section
      className={`ai-file-change is-${change.status}`}
      aria-label={`文件修改提议：${change.path}`}
    >
      <header>
        <span className="ai-file-change-icon">
          <FilePen aria-hidden="true" />
        </span>
        <span className="ai-file-change-title">
          <strong title={change.path}>{change.path}</strong>
          <small>
            {change.mode === "create" ? "新建文件" : "修改文件"}
            {change.workspace ? ` · ${change.workspace}` : ""} ·{" "}
            <span className="ai-diff-count is-add">+{change.added}</span>{" "}
            <span className="ai-diff-count is-del">−{change.removed}</span> 行
          </small>
        </span>
        <span className={`ai-file-change-status is-${change.status}`}>
          {statusLabels[change.status]}
        </span>
      </header>
      {change.summary && (
        <p className="ai-file-change-summary">{change.summary}</p>
      )}
      {(change.error || error) && (
        <p className="ai-file-change-error" role="alert">
          {change.error || error}
        </p>
      )}
      <footer className="ai-file-change-footer">
        <button
          type="button"
          className="ai-file-change-toggle"
          aria-expanded={open}
          aria-controls={diffId}
          onClick={() => setOpen((value) => !value)}
        >
          <ChevronRight aria-hidden="true" />
          {open ? "收起差异" : "查看差异"}
        </button>
        {change.status === "pending" && (
          <div className="ai-file-change-actions">
            <button
              type="button"
              disabled={busy !== null}
              onClick={() => void act("reject")}
            >
              <X aria-hidden="true" />
              放弃
            </button>
            <button
              type="button"
              className="is-primary"
              disabled={busy !== null}
              onClick={() => void act("apply")}
            >
              {busy === "apply" ? (
                <LoaderCircle className="animate-spin" aria-hidden="true" />
              ) : (
                <Check aria-hidden="true" />
              )}
              {busy === "apply" ? "正在写入…" : "确认写入"}
            </button>
          </div>
        )}
      </footer>
      {open && (
        <div id={diffId}>
          <DiffView diff={change.diff} />
          {change.diff_truncated && (
            <p className="ai-file-change-note">差异较长，只显示前 600 行。</p>
          )}
        </div>
      )}
      {change.status === "pending" && (
        <p className="ai-file-change-note">
          {change.mode === "create"
            ? "确认后在工作区新建文件。"
            : "确认后写入，原文件会先备份。"}
        </p>
      )}
      {change.status === "applied" && (
        <p className="ai-file-change-note">
          {change.mode === "create"
            ? "文件已创建到工作区。"
            : "已写入工作区，原文件已备份。"}
        </p>
      )}
    </section>
  );
}
