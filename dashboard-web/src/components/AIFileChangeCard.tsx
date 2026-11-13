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
  const diffId = useId();

  useEffect(() => {
    let cancelled = false;
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
  }, [changeId]);

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
        <span>{error ?? "正在读取修改提议…"}</span>
      </section>
    );
  }

  return (
    <section
      className={`ai-file-change is-${change.status}`}
      aria-label={`文件修改提议：${change.path}`}
    >
      <header>
        <FilePen aria-hidden="true" />
        <span className="ai-file-change-title">
          <strong title={change.path}>{change.path}</strong>
          <small>
            {change.mode === "create" ? "新建文件" : "修改文件"}
            {change.workspace ? ` · ${change.workspace}` : ""} ·{" "}
            <span className="ai-diff-count is-add">+{change.added}</span>{" "}
            <span className="ai-diff-count is-del">−{change.removed}</span>
          </small>
        </span>
        <span className={`ai-file-change-status is-${change.status}`}>
          {statusLabels[change.status]}
        </span>
      </header>
      {change.summary && (
        <p className="ai-file-change-summary">{change.summary}</p>
      )}
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
      {open && (
        <div id={diffId}>
          <DiffView diff={change.diff} />
          {change.diff_truncated && (
            <p className="ai-file-change-note">差异较长，只显示前 600 行。</p>
          )}
        </div>
      )}
      {(change.error || error) && (
        <p className="ai-file-change-error" role="alert">
          {change.error || error}
        </p>
      )}
      {change.status === "pending" ? (
        <div className="ai-file-change-actions">
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
            应用修改
          </button>
          <button
            type="button"
            disabled={busy !== null}
            onClick={() => void act("reject")}
          >
            <X aria-hidden="true" />
            放弃
          </button>
          <small>写入前会备份原文件，不会删除或移动任何文件。</small>
        </div>
      ) : (
        change.status === "applied" && (
          <p className="ai-file-change-note">
            已写入工作区，原文件已备份到应用数据目录。
          </p>
        )
      )}
    </section>
  );
}
