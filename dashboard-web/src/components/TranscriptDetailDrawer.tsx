import { ExternalLink, RefreshCw, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";
import { Button } from "@/components/ui/Button";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/Tabs";
import { getTranscriptArtifacts, readTranscriptArtifact } from "@/lib/api";
import type { TranscriptArtifact, TranscriptJob } from "@/lib/types";
import { useModalFocus } from "@/lib/useModalFocus";

type DetailTab = "summary" | "cleaned" | "raw_vtt";

const tabLabels: Record<DetailTab, string> = {
  summary: "本节要点",
  cleaned: "规整字幕",
  raw_vtt: "原始字幕",
};

export function TranscriptDetailDrawer({
  job,
  onClose,
  onRetry,
  onCancel,
  onReveal,
}: {
  job: TranscriptJob;
  onClose: () => void;
  onRetry: (job: TranscriptJob) => void | Promise<void>;
  onCancel: (job: TranscriptJob) => void | Promise<void>;
  onReveal: (artifact: TranscriptArtifact) => void | Promise<void>;
}) {
  const [tab, setTab] = useState<DetailTab>("summary");
  const [artifacts, setArtifacts] = useState<TranscriptArtifact[]>([]);
  const [content, setContent] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const drawerRef = useRef<HTMLElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  useModalFocus(drawerRef, onClose, { initialFocusRef: closeRef });

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    void getTranscriptArtifacts(job.id)
      .then((result) => {
        if (active) setArtifacts(result.items);
      })
      .catch((reason) => {
        if (active)
          setError(
            reason instanceof Error ? reason.message : "字幕工件读取失败",
          );
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [job.id]);

  const currentArtifact = useMemo(
    () => artifacts.find((artifact) => artifact.kind === tab),
    [artifacts, tab],
  );

  useEffect(() => {
    let active = true;
    if (!currentArtifact) {
      setContent("");
      return () => {
        active = false;
      };
    }
    setLoading(true);
    setError("");
    void readTranscriptArtifact(currentArtifact.id)
      .then((result) => {
        if (active) setContent(result.content);
      })
      .catch((reason) => {
        if (active)
          setError(
            reason instanceof Error ? reason.message : "字幕工件读取失败",
          );
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [currentArtifact]);

  const runJobAction = async (
    action: (job: TranscriptJob) => void | Promise<void>,
  ) => {
    setError("");
    try {
      await action(job);
      onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "字幕任务操作失败");
    }
  };

  const active = ["queued", "fetching", "organizing"].includes(job.status);
  const retryable = [
    "waiting_remote",
    "waiting_for_ai",
    "partial",
    "failed",
    "interrupted",
  ].includes(job.status);

  return (
    <div className="transcript-overlay" data-modal-layer>
      <button
        type="button"
        className="transcript-overlay-backdrop"
        aria-label="关闭字幕详情"
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
              className={`transcript-status transcript-status-${job.status}`}
            >
              {job.message || "字幕任务"}
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

        <div className="transcript-progress-block" aria-live="polite">
          <div>
            <span>{job.message || "等待处理"}</span>
            <strong>{Math.max(0, Math.min(100, job.progress))}%</strong>
          </div>
          <div
            className="transcript-progress-track"
            role="progressbar"
            aria-label="字幕整理进度"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.max(0, Math.min(100, job.progress))}
          >
            <span
              style={{ width: `${Math.max(0, Math.min(100, job.progress))}%` }}
            />
          </div>
        </div>

        <div className="transcript-drawer-actions">
          {active && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => void runJobAction(onCancel)}
            >
              取消任务
            </Button>
          )}
          {retryable && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => void runJobAction(onRetry)}
            >
              <RefreshCw aria-hidden="true" />
              重试
            </Button>
          )}
          {currentArtifact && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => void onReveal(currentArtifact)}
            >
              <ExternalLink aria-hidden="true" />在 Finder 中显示
            </Button>
          )}
        </div>

        <Tabs value={tab} onValueChange={(value) => setTab(value as DetailTab)}>
          <TabsList aria-label="字幕结果">
            {(Object.keys(tabLabels) as DetailTab[]).map((value) => (
              <TabsTrigger key={value} value={value}>
                {tabLabels[value]}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>

        <div className="transcript-drawer-content">
          {error ? (
            <p role="alert" className="transcript-inline-error">
              {error}
            </p>
          ) : null}
          {loading ? (
            <p role="status" className="transcript-muted">
              正在读取字幕结果…
            </p>
          ) : currentArtifact ? (
            tab === "raw_vtt" ? (
              <pre className="transcript-raw">{content}</pre>
            ) : (
              <div className="ai-markdown transcript-markdown">
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  rehypePlugins={[rehypeSanitize]}
                >
                  {content}
                </ReactMarkdown>
              </div>
            )
          ) : (
            <p className="transcript-muted">
              {job.status === "completed"
                ? "结果暂不可用，请稍后重试。"
                : "该阶段尚未生成此工件。"}
            </p>
          )}
        </div>
      </section>
    </div>
  );
}
