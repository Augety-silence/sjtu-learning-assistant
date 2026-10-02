import { ExternalLink, RefreshCw, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";
import {
  isTranscriptQualityRisk,
  TranscriptLearningFlow,
  TranscriptPhase1Panel,
  TranscriptPractice,
  TranscriptQualityWarning,
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
  | "quality";
type DetailTab = V1DetailTab | Phase1DetailTab;

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
};
const phase1Tabs = Object.keys(phase1TabLabels) as Phase1DetailTab[];

function isPhase1Tab(value: DetailTab): value is Phase1DetailTab {
  return phase1Tabs.includes(value as Phase1DetailTab);
}

function messageFrom(reason: unknown, fallback: string) {
  return reason instanceof Error ? reason.message : fallback;
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
  initialTab?: V1DetailTab;
  onClose: () => void;
  onRetry: (job: TranscriptJob) => void | Promise<void>;
  onCancel: (job: TranscriptJob) => void | Promise<void>;
  onReveal: (artifact: TranscriptArtifact) => void | Promise<void>;
}) {
  const [tab, setTab] = useState<DetailTab>(initialTab);
  const [artifacts, setArtifacts] = useState<TranscriptArtifact[]>([]);
  const [v1Content, setV1Content] = useState<Record<string, string>>({});
  const [v1Loading, setV1Loading] = useState(true);
  const [v1Error, setV1Error] = useState("");
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
  const currentJobRef = useRef(job.id);
  const v1CacheRef = useRef(new Map<string, Promise<string> | string>());
  const phase1CacheRef = useRef(
    new Map<
      Phase1DetailTab,
      Promise<Phase1ArtifactRead> | Phase1ArtifactRead
    >(),
  );
  useModalFocus(drawerRef, onClose, { initialFocusRef: closeRef });

  useEffect(() => {
    currentJobRef.current = job.id;
    setTab(initialTab);
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
  }, [job.id]);

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
  }, [job.id, phase1ListAttempt]);

  const currentV1Artifact = useMemo(
    () =>
      isPhase1Tab(tab)
        ? undefined
        : artifacts.find(
            (artifact) =>
              artifact.kind === (tab === "practice" ? "summary" : tab),
          ),
    [artifacts, tab],
  );
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
    let active = true;
    if (!currentV1Artifact) return () => undefined;
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
        v1CacheRef.current.set(currentV1Artifact.id, content);
        if (active)
          setV1Content((current) => ({
            ...current,
            [currentV1Artifact.id]: content,
          }));
      })
      .catch((reason) => {
        v1CacheRef.current.delete(currentV1Artifact.id);
        if (active) setV1Error(messageFrom(reason, "字幕工件读取失败"));
      })
      .finally(() => {
        if (active) setV1Loading(false);
      });
    return () => {
      active = false;
    };
  }, [currentV1Artifact]);

  const loadPhase1Artifact = (
    kind: Phase1DetailTab,
    artifact: Phase1ArtifactItem & { id: string },
    force = false,
  ) => {
    const requestedJob = job.id;
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
    if (!isPhase1Tab(tab)) return;
    const artifact = phase1Artifacts.get(tab);
    if (artifact) loadPhase1Artifact(tab, artifact);
  }, [phase1Artifacts, tab]);

  const runJobAction = async (
    action: (job: TranscriptJob) => void | Promise<void>,
  ) => {
    setV1Error("");
    try {
      await action(job);
      onClose();
    } catch (reason) {
      setV1Error(messageFrom(reason, "字幕任务操作失败"));
    }
  };

  const active =
    job.progress < 100 &&
    ["queued", "fetching", "saved", "organizing", "reviewing"].includes(
      job.status,
    );
  const retryable = [
    "waiting_remote",
    "waiting_for_ai",
    "partial",
    "failed",
    "interrupted",
  ].includes(job.status);
  const phase1Warning =
    job.partial_warning === true ||
    isTranscriptQualityRisk(phase1List?.quality ?? job.quality) ||
    ["partial", "failed", "completed_with_warnings"].includes(
      job.phase1_status ?? job.pipeline_status ?? phase1List?.status ?? "",
    );
  const currentPhase1 = isPhase1Tab(tab) ? phase1Content[tab] : undefined;
  const currentPhase1Error = isPhase1Tab(tab) ? phase1Errors[tab] : "";
  const currentPhase1Loading = isPhase1Tab(tab)
    ? phase1Loading[tab] === true
    : false;
  const manifestWarnings = Array.isArray(phase1List?.warnings)
    ? phase1List.warnings.filter(
        (warning): warning is string => typeof warning === "string",
      )
    : [];
  const progress = Math.max(0, Math.min(100, job.progress));
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
  const resultLabel = active
    ? "正在处理"
    : resultTone === "partial"
      ? "已有可用结果"
      : resultTone === "failed"
        ? "暂无可用结果"
        : resultTone === "completed"
          ? "处理完成"
          : "等待处理";

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
        aria-busy={active || undefined}
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
                  深度校对部分完成
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

        <div className="transcript-progress-block" aria-live="polite">
          <div>
            <span>{job.message || "等待处理"}</span>
            <strong>{progress}%</strong>
          </div>
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

        {(active || retryable || currentV1Artifact) && (
          <details className="transcript-drawer-actions">
            <summary>更多操作</summary>
            <div>
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
              {currentV1Artifact && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => void onReveal(currentV1Artifact)}
                >
                  <ExternalLink aria-hidden="true" />在 Finder 中显示
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
          </TabsList>
          <section
            className="phase1-section"
            aria-labelledby="phase1-section-title"
          >
            <div className="phase1-section-heading">
              <div>
                <h4 id="phase1-section-title">AI 校对</h4>
                <p>课程语境驱动的深度校对结果</p>
              </div>
              {phase1ListLoading && <span role="status">正在检查…</span>}
            </div>
            <TabsList className="phase1-tabs" aria-label="AI 校对结果">
              {phase1Tabs.map((value) => (
                <TabsTrigger
                  key={value}
                  value={value}
                  disabled={!phase1Available || !phase1Artifacts.has(value)}
                >
                  {phase1TabLabels[value]}
                </TabsTrigger>
              ))}
            </TabsList>
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
              <p className="phase1-legacy-note">
                本节尚未生成深度校对，可重新执行字幕AI整理
              </p>
            )}
          </section>
        </Tabs>

        <div className="transcript-drawer-content">
          {isPhase1Tab(tab) ? (
            currentPhase1Error ? (
              <div className="phase1-inline-state" role="alert">
                <span>{currentPhase1Error}</span>
                {phase1Artifacts.get(tab) && (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() =>
                      loadPhase1Artifact(
                        tab,
                        phase1Artifacts.get(tab) as Phase1ArtifactItem & {
                          id: string;
                        },
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
                正在读取{phase1TabLabels[tab]}…
              </p>
            ) : currentPhase1 ? (
              <TranscriptPhase1Panel
                kind={tab}
                artifact={currentPhase1}
                manifestWarnings={manifestWarnings}
              />
            ) : (
              <p className="transcript-muted">该校对工件暂不可用。</p>
            )
          ) : v1Error ? (
            <p role="alert" className="transcript-inline-error">
              {v1Error}
            </p>
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
              />
            ) : tab === "practice" ? (
              <TranscriptPractice
                content={v1Content[currentV1Artifact.id] ?? ""}
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
