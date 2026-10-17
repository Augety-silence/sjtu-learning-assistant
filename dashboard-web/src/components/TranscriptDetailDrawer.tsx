import { ExternalLink, RefreshCw, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import ReactMarkdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";
import { ContentReveal } from "@/components/ContentReveal";
import {
  deriveTranscriptJobRecoveryState,
  formatFutureRetryTime,
  isTranscriptQualityRisk,
  TranscriptLearningFlow,
  TranscriptPhase1Panel,
  TranscriptPractice,
  TranscriptQualityWarning,
  transcriptJobFailedStage,
  transcriptStageLabel,
} from "@/components/TranscriptPhase1Panels";
import { Button } from "@/components/ui/Button";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/Tabs";
import {
  getTranscriptArtifacts,
  getTranscriptV2Artifacts,
  readTranscriptArtifact,
  readTranscriptV2Artifact,
} from "@/lib/api";
import type {
  Phase1ArtifactItem,
  Phase1ArtifactList,
  Phase1ArtifactRead,
  TranscriptArtifact,
  TranscriptJob,
} from "@/lib/types";
import { useModalFocus } from "@/lib/useModalFocus";

type V1DetailTab = "summary" | "practice" | "cleaned" | "raw_vtt";
type Phase1DetailTab =
  | "corrected"
  | "correction_diff"
  | "uncertain"
  | "quality"
  | "pipeline_events";
type DetailTab = V1DetailTab | "ai_review";

const v1TabLabels: Record<V1DetailTab, string> = {
  summary: "本节要点",
  practice: "主动练习",
  cleaned: "规整字幕",
  raw_vtt: "原始字幕",
};
const phase1TabLabels: Record<Phase1DetailTab, string> = {
  corrected: "AI校对字幕",
  correction_diff: "修改对比",
  uncertain: "待确认",
  quality: "质量",
  pipeline_events: "AI 用量",
};
const phase1Tabs = Object.keys(phase1TabLabels) as Phase1DetailTab[];

function messageFrom(reason: unknown, fallback: string) {
  return reason instanceof Error ? reason.message : fallback;
}

// Windows builds cannot "reveal in Finder"; match the host shell instead of
// printing a macOS-only label (same detection pattern as MessagesView).
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
  onSeek,
}: {
  job: TranscriptJob;
  initialTab?: V1DetailTab;
  onClose: () => void;
  onRetry?: (job: TranscriptJob) => void | Promise<void>;
  onCancel?: (job: TranscriptJob) => void | Promise<void>;
  onReveal?: (artifact: TranscriptArtifact) => void | Promise<void>;
  onSeek?: (seconds: number) => void;
}) {
  const [tab, setTab] = useState<DetailTab>(initialTab);
  const [phase1Tab, setPhase1Tab] = useState<Phase1DetailTab>("corrected");
  const [artifacts, setArtifacts] = useState<TranscriptArtifact[]>([]);
  const [v1Content, setV1Content] = useState<Record<string, string>>({});
  const [v1Loading, setV1Loading] = useState(true);
  const [v1Error, setV1Error] = useState("");
  const [v1Attempt, setV1Attempt] = useState(0);
  const [actionBusy, setActionBusy] = useState<string | null>(null);
  const actionBusyRef = useRef(false);
  const artifactRevision = `${job.id}:${job.status}:${job.phase1_status ?? job.pipeline_status ?? ""}`;
  const [phase1List, setPhase1List] = useState<Phase1ArtifactList | null>(null);
  const [phase1ListLoading, setPhase1ListLoading] = useState(true);
  const [phase1ListError, setPhase1ListError] = useState("");
  const [phase1ListAttempt, setPhase1ListAttempt] = useState(0);
  const [phase1Content, setPhase1Content] = useState<
    Partial<Record<Phase1DetailTab, Phase1ArtifactRead>>
  >({});
  const [phase1Loading, setPhase1Loading] = useState<
    Partial<Record<Phase1DetailTab, boolean>>
  >({});
  const [phase1Errors, setPhase1Errors] = useState<
    Partial<Record<Phase1DetailTab, string>>
  >({});
  const drawerRef = useRef<HTMLElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const currentJobRef = useRef(artifactRevision);
  currentJobRef.current = artifactRevision;
  const v1CacheRef = useRef(new Map<string, Promise<string> | string>());
  const phase1CacheRef = useRef(
    new Map<
      Phase1DetailTab,
      Promise<Phase1ArtifactRead> | Phase1ArtifactRead
    >(),
  );
  useModalFocus(drawerRef, onClose, { initialFocusRef: closeRef });

  useEffect(() => {
    setTab(initialTab);
    setPhase1Tab("corrected");
    setArtifacts([]);
    setV1Content({});
    setV1Error("");
    setPhase1List(null);
    setPhase1ListError("");
    setPhase1Content({});
    setPhase1Loading({});
    setPhase1Errors({});
    v1CacheRef.current.clear();
    phase1CacheRef.current.clear();
  }, [initialTab, job.id]);

  useEffect(() => {
    v1CacheRef.current.clear();
    phase1CacheRef.current.clear();
    setV1Content({});
    setPhase1Content({});
    setPhase1Errors({});
  }, [artifactRevision]);

  useEffect(() => {
    let active = true;
    setV1Loading(true);
    setV1Error("");
    void getTranscriptArtifacts(job.id)
      .then((result) => {
        if (active)
          setArtifacts(Array.isArray(result?.items) ? result.items : []);
      })
      .catch((reason) => {
        if (active) setV1Error(messageFrom(reason, "字幕工件读取失败"));
      })
      .finally(() => {
        if (active) setV1Loading(false);
      });
    return () => {
      active = false;
    };
  }, [artifactRevision, v1Attempt]);

  useEffect(() => {
    let active = true;
    setPhase1ListLoading(true);
    setPhase1ListError("");
    void getTranscriptV2Artifacts(job.id)
      .then((result) => {
        if (active) setPhase1List(result);
      })
      .catch((reason) => {
        if (active)
          setPhase1ListError(messageFrom(reason, "深度校对状态读取失败"));
      })
      .finally(() => {
        if (active) setPhase1ListLoading(false);
      });
    return () => {
      active = false;
    };
  }, [artifactRevision, phase1ListAttempt]);

  const currentV1Artifact = useMemo(() => {
    if (tab === "ai_review") return undefined;
    if (tab === "summary" || tab === "practice")
      return (
        artifacts.find((artifact) => artifact.kind === "summary_json") ??
        artifacts.find((artifact) => artifact.kind === "summary")
      );
    return artifacts.find((artifact) => artifact.kind === tab);
  }, [artifacts, tab]);
  const phase1Artifacts = useMemo(() => {
    const items: unknown = phase1List?.items;
    if (!Array.isArray(items))
      return new Map<Phase1DetailTab, Phase1ArtifactItem & { id: string }>();
    return new Map(
      items
        .filter(
          (item): item is Phase1ArtifactItem & { id: string } =>
            item !== null &&
            typeof item === "object" &&
            "kind" in item &&
            phase1Tabs.includes(item.kind as Phase1DetailTab) &&
            "available" in item &&
            item.available === true &&
            "id" in item &&
            typeof item.id === "string",
        )
        .map((item) => [item.kind as Phase1DetailTab, item]),
    );
  }, [phase1List?.items]);
  const phase1Available =
    phase1List?.available === true && phase1Artifacts.size > 0;

  useEffect(() => {
    if (!phase1Available || phase1Artifacts.has(phase1Tab)) return;
    const firstAvailable = phase1Tabs.find((kind) => phase1Artifacts.has(kind));
    if (firstAvailable) setPhase1Tab(firstAvailable);
  }, [phase1Artifacts, phase1Available, phase1Tab]);

  useEffect(() => {
    let active = true;
    if (!currentV1Artifact) return () => undefined;
    const requestedRevision = artifactRevision;
    const cached = v1CacheRef.current.get(currentV1Artifact.id);
    setV1Loading(true);
    setV1Error("");
    const request =
      typeof cached === "string"
        ? Promise.resolve(cached)
        : (cached ??
          readTranscriptArtifact(currentV1Artifact.id).then(
            (result) => result.content,
          ));
    v1CacheRef.current.set(currentV1Artifact.id, request);
    void request
      .then((content) => {
        if (currentJobRef.current !== requestedRevision) return;
        v1CacheRef.current.set(currentV1Artifact.id, content);
        if (active)
          setV1Content((current) => ({
            ...current,
            [currentV1Artifact.id]: content,
          }));
      })
      .catch((reason) => {
        if (currentJobRef.current !== requestedRevision) return;
        v1CacheRef.current.delete(currentV1Artifact.id);
        if (active) setV1Error(messageFrom(reason, "字幕工件读取失败"));
      })
      .finally(() => {
        if (active) setV1Loading(false);
      });
    return () => {
      active = false;
    };
  }, [currentV1Artifact, artifactRevision, v1Attempt]);

  const loadPhase1Artifact = (
    kind: Phase1DetailTab,
    artifact: Phase1ArtifactItem & { id: string },
    force = false,
  ) => {
    const requestedJob = artifactRevision;
    if (force) phase1CacheRef.current.delete(kind);
    const cached = phase1CacheRef.current.get(kind);
    if (cached && !(cached instanceof Promise)) {
      setPhase1Content((current) => ({ ...current, [kind]: cached }));
      return;
    }
    setPhase1Loading((current) => ({ ...current, [kind]: true }));
    setPhase1Errors((current) => ({ ...current, [kind]: "" }));
    const request = cached ?? readTranscriptV2Artifact(artifact.id);
    phase1CacheRef.current.set(kind, request);
    void Promise.resolve(request)
      .then((result) => {
        if (
          currentJobRef.current === requestedJob &&
          phase1CacheRef.current.get(kind) === request
        ) {
          phase1CacheRef.current.set(kind, result);
          setPhase1Content((current) => ({ ...current, [kind]: result }));
        }
      })
      .catch((reason) => {
        if (
          currentJobRef.current === requestedJob &&
          phase1CacheRef.current.get(kind) === request
        ) {
          phase1CacheRef.current.delete(kind);
          setPhase1Errors((current) => ({
            ...current,
            [kind]: messageFrom(reason, "深度校对内容读取失败"),
          }));
        }
      })
      .finally(() => {
        if (currentJobRef.current === requestedJob)
          setPhase1Loading((current) => ({ ...current, [kind]: false }));
      });
  };

  useEffect(() => {
    if (tab !== "ai_review") return;
    const artifact = phase1Artifacts.get(phase1Tab);
    if (artifact) loadPhase1Artifact(phase1Tab, artifact);
  }, [phase1Artifacts, phase1Tab, tab]);

  const runJobAction = async (
    key: string,
    action?: (job: TranscriptJob) => void | Promise<void>,
    close = true,
  ) => {
    if (!action || actionBusyRef.current) return;
    actionBusyRef.current = true;
    setActionBusy(key);
    setV1Error("");
    try {
      await action(job);
      if (close) onClose();
    } catch (reason) {
      setV1Error(messageFrom(reason, "字幕任务操作失败"));
    } finally {
      actionBusyRef.current = false;
      setActionBusy(null);
    }
  };

  const active =
    (job.progress < 100 || job.review_status === "running") &&
    [
      "queued",
      "fetching",
      "saved",
      "waiting_remote",
      "waiting_for_ai",
      "organizing",
      "reviewing",
    ].includes(job.status);
  const missingReview =
    !phase1ListLoading && !phase1ListError && !phase1Available;
  const retryable =
    (job.status === "waiting_remote" && job.progress >= 100) ||
    ["partial", "failed", "interrupted", "completed_with_warnings"].includes(
      job.status,
    ) ||
    (job.status === "completed" &&
      (missingReview ||
        isTranscriptQualityRisk(phase1List?.quality ?? job.quality) ||
        ["partial", "failed", "completed_with_warnings"].includes(
          job.phase1_status ?? job.pipeline_status ?? "",
        )));
  const phase1Warning =
    job.partial_warning === true ||
    isTranscriptQualityRisk(phase1List?.quality ?? job.quality) ||
    ["partial", "failed", "completed_with_warnings"].includes(
      job.phase1_status ?? job.pipeline_status ?? phase1List?.status ?? "",
    );
  const currentPhase1 = phase1Content[phase1Tab];
  const currentPhase1Error = phase1Errors[phase1Tab] ?? "";
  const currentPhase1Loading = phase1Loading[phase1Tab] === true;
  const manifestWarnings = Array.isArray(phase1List?.warnings)
    ? phase1List.warnings.filter(
        (warning): warning is string => typeof warning === "string",
      )
    : [];
  const reviewProgress =
    job.review_status === "running" && (job.review_total ?? 0) > 0
      ? Math.round(
          ((job.review_completed ?? 0) / (job.review_total ?? 1)) * 100,
        )
      : null;
  const progress = Math.max(0, Math.min(100, reviewProgress ?? job.progress));
  const hasAvailableResult = artifacts.length > 0 || phase1Available;
  const showPhase1Warning =
    phase1Warning && (job.status !== "failed" || hasAvailableResult);
  const resultTone = active
    ? "processing"
    : ["partial", "completed_with_warnings"].includes(job.status) ||
        showPhase1Warning ||
        (job.status === "failed" && hasAvailableResult)
      ? "partial"
      : job.status === "failed" || job.status === "interrupted"
        ? "failed"
        : job.status === "completed"
          ? "completed"
          : "pending";
  // Finished review vs. pending / exhausted recovery; null keeps legacy copy.
  const recovery = deriveTranscriptJobRecoveryState(job, active);
  const recoveryRetryTime =
    recovery === "recovering"
      ? formatFutureRetryTime(
          (job as TranscriptJob & { next_retry_at?: string | null })
            .next_retry_at ?? job.retry_after,
        )
      : null;
  const failedStage =
    recovery === "recovering" || recovery === "recovery_exhausted"
      ? transcriptStageLabel(transcriptJobFailedStage(job))
      : null;
  const progressMessage =
    recovery === "finished_needs_review"
      ? "校对已结束，部分内容需核对"
      : recovery === "recovery_exhausted"
        ? "自动恢复已用尽，可手动重试"
        : recovery === "recovering"
          ? recoveryRetryTime
            ? `${failedStage}未完成，等待自动恢复（预计 ${recoveryRetryTime}）`
            : `${failedStage}未完成，等待自动恢复`
          : job.status === "completed_with_warnings" || job.status === "partial"
            ? "任务已结束，存在未通过或未完成阶段"
            : job.message || "等待处理";
  const resultLabel = active
    ? "正在处理"
    : resultTone === "partial"
      ? "已有可用结果"
      : resultTone === "failed"
        ? "暂无可用结果"
        : resultTone === "completed"
          ? "处理完成"
          : "等待处理";

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
            <div className="transcript-heading-statuses">
              <span
                className={`transcript-status transcript-status-${resultTone}`}
              >
                {resultLabel}
              </span>
              {showPhase1Warning && (
                <span className="transcript-phase1-warning" role="status">
                  {recovery === "finished_needs_review"
                    ? "部分内容需核对"
                    : "深度校对部分完成"}
                </span>
              )}
            </div>
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

        <div className="transcript-progress-block">
          <div>
            <span aria-live="polite">{progressMessage}</span>
            <strong>{progress}%</strong>
          </div>
          {failedStage ? (
            <p>失败阶段：{failedStage}</p>
          ) : (
            <p>当前阶段：{job.stage || "状态未知"}</p>
          )}
          {(job.error_code || job.error_message || job.error) && (
            <p role="alert">
              {[job.error_code, job.error_message || job.error]
                .filter(Boolean)
                .join("：")}
            </p>
          )}
          {job.updated_at && (
            <small>最近更新：{new Date(job.updated_at).toLocaleString()}</small>
          )}
          <div
            className="transcript-progress-track"
            role="progressbar"
            aria-label="字幕整理进度"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={progress}
          >
            <span style={{ width: `${progress}%` }} />
          </div>
        </div>

        {((active && onCancel) ||
          (retryable && onRetry) ||
          (currentV1Artifact && onReveal)) && (
          <details className="transcript-drawer-actions">
            <summary>更多操作</summary>
            <div>
              {active && onCancel && (
                <Button
                  variant="outline"
                  size="sm"
                  loading={actionBusy === "cancel"}
                  disabled={Boolean(actionBusy)}
                  onClick={() => void runJobAction("cancel", onCancel)}
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
                  onClick={() => void runJobAction("retry", onRetry)}
                >
                  <RefreshCw aria-hidden="true" />
                  重试
                </Button>
              )}
              {currentV1Artifact && onReveal && (
                <Button
                  variant="outline"
                  size="sm"
                  loading={actionBusy === "reveal"}
                  disabled={Boolean(actionBusy)}
                  onClick={() =>
                    void runJobAction(
                      "reveal",
                      () => onReveal(currentV1Artifact),
                      false,
                    )
                  }
                >
                  <ExternalLink aria-hidden="true" />
                  {revealInFilesLabel()}
                </Button>
              )}
            </div>
          </details>
        )}

        {isTranscriptQualityRisk(phase1List?.quality ?? job.quality) && (
          <details className="transcript-quality-diagnostics">
            <summary>质量诊断：AI 校对未通过</summary>
            <TranscriptQualityWarning
              quality={phase1List?.quality ?? job.quality}
            />
          </details>
        )}

        <Tabs value={tab} onValueChange={(value) => setTab(value as DetailTab)}>
          <TabsList aria-label="字幕结果">
            {(Object.keys(v1TabLabels) as V1DetailTab[]).map((value) => (
              <TabsTrigger key={value} value={value}>
                {v1TabLabels[value]}
              </TabsTrigger>
            ))}
            <TabsTrigger value="ai_review">AI 校对</TabsTrigger>
          </TabsList>
        </Tabs>

        <ContentReveal
          key={tab === "ai_review" ? phase1Tab : tab}
          className="transcript-drawer-content"
          variant="detail"
          ready={
            tab === "ai_review"
              ? !phase1ListLoading && !currentPhase1Loading
              : !v1Loading
          }
        >
          {tab === "ai_review" ? (
            <section
              className="phase1-section is-active"
              aria-labelledby="phase1-section-title"
            >
              <div className="phase1-section-heading">
                <div>
                  <h4 id="phase1-section-title">AI 校对</h4>
                  <p>课程语境驱动的深度校对结果</p>
                </div>
                {phase1ListLoading && <span role="status">正在检查…</span>}
              </div>
              {!phase1ListLoading && phase1ListError && (
                <div className="phase1-inline-state" role="alert">
                  <span>{phase1ListError}</span>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setPhase1ListAttempt((value) => value + 1)}
                  >
                    重试加载
                  </Button>
                </div>
              )}
              {!phase1ListLoading && !phase1ListError && !phase1Available && (
                <div className="phase1-legacy-note">
                  <p>本节尚未生成深度校对，可重新执行字幕 AI 整理</p>
                  {retryable && onRetry ? (
                    <Button
                      variant="outline"
                      size="sm"
                      loading={actionBusy === "retry"}
                      disabled={Boolean(actionBusy)}
                      onClick={() => void runJobAction("retry", onRetry)}
                    >
                      补充生成 AI 校对
                    </Button>
                  ) : !active ? (
                    <p>当前来源或 AI 配置不支持补充整理；已有材料仍可阅读。</p>
                  ) : (
                    <p>任务仍在处理中，完成后会更新结果。</p>
                  )}
                </div>
              )}
              {phase1Available && (
                <Tabs
                  value={phase1Tab}
                  onValueChange={(value) =>
                    setPhase1Tab(value as Phase1DetailTab)
                  }
                >
                  <TabsList className="phase1-tabs" aria-label="AI 校对结果">
                    {phase1Tabs.map((value) => (
                      <TabsTrigger
                        key={value}
                        value={value}
                        disabled={!phase1Artifacts.has(value)}
                      >
                        {phase1TabLabels[value]}
                      </TabsTrigger>
                    ))}
                  </TabsList>
                </Tabs>
              )}
              {phase1Available &&
                (currentPhase1Error ? (
                  <div className="phase1-inline-state" role="alert">
                    <span>{currentPhase1Error}</span>
                    {phase1Artifacts.get(phase1Tab) && (
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() =>
                          loadPhase1Artifact(
                            phase1Tab,
                            phase1Artifacts.get(
                              phase1Tab,
                            ) as Phase1ArtifactItem & { id: string },
                            true,
                          )
                        }
                      >
                        重试加载
                      </Button>
                    )}
                  </div>
                ) : currentPhase1Loading ? (
                  <p role="status" className="transcript-muted">
                    正在读取{phase1TabLabels[phase1Tab]}…
                  </p>
                ) : currentPhase1 ? (
                  <TranscriptPhase1Panel
                    kind={phase1Tab}
                    artifact={currentPhase1}
                    manifestWarnings={manifestWarnings}
                  />
                ) : (
                  <p className="transcript-muted">该校对工件暂不可用。</p>
                ))}
            </section>
          ) : v1Error ? (
            <div role="alert" className="transcript-inline-error">
              <p>{v1Error}</p>
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  v1CacheRef.current.clear();
                  setV1Attempt((value) => value + 1);
                }}
              >
                重试加载学习材料
              </Button>
            </div>
          ) : v1Loading ? (
            <p role="status" className="transcript-muted">
              正在读取字幕结果…
            </p>
          ) : currentV1Artifact ? (
            tab === "raw_vtt" ? (
              <pre className="transcript-raw">
                {v1Content[currentV1Artifact.id] ?? ""}
              </pre>
            ) : tab === "summary" ? (
              <TranscriptLearningFlow
                content={v1Content[currentV1Artifact.id] ?? ""}
                onSeek={onSeek}
              />
            ) : tab === "practice" ? (
              <TranscriptPractice
                content={v1Content[currentV1Artifact.id] ?? ""}
                onSeek={onSeek}
              />
            ) : (
              <div className="ai-markdown transcript-markdown">
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  rehypePlugins={[rehypeSanitize]}
                >
                  {v1Content[currentV1Artifact.id] ?? ""}
                </ReactMarkdown>
              </div>
            )
          ) : (
            <div className="transcript-muted">
              <p>
                {[
                  "completed",
                  "completed_with_warnings",
                  "partial",
                  "failed",
                  "interrupted",
                  "cancelled",
                ].includes(job.status)
                  ? "这节任务已结束，但没有此类学习材料；可查看其他结果或补充整理。"
                  : "该阶段尚未生成此类学习材料，完成后会自动更新。"}
              </p>
              {!active && onRetry && (
                <Button
                  variant="outline"
                  size="sm"
                  loading={actionBusy === "retry"}
                  disabled={Boolean(actionBusy)}
                  onClick={() => void runJobAction("retry", onRetry)}
                >
                  补充整理学习材料
                </Button>
              )}
            </div>
          )}
        </ContentReveal>
      </section>
    </div>,
    document.body,
  );
}
