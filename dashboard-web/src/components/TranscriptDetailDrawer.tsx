import { ExternalLink, RefreshCw, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import ReactMarkdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";
import { ContentReveal } from "@/components/ContentReveal";
import { Button } from "@/components/ui/Button";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/Tabs";
import { getTranscriptArtifacts, readTranscriptArtifact } from "@/lib/api";
import type { TranscriptArtifact, TranscriptJob } from "@/lib/types";
import { useModalFocus } from "@/lib/useModalFocus";

type DetailTab = "summary" | "raw_vtt";
// Older callers may request a legacy tab; never restore the removed UI.
type InitialTab = DetailTab | "practice" | "cleaned";
const tabLabels: Record<DetailTab, string> = {
  summary: "当堂老师讲了什么",
  raw_vtt: "原始字幕",
};
const normalizeTab = (value: InitialTab): DetailTab =>
  value === "raw_vtt" ? "raw_vtt" : "summary";

function messageFrom(reason: unknown, fallback: string) {
  return reason instanceof Error ? reason.message : fallback;
}

function revealInFilesLabel() {
  const platform = navigator.platform || navigator.userAgent;
  return /Mac|iPhone|iPad/i.test(platform)
    ? "在 Finder 中显示"
    : /Win/i.test(platform)
      ? "在资源管理器中显示"
      : "在文件管理器中显示";
}

export function TranscriptDetailDrawer({
  job,
  initialTab = "summary",
  onClose,
  onRetry,
  onCancel,
  onReveal,
}: {
  job: TranscriptJob;
  initialTab?: InitialTab;
  onClose: () => void;
  onRetry?: (job: TranscriptJob) => void | Promise<void>;
  onCancel?: (job: TranscriptJob) => void | Promise<void>;
  onReveal?: (artifact: TranscriptArtifact) => void | Promise<void>;
  onSeek?: (seconds: number) => void;
}) {
  const [tab, setTab] = useState<DetailTab>(normalizeTab(initialTab));
  const [artifacts, setArtifacts] = useState<TranscriptArtifact[]>([]);
  const [content, setContent] = useState<Record<string, string>>({});
  const [listLoading, setListLoading] = useState(true);
  const [bodyLoading, setBodyLoading] = useState(false);
  const [listError, setListError] = useState("");
  const [bodyError, setBodyError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [actionError, setActionError] = useState("");
  const [actionBusy, setActionBusy] = useState<string | null>(null);
  const actionBusyRef = useRef(false);
  const drawerRef = useRef<HTMLElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const revision = `${job.id}:${job.status}:${job.lesson_notes_status ?? ""}`;
  const currentRevision = useRef(revision);
  currentRevision.current = revision;
  const cache = useRef(new Map<string, Promise<string> | string>());
  // Scope lists as well as bodies: a previous lesson must never flash here.
  const [listedRevision, setListedRevision] = useState("");
  useModalFocus(drawerRef, onClose, { initialFocusRef: closeRef });

  useEffect(() => {
    setTab(normalizeTab(initialTab));
    setActionError("");
  }, [initialTab, job.id]);

  useEffect(() => {
    let active = true;
    cache.current.clear();
    setContent({});
    setArtifacts([]);
    setListedRevision("");
    setListLoading(true);
    setListError("");
    setBodyError("");
    void getTranscriptArtifacts(job.id)
      .then((result) => {
        if (!active) return;
        const items: unknown = result?.items;
        setArtifacts(
          Array.isArray(items)
            ? items.filter(
                (item): item is TranscriptArtifact =>
                  item !== null &&
                  typeof item === "object" &&
                  typeof item.id === "string" &&
                  typeof item.kind === "string",
              )
            : [],
        );
        setListedRevision(revision);
      })
      .catch((reason) => {
        if (active) setListError(messageFrom(reason, "字幕材料列表读取失败"));
      })
      .finally(() => {
        if (active) setListLoading(false);
      });
    return () => {
      active = false;
    };
  }, [revision, attempt]);

  const artifact = useMemo(() => {
    if (listedRevision !== revision) return undefined;
    if (tab === "summary")
      return artifacts.find((item) => item.kind === "lesson_summary");
    return (
      artifacts.find((item) => item.kind === "original") ??
      artifacts.find((item) => item.kind === "raw_vtt")
    );
  }, [artifacts, listedRevision, revision, tab]);

  useEffect(() => {
    let active = true;
    setBodyError("");
    if (!artifact) {
      setBodyLoading(false);
      return;
    }
    const requestedRevision = revision;
    const cached = cache.current.get(artifact.id);
    setBodyLoading(true);
    const request =
      typeof cached === "string"
        ? Promise.resolve(cached)
        : (cached ??
          readTranscriptArtifact(artifact.id).then((result) => result.content));
    cache.current.set(artifact.id, request);
    void request
      .then((text) => {
        if (currentRevision.current !== requestedRevision) return;
        cache.current.set(artifact.id, text);
        if (active)
          setContent((previous) => ({ ...previous, [artifact.id]: text }));
      })
      .catch((reason) => {
        if (currentRevision.current !== requestedRevision) return;
        cache.current.delete(artifact.id);
        if (active) setBodyError(messageFrom(reason, "字幕正文读取失败"));
      })
      .finally(() => {
        if (active) setBodyLoading(false);
      });
    return () => {
      active = false;
    };
  }, [artifact, revision]);

  const runAction = async (
    key: string,
    action: () => void | Promise<void>,
    close = true,
  ) => {
    if (actionBusyRef.current) return;
    const requestedRevision = revision;
    actionBusyRef.current = true;
    setActionBusy(key);
    setActionError("");
    try {
      await action();
      if (close && currentRevision.current === requestedRevision) onClose();
    } catch (reason) {
      if (currentRevision.current === requestedRevision)
        setActionError(messageFrom(reason, "字幕任务操作失败"));
    } finally {
      actionBusyRef.current = false;
      setActionBusy(null);
    }
  };

  const active =
    [
      "queued",
      "fetching",
      "saved",
      "waiting_remote",
      "waiting_for_ai",
      "organizing",
      "reviewing",
    ].includes(job.status) &&
    (job.progress < 100 || job.review_status === "running");
  const notesReady =
    job.lesson_notes_status === "completed" ||
    job.lesson_notes_status === "user_modified";
  const hasResult =
    listedRevision === revision &&
    artifacts.some((item) =>
      ["lesson_summary", "original", "raw_vtt"].includes(item.kind),
    );
  const retryable = !active && (!notesReady || !hasResult);
  const resultLabel =
    notesReady && hasResult
      ? "课堂材料已就绪"
      : hasResult
        ? "原始字幕可阅读"
        : active
          ? "正在处理"
          : "暂无可用材料";
  const resultTone =
    notesReady && hasResult
      ? "completed"
      : hasResult
        ? "partial"
        : active
          ? "processing"
          : "pending";
  const error = listError || bodyError;

  return createPortal(
    <div className="transcript-overlay" data-modal-layer>
      <button
        type="button"
        className="transcript-overlay-backdrop"
        aria-label="关闭字幕详情"
        aria-hidden="true"
        tabIndex={-1}
        onClick={onClose}
      />
      <section
        ref={drawerRef}
        className="transcript-detail-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="transcript-detail-title"
        tabIndex={-1}
      >
        <header className="transcript-drawer-header">
          <div>
            <span
              className={`transcript-status transcript-status-${resultTone}`}
            >
              {resultLabel}
            </span>
            <h3 id="transcript-detail-title">{job.title}</h3>
          </div>
          <Button
            ref={closeRef}
            variant="ghost"
            size="icon"
            aria-label="关闭字幕详情"
            onClick={onClose}
          >
            <X aria-hidden="true" />
          </Button>
        </header>
        {active && !notesReady && (
          <p role="status" className="transcript-muted">
            课堂材料正在生成；已有原始字幕可先阅读。
          </p>
        )}
        {actionError && (
          <div role="alert" className="transcript-inline-error">
            <p>{actionError}</p>
          </div>
        )}
        {((active && onCancel) ||
          (retryable && onRetry) ||
          (artifact && onReveal)) && (
          <details className="transcript-drawer-actions">
            <summary>更多操作</summary>
            <div>
              {active && onCancel && (
                <Button
                  variant="outline"
                  size="sm"
                  loading={actionBusy === "cancel"}
                  disabled={Boolean(actionBusy)}
                  onClick={() => void runAction("cancel", () => onCancel(job))}
                >
                  取消任务
                </Button>
              )}
              {retryable && onRetry && (
                <Button
                  variant="outline"
                  size="sm"
                  loading={actionBusy === "retry"}
                  disabled={Boolean(actionBusy)}
                  onClick={() => void runAction("retry", () => onRetry(job))}
                >
                  <RefreshCw aria-hidden="true" />
                  重新生成课堂材料
                </Button>
              )}
              {artifact && onReveal && (
                <Button
                  variant="outline"
                  size="sm"
                  loading={actionBusy === "reveal"}
                  disabled={Boolean(actionBusy)}
                  onClick={() =>
                    void runAction("reveal", () => onReveal(artifact), false)
                  }
                >
                  <ExternalLink aria-hidden="true" />
                  {revealInFilesLabel()}
                </Button>
              )}
            </div>
          </details>
        )}
        <Tabs value={tab} onValueChange={(value) => setTab(value as DetailTab)}>
          <TabsList indicator aria-label="字幕结果">
            {(["summary", "raw_vtt"] as DetailTab[]).map((value) => (
              <TabsTrigger key={value} value={value}>
                {tabLabels[value]}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
        <ContentReveal
          key={`${revision}:${tab}`}
          className="transcript-artifact-body"
        >
          {error ? (
            <div role="alert" className="transcript-inline-error">
              <p>{error}</p>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setAttempt((value) => value + 1)}
              >
                重试加载学习材料
              </Button>
            </div>
          ) : listLoading || bodyLoading || listedRevision !== revision ? (
            <p role="status" className="transcript-muted">
              正在读取字幕结果…
            </p>
          ) : artifact ? (
            artifact.kind === "raw_vtt" ? (
              <pre className="transcript-raw" style={{ userSelect: "text" }}>
                {content[artifact.id] ?? ""}
              </pre>
            ) : (
              <div
                className="ai-markdown transcript-markdown"
                style={{ userSelect: "text" }}
              >
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  rehypePlugins={[rehypeSanitize]}
                >
                  {content[artifact.id] ?? ""}
                </ReactMarkdown>
              </div>
            )
          ) : (
            <div className="transcript-muted">
              <p>
                {tab === "summary"
                  ? "精炼课堂总结尚未完成；已有原始字幕可完整阅读和检索。"
                  : "原始字幕尚未生成，完成后会自动更新。"}
              </p>
            </div>
          )}
        </ContentReveal>
      </section>
    </div>,
    document.body,
  );
}
