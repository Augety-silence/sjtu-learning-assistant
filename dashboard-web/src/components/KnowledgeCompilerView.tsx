import {
  BookOpenCheck,
  BrainCircuit,
  FolderInput,
  FolderOutput,
  OctagonX,
  RefreshCw,
  ScanSearch,
  Sparkles,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { CompiledKnowledgeBrowser } from "@/components/CompiledKnowledgeBrowser";
import { ContentReveal } from "@/components/ContentReveal";
import { CourseMarkdownSyncPanel } from "@/components/CourseMarkdownSyncPanel";
import { ErrorState, LoadingState } from "@/components/States";
import { Button } from "@/components/ui/Button";
import { InfoTooltip } from "@/components/ui/InfoTooltip";
import {
  SelectionIndicator,
  useSelectionIndicatorId,
} from "@/components/ui/SelectionIndicator";
import {
  cancelKnowledgeCompiler,
  getKnowledgeCompilerStatus,
  getSettings,
  inspectKnowledgeSource,
  pickKnowledgeFolder,
  startKnowledgeCompiler,
} from "@/lib/api";
import type {
  KnowledgeCompilerInspection,
  KnowledgeCompilerMode,
  KnowledgeCompilerTask,
} from "@/lib/types";
import { useUIRelease } from "@/lib/uiRelease";

const POLL_INTERVAL_MS = 1200;
const SOURCE_KEY = "knowledge-compiler-source";
const TARGET_KEY = "knowledge-compiler-target";

const phaseLabels = [
  "扫描归档",
  "Lecture 重构",
  "Concept 提取",
  "语义双链",
  "前置图谱",
  "跨课连接",
  "缺口分析",
  "反向校验",
  "MOC 重建",
  "题库提取",
  "分层复习",
  "图谱精炼",
];

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function statusLabel(status: KnowledgeCompilerTask["status"]) {
  if (status === "running") return "编译运行中";
  if (status === "completed") return "编译已完成";
  if (status === "completed_with_warnings") return "编译完成，存在待确认项";
  if (status === "cancelled") return "编译已停止";
  if (status === "interrupted") return "上次任务已中断";
  if (status === "failed") return "编译失败";
  return "等待开始";
}

function loadInitialStatus(targetRoot: string) {
  return getKnowledgeCompilerStatus(targetRoot || undefined).catch((reason) => {
    if (!targetRoot) throw reason;
    // 已保存的输出目录可能已被删除或位于已拔出的磁盘；回退到全局状态，避免整页卡在错误页。
    return getKnowledgeCompilerStatus();
  });
}

function PathPicker({
  kind,
  value,
  disabled,
  onPick,
}: {
  kind: "source" | "target";
  value: string;
  disabled: boolean;
  onPick: () => void;
}) {
  const source = kind === "source";
  const Icon = source ? FolderInput : FolderOutput;
  return (
    <div className="knowledge-path-card">
      <div className="knowledge-path-icon" aria-hidden="true">
        <Icon />
      </div>
      <div>
        <strong>{source ? "素材目录" : "输出目录"}</strong>
        <span title={value || undefined}>{value || "未选择"}</span>
      </div>
      <Button
        type="button"
        variant="outline"
        disabled={disabled}
        onClick={onPick}
      >
        {source ? "选择素材目录" : "选择输出目录"}
      </Button>
    </div>
  );
}

export function KnowledgeCompilerView() {
  const release = useUIRelease();
  const [sourceRoot, setSourceRoot] = useState(
    () => window.localStorage.getItem(SOURCE_KEY) ?? "",
  );
  const [targetRoot, setTargetRoot] = useState(
    () => window.localStorage.getItem(TARGET_KEY) ?? "",
  );
  const [mode, setMode] = useState<KnowledgeCompilerMode>("documents");
  const phaseIndicatorId = useSelectionIndicatorId("knowledge-phase");
  const [inspection, setInspection] =
    useState<KnowledgeCompilerInspection | null>(null);
  const [task, setTask] = useState<KnowledgeCompilerTask | null>(null);
  const [aiReady, setAiReady] = useState<boolean | null>(null);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [toolsOpen, setToolsOpen] = useState(false);
  const [error, setError] = useState("");

  const running = task?.status === "running";
  const terminal = task && !["idle", "running"].includes(task.status);

  useEffect(() => {
    if (running) setToolsOpen(true);
  }, [running]);

  const loadStatus = useCallback(async () => {
    try {
      const next = await getKnowledgeCompilerStatus(targetRoot || undefined);
      setTask(next);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "编译状态读取失败");
    }
  }, [targetRoot]);

  // 初始化失败后的重试必须同时重读设置：只刷新状态会让 aiReady 停留在 null，
  // 页面误报“需要先配置 AI 连接”且开始按钮永久禁用。
  const retryInitialLoad = useCallback(async () => {
    try {
      const [settings, next] = await Promise.all([
        getSettings(),
        loadInitialStatus(targetRoot),
      ]);
      setAiReady(settings.ai_enabled && settings.ai_key_saved);
      setTask(next);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "初始化失败");
    }
  }, [targetRoot]);

  useEffect(() => {
    let active = true;
    void Promise.all([getSettings(), loadInitialStatus(targetRoot)])
      .then(([settings, next]) => {
        if (!active) return;
        setAiReady(settings.ai_enabled && settings.ai_key_saved);
        setTask(next);
      })
      .catch((reason) => {
        if (active)
          setError(reason instanceof Error ? reason.message : "初始化失败");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [targetRoot]);

  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => void loadStatus(), POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [loadStatus, running]);

  const pick = async (kind: "source" | "target") => {
    setWorking(true);
    setError("");
    try {
      const result = await pickKnowledgeFolder();
      if (result.cancelled || !result.path) return;
      if (kind === "source") {
        setSourceRoot(result.path);
        setInspection(null);
        window.localStorage.setItem(SOURCE_KEY, result.path);
      } else {
        setTargetRoot(result.path);
        window.localStorage.setItem(TARGET_KEY, result.path);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "文件夹选择失败");
    } finally {
      setWorking(false);
    }
  };

  const inspect = async () => {
    if (!sourceRoot) return;
    setWorking(true);
    setError("");
    try {
      setInspection(await inspectKnowledgeSource(sourceRoot));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "素材扫描失败");
    } finally {
      setWorking(false);
    }
  };

  const start = async () => {
    if (!sourceRoot || !targetRoot || !aiReady) return;
    setWorking(true);
    setError("");
    try {
      // 已有任务记录时重新开始会从第 1 轮重写输出目录，用户在 Vault 中的手动修改可能被覆盖。
      // 按发起时的输出目录重读状态：刚切换目录时 task 仍是旧目录的，历史状态损坏时
      // 初始化会回退到全局空闲状态，仅凭 terminal 会漏掉覆盖确认。读取失败按"已有记录"处理。
      let existing = true;
      try {
        const latest = await getKnowledgeCompilerStatus(targetRoot);
        existing = !["idle", "running"].includes(latest.status);
      } catch {
        // 保持 existing = true：宁可多问一次，也不静默覆盖。
      }
      if (
        existing &&
        mode !== "documents" &&
        !window.confirm(
          "重新开始会从第 1 轮重新生成，输出目录中由本功能生成的笔记将被覆盖更新。确定继续？",
        )
      )
        return;
      const result = await startKnowledgeCompiler(sourceRoot, targetRoot, mode);
      setTask(result.task);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "知识库编译启动失败");
    } finally {
      setWorking(false);
    }
  };

  const stop = async () => {
    setWorking(true);
    setError("");
    try {
      setTask(await cancelKnowledgeCompiler());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "停止请求失败");
    } finally {
      setWorking(false);
    }
  };

  const progress = task?.progress;
  const progressTotal = Math.max(progress?.total ?? 0, 1);
  const progressDone = Math.min(progress?.done ?? 0, progressTotal);
  const percentage = useMemo(
    () => Math.round((progressDone / progressTotal) * 100),
    [progressDone, progressTotal],
  );

  if (loading) return <LoadingState label="正在读取知识库编译状态…" />;
  if (!task && error)
    return <ErrorState message={error} retry={retryInitialLoad} />;

  return (
    <ContentReveal
      className={
        release === "2" ? "v2-knowledge-page" : "section-stack knowledge-page"
      }
      afterLoad
    >
      {release === "2" ? (
        <div className="v2-knowledge-workspace">
          <CompiledKnowledgeBrowser />
          <aside className="v2-compile-rail">
            <h2>编译与同步队列</h2>
            <div className="v2-compile-task">
              <strong>{statusLabel(task?.status ?? "idle")}</strong>
              <small>
                {task?.current_phase?.label || "选择素材和输出目录后开始编译"}
              </small>
              {(running || terminal) && task && (
                <>
                  <progress
                    value={percentage}
                    max={100}
                    aria-label="知识库编译进度"
                  />
                  <span>
                    {percentage}% · 已生成 {task.counts.generated_notes} 份笔记
                  </span>
                </>
              )}
              <div>
                <Button variant="outline" onClick={() => setToolsOpen(true)}>
                  配置与任务详情
                </Button>
                {running && (
                  <Button
                    variant="outline"
                    disabled={working}
                    onClick={() => void stop()}
                  >
                    安全停止
                  </Button>
                )}
              </div>
            </div>
            <CourseMarkdownSyncPanel />
          </aside>
        </div>
      ) : (
        <CompiledKnowledgeBrowser />
      )}
      <details
        className="knowledge-tools"
        open={toolsOpen}
        onToggle={(event) => setToolsOpen(event.currentTarget.open)}
      >
        <summary>编译设置与任务</summary>
        <div className="section-stack">
          {!aiReady && (
            <div className="knowledge-notice" role="alert">
              <Sparkles aria-hidden="true" />
              <div>
                <strong>需要先配置 AI 连接</strong>
                <span>
                  请到“设置 → AI 模型”保存 API Key 并启用 AI，再返回开始编译。
                </span>
              </div>
            </div>
          )}

          {release !== "2" && <CourseMarkdownSyncPanel />}

          <section
            className="knowledge-config"
            aria-labelledby="knowledge-config-title"
          >
            <div className="section-header">
              <div className="heading-with-info">
                <h2 id="knowledge-config-title">输入与输出</h2>
                <InfoTooltip label="目录要求">
                  输出目录必须为空或由本功能创建，且不能位于素材目录内部。
                </InfoTooltip>
              </div>
            </div>
            <div className="knowledge-paths">
              <PathPicker
                kind="source"
                value={sourceRoot}
                disabled={Boolean(running) || working}
                onPick={() => void pick("source")}
              />
              <PathPicker
                kind="target"
                value={targetRoot}
                disabled={Boolean(running) || working}
                onPick={() => void pick("target")}
              />
            </div>
            <div className="knowledge-scan-row">
              <Button
                type="button"
                variant="outline"
                disabled={!sourceRoot || Boolean(running) || working}
                onClick={() => void inspect()}
              >
                <ScanSearch aria-hidden="true" />
                扫描素材
              </Button>
              {inspection && (
                <dl
                  className="knowledge-scan-summary"
                  aria-label="素材扫描结果"
                >
                  <div>
                    <dt>PPT / PDF</dt>
                    <dd>{inspection.document_files ?? 0}</dd>
                  </div>
                  <div>
                    <dt>Markdown</dt>
                    <dd>{inspection.markdown_files}</dd>
                  </div>
                  <div>
                    <dt>文本体积</dt>
                    <dd>{formatBytes(inspection.total_bytes)}</dd>
                  </div>
                  <div>
                    <dt>图片引用</dt>
                    <dd>{inspection.image_references}</dd>
                  </div>
                  <div>
                    <dt>课程范围</dt>
                    <dd>{inspection.courses.length}</dd>
                  </div>
                </dl>
              )}
            </div>
          </section>

          <section
            className="knowledge-config"
            aria-labelledby="knowledge-mode-title"
          >
            <div className="section-header">
              <div className="heading-with-info">
                <h2 id="knowledge-mode-title">编译方式</h2>
                <InfoTooltip label="编译方式说明">
                  逐文件生成课程
                  Markdown；基础建库整理讲义与概念；完整编译进一步建立双链、图谱和复习资料。不设每日或每次总调用额度，仍遵守接口限速并断点续编。PPTX
                  需 LibreOffice，旧 PPT 请先导出 PDF。
                </InfoTooltip>
              </div>
            </div>
            <div className="knowledge-mode-grid">
              <label
                className={
                  mode === "documents" ? "knowledge-mode-selected" : ""
                }
              >
                <input
                  type="radio"
                  name="compiler-mode"
                  value="documents"
                  checked={mode === "documents"}
                  disabled={Boolean(running)}
                  onChange={() => setMode("documents")}
                />
                <ScanSearch aria-hidden="true" />
                <span>
                  <strong>逐页多模态转 Markdown</strong>
                </span>
              </label>
              <label
                className={
                  mode === "foundation" ? "knowledge-mode-selected" : ""
                }
              >
                <input
                  type="radio"
                  name="compiler-mode"
                  value="foundation"
                  checked={mode === "foundation"}
                  disabled={Boolean(running)}
                  onChange={() => setMode("foundation")}
                />
                <BookOpenCheck aria-hidden="true" />
                <span>
                  <strong>基础建库 · 3 轮</strong>
                </span>
              </label>
              <label
                className={mode === "full" ? "knowledge-mode-selected" : ""}
              >
                <input
                  type="radio"
                  name="compiler-mode"
                  value="full"
                  checked={mode === "full"}
                  disabled={Boolean(running)}
                  onChange={() => setMode("full")}
                />
                <BrainCircuit aria-hidden="true" />
                <span>
                  <strong>完整编译 · 12 轮</strong>
                </span>
              </label>
            </div>
            {mode !== "documents" && (running || terminal) && (
              <ol className="knowledge-phase-strip" aria-label="编译轮次">
                {phaseLabels
                  .slice(0, mode === "foundation" ? 3 : 12)
                  .map((label, index) => (
                    <li
                      key={label}
                      data-selection-layout-id={phaseIndicatorId}
                      className={
                        (task?.phase_index ?? 0) > index + 1
                          ? "selection-host knowledge-phase-done"
                          : (task?.phase_index ?? 0) === index + 1
                            ? "selection-host selection-active knowledge-phase-active"
                            : "selection-host"
                      }
                    >
                      {(task?.phase_index ?? 0) === index + 1 && (
                        <SelectionIndicator
                          layoutId={phaseIndicatorId}
                          radius={8}
                          className="knowledge-phase-indicator"
                        />
                      )}
                      <span>{index + 1}</span>
                      <span className="selection-label">{label}</span>
                    </li>
                  ))}
              </ol>
            )}
          </section>

          <section
            className="knowledge-run-card"
            aria-labelledby="knowledge-run-title"
          >
            <div className="knowledge-run-heading">
              <div>
                <span
                  className={`status-dot ${running ? "status-running" : ""}`}
                  aria-hidden="true"
                />
                <div>
                  <h2 id="knowledge-run-title">
                    {statusLabel(task?.status ?? "idle")}
                  </h2>
                  {task?.current_phase && (
                    <InfoTooltip label="当前编译阶段">
                      {task.current_phase.label} · {task.current_phase.model}
                    </InfoTooltip>
                  )}
                </div>
              </div>
              <div className="knowledge-run-actions">
                <Button
                  type="button"
                  variant="outline"
                  disabled={working}
                  onClick={() => void loadStatus()}
                >
                  <RefreshCw aria-hidden="true" />
                  刷新
                </Button>
                {running ? (
                  <Button
                    type="button"
                    variant="outline"
                    disabled={working}
                    onClick={() => void stop()}
                  >
                    <OctagonX aria-hidden="true" />
                    安全停止
                  </Button>
                ) : (
                  <Button
                    type="button"
                    disabled={!sourceRoot || !targetRoot || !aiReady || working}
                    loading={working}
                    loadingLabel="正在启动…"
                    onClick={() => void start()}
                  >
                    <Sparkles aria-hidden="true" />
                    开始编译
                  </Button>
                )}
              </div>
            </div>

            {error && (
              <p className="knowledge-error" role="alert">
                {error}
              </p>
            )}
            {!error &&
              task?.error &&
              (task.status === "failed" || task.status === "interrupted") && (
                <p className="knowledge-error" role="alert">
                  {task.error}
                </p>
              )}
            {(running || terminal) && task && (
              <>
                <dl className="knowledge-metrics">
                  <div>
                    <dt>当前轮次</dt>
                    <dd>
                      {task.phase_index} / {task.total_phases}
                    </dd>
                  </div>
                  <div>
                    <dt>原始文件</dt>
                    <dd>{task.counts.markdown_files}</dd>
                  </div>
                  <div>
                    <dt>生成笔记</dt>
                    <dd>{task.counts.generated_notes}</dd>
                  </div>
                  <div>
                    <dt>待确认</dt>
                    <dd>{task.counts.warnings}</dd>
                  </div>
                </dl>
                <div className="knowledge-progress-copy">
                  <strong>{percentage}%</strong>
                  {/* 后端在完成/停止时会清空 current_file；终态不能再显示"正在切换"。 */}
                  <span>
                    {progress?.current_file ||
                      (running ? "正在切换到下一轮…" : "本次任务已结束")}
                  </span>
                </div>
                <div
                  className="knowledge-progress"
                  role="progressbar"
                  aria-label="知识库编译进度"
                  aria-valuemin={0}
                  aria-valuemax={progressTotal}
                  aria-valuenow={progressDone}
                >
                  <span style={{ width: `${percentage}%` }} />
                </div>
                <details className="knowledge-events">
                  <summary>运行记录</summary>
                  <ol>
                    {task.events
                      .slice(-8)
                      .reverse()
                      .map((event, index) => (
                        <li
                          key={`${event.time}-${index}`}
                          data-level={event.level}
                        >
                          <span>
                            {new Date(event.time).toLocaleTimeString("zh-CN", {
                              hour: "2-digit",
                              minute: "2-digit",
                            })}
                          </span>
                          <p>{event.message}</p>
                        </li>
                      ))}
                  </ol>
                </details>
              </>
            )}
          </section>
        </div>
      </details>
    </ContentReveal>
  );
}
