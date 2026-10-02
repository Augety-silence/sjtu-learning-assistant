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
import { ErrorState, LoadingState } from "@/components/States";
import { Button } from "@/components/ui/Button";
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
        <strong>{source ? "课程 Markdown 素材" : "Obsidian Vault 输出"}</strong>
        <span title={value || undefined}>
          {value || (source ? "选择需要递归扫描的目录" : "请选择独立的空目录")}
        </span>
      </div>
      <Button
        type="button"
        variant="outline"
        disabled={disabled}
        onClick={onPick}
      >
        选择目录
      </Button>
    </div>
  );
}

export function KnowledgeCompilerView() {
  const [sourceRoot, setSourceRoot] = useState(
    () => window.localStorage.getItem(SOURCE_KEY) ?? "",
  );
  const [targetRoot, setTargetRoot] = useState(
    () => window.localStorage.getItem(TARGET_KEY) ?? "",
  );
  const [mode, setMode] = useState<KnowledgeCompilerMode>("full");
  const [inspection, setInspection] =
    useState<KnowledgeCompilerInspection | null>(null);
  const [task, setTask] = useState<KnowledgeCompilerTask | null>(null);
  const [aiReady, setAiReady] = useState<boolean | null>(null);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");

  const running = task?.status === "running";
  const terminal = task && !["idle", "running"].includes(task.status);

  const loadStatus = useCallback(async () => {
    try {
      const next = await getKnowledgeCompilerStatus(targetRoot || undefined);
      setTask(next);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "编译状态读取失败");
    }
  }, [targetRoot]);

  useEffect(() => {
    let active = true;
    void Promise.all([
      getSettings(),
      getKnowledgeCompilerStatus(targetRoot || undefined),
    ])
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
  if (!task && error) return <ErrorState message={error} retry={loadStatus} />;

  return (
    <div className="section-stack knowledge-page">
      <section className="knowledge-hero">
        <div className="knowledge-hero-mark" aria-hidden="true">
          <BrainCircuit />
        </div>
        <div>
          <span className="knowledge-eyebrow">OBSIDIAN COURSE COMPILER</span>
          <h2>把课程文件编译成可生长的知识网络</h2>
          <p>
            原文只读归档，DeepSeek Chat 负责批量重构，Reasoner
            负责跨文件推理；每轮结果都写入处理日志。
          </p>
        </div>
      </section>

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

      <section
        className="knowledge-config"
        aria-labelledby="knowledge-config-title"
      >
        <div className="section-header">
          <div>
            <h2 id="knowledge-config-title">1. 选择输入与输出</h2>
            <p>输出目录必须为空或由本功能创建，且不能位于素材目录内部。</p>
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
            <dl className="knowledge-scan-summary" aria-label="素材扫描结果">
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
          <div>
            <h2 id="knowledge-mode-title">2. 选择编译深度</h2>
            <p>
              完整编译会多轮回读全库，适合长期运行；基础建库可先验证目录与内容质量。
            </p>
          </div>
        </div>
        <div className="knowledge-mode-grid">
          <label
            className={mode === "foundation" ? "knowledge-mode-selected" : ""}
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
              <small>原文归档、Lecture 重构、Concept 提取</small>
            </span>
          </label>
          <label className={mode === "full" ? "knowledge-mode-selected" : ""}>
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
              <small>双链、图谱、查缺、反向验证、题库与复习系统</small>
            </span>
          </label>
        </div>
        <ol className="knowledge-phase-strip" aria-label="完整编译轮次">
          {phaseLabels.map((label, index) => (
            <li
              key={label}
              className={
                (task?.phase_index ?? 0) > index
                  ? "knowledge-phase-done"
                  : (task?.phase_index ?? 0) === index + 1
                    ? "knowledge-phase-active"
                    : ""
              }
            >
              <span>{index + 1}</span>
              {label}
            </li>
          ))}
        </ol>
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
              <p>
                {task?.current_phase
                  ? `${task.current_phase.label} · ${task.current_phase.model}`
                  : "选择目录后即可启动。"}
              </p>
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
              <span>{progress?.current_file || "正在切换到下一轮…"}</span>
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
            <div className="knowledge-events">
              <h3>运行记录</h3>
              <ol>
                {task.events
                  .slice(-8)
                  .reverse()
                  .map((event, index) => (
                    <li key={`${event.time}-${index}`} data-level={event.level}>
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
            </div>
          </>
        )}
      </section>
    </div>
  );
}
