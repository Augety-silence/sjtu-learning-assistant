import {
  ArrowRight,
  Files,
  FolderInput,
  FolderKanban,
  FolderOutput,
  Image,
  Plus,
  RefreshCw,
  Trash2,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ErrorState, LoadingState } from "@/components/States";
import { Button } from "@/components/ui/Button";
import {
  addLocalProject,
  listLocalProjects,
  pickKnowledgeFolder,
  refreshLocalProject,
  removeLocalProject,
} from "@/lib/api";
import type { LocalProject } from "@/lib/types";

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function statusLabel(status: LocalProject["compile_status"]) {
  if (status === "running") return "编译运行中";
  if (status === "completed") return "编译完成";
  if (status === "completed_with_warnings") return "完成，待核验";
  if (status === "cancelled") return "已安全停止";
  if (status === "interrupted") return "上次中断";
  if (status === "failed") return "编译失败";
  return "尚未编译";
}

function ProjectPath({
  label,
  value,
  kind,
  disabled,
  onPick,
}: {
  label: string;
  value: string;
  kind: "source" | "target";
  disabled: boolean;
  onPick: () => void;
}) {
  const Icon = kind === "source" ? FolderInput : FolderOutput;
  return (
    <div className="local-project-path">
      <Icon aria-hidden="true" />
      <div>
        <strong>{label}</strong>
        <span title={value || undefined}>
          {value ||
            (kind === "source"
              ? "选择包含 Markdown 的素材目录"
              : "选择独立的空目录或已有编译 Vault")}
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

function ProjectCard({
  project,
  busy,
  onOpen,
  onRefresh,
  onRemove,
}: {
  project: LocalProject;
  busy: boolean;
  onOpen: () => void;
  onRefresh: () => void;
  onRemove: () => void;
}) {
  const courseCopy = project.courses.length
    ? project.courses.slice(0, 3).join("、")
    : "未识别课程";
  return (
    <article className="local-project-card">
      <div className="local-project-card-heading">
        <div className="local-project-icon" aria-hidden="true">
          <FolderKanban />
        </div>
        <div>
          <h3>{project.name}</h3>
          <span
            className="local-project-status"
            data-status={project.available ? project.compile_status : "missing"}
          >
            {project.issue || statusLabel(project.compile_status)}
          </span>
        </div>
      </div>

      <dl className="local-project-stats">
        <div>
          <dt>Markdown</dt>
          <dd>{project.markdown_files}</dd>
        </div>
        <div>
          <dt>文本体积</dt>
          <dd>{formatBytes(project.total_bytes)}</dd>
        </div>
        <div>
          <dt>图片引用</dt>
          <dd>{project.image_references}</dd>
        </div>
      </dl>

      <div className="local-project-course">
        <span>课程范围</span>
        <p title={project.courses.join("、")}>{courseCopy}</p>
      </div>
      <div className="local-project-paths">
        <p title={project.source_root}>
          <FolderInput aria-hidden="true" />
          <span>{project.source_root}</span>
        </p>
        <p title={project.target_root}>
          <FolderOutput aria-hidden="true" />
          <span>{project.target_root}</span>
        </p>
      </div>

      {project.compile_status === "running" && project.total_phases > 0 && (
        <div className="local-project-progress">
          <span>
            当前进度 {project.phase_index} / {project.total_phases}
          </span>
          <div
            role="progressbar"
            aria-label={`${project.name} 编译进度`}
            aria-valuemin={0}
            aria-valuemax={project.total_phases}
            aria-valuenow={project.phase_index}
          >
            <i
              style={{
                width: `${Math.round(
                  (project.phase_index / project.total_phases) * 100,
                )}%`,
              }}
            />
          </div>
        </div>
      )}

      <div className="local-project-actions">
        <Button
          type="button"
          disabled={!project.available || busy}
          onClick={onOpen}
        >
          进入编译
          <ArrowRight aria-hidden="true" />
        </Button>
        <Button
          type="button"
          variant="outline"
          size="icon"
          aria-label={`刷新 ${project.name}`}
          title="重新扫描项目"
          disabled={busy}
          onClick={onRefresh}
        >
          <RefreshCw aria-hidden="true" />
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          aria-label={`移除 ${project.name}`}
          title="仅移除索引，不删除本地文件"
          disabled={busy || project.compile_status === "running"}
          onClick={onRemove}
        >
          <Trash2 aria-hidden="true" />
        </Button>
      </div>
    </article>
  );
}

export function LocalProjectsView({
  onCompile,
}: {
  onCompile: (project: LocalProject) => void;
}) {
  const [projects, setProjects] = useState<LocalProject[]>([]);
  const [sourceRoot, setSourceRoot] = useState("");
  const [targetRoot, setTargetRoot] = useState("");
  const [showForm, setShowForm] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const result = await listLocalProjects();
      setProjects(result.items);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "本地项目读取失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const pick = async (kind: "source" | "target") => {
    setBusyId("form");
    setError("");
    try {
      const result = await pickKnowledgeFolder();
      if (result.cancelled || !result.path) return;
      if (kind === "source") setSourceRoot(result.path);
      else setTargetRoot(result.path);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "文件夹选择失败");
    } finally {
      setBusyId("");
    }
  };

  const add = async () => {
    if (!sourceRoot || !targetRoot) return;
    setBusyId("form");
    setError("");
    try {
      const project = await addLocalProject(sourceRoot, targetRoot);
      setProjects((current) => [
        project,
        ...current.filter((item) => item.id !== project.id),
      ]);
      setSourceRoot("");
      setTargetRoot("");
      setShowForm(false);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "本地项目登记失败");
    } finally {
      setBusyId("");
    }
  };

  const refresh = async (project: LocalProject) => {
    setBusyId(project.id);
    setError("");
    try {
      const next = await refreshLocalProject(project.id);
      setProjects((current) =>
        current.map((item) => (item.id === next.id ? next : item)),
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "项目刷新失败");
    } finally {
      setBusyId("");
    }
  };

  const remove = async (project: LocalProject) => {
    if (
      !window.confirm(
        `只从 App 中移除“${project.name}”的登记记录？素材和 Vault 文件不会被删除。`,
      )
    )
      return;
    setBusyId(project.id);
    setError("");
    try {
      await removeLocalProject(project.id);
      setProjects((current) =>
        current.filter((item) => item.id !== project.id),
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "项目移除失败");
    } finally {
      setBusyId("");
    }
  };

  const totals = useMemo(
    () => ({
      files: projects.reduce((sum, project) => sum + project.markdown_files, 0),
      images: projects.reduce(
        (sum, project) => sum + project.image_references,
        0,
      ),
      running: projects.filter(
        (project) => project.compile_status === "running",
      ).length,
    }),
    [projects],
  );

  if (loading) return <LoadingState label="正在读取本地项目…" />;
  if (error && !projects.length && !showForm)
    return <ErrorState message={error} retry={load} />;

  return (
    <div className="section-stack local-projects-page">
      <section className="local-projects-hero">
        <div>
          <span className="knowledge-eyebrow">LOCAL PROJECT WORKSPACE</span>
          <h2>集中管理全部本地知识项目</h2>
          <p>
            每个项目绑定只读素材目录与独立 Vault；从这里统一查看状态，再进入现有
            3 轮或 12 轮编译流程。
          </p>
        </div>
        <Button type="button" onClick={() => setShowForm((value) => !value)}>
          <Plus aria-hidden="true" />
          {showForm ? "收起登记" : "登记项目"}
        </Button>
      </section>

      <dl className="local-projects-summary" aria-label="本地项目汇总">
        <div>
          <FolderKanban aria-hidden="true" />
          <span>
            <dt>项目</dt>
            <dd>{projects.length}</dd>
          </span>
        </div>
        <div>
          <Files aria-hidden="true" />
          <span>
            <dt>Markdown</dt>
            <dd>{totals.files}</dd>
          </span>
        </div>
        <div>
          <Image aria-hidden="true" />
          <span>
            <dt>待核验图片</dt>
            <dd>{totals.images}</dd>
          </span>
        </div>
        <div>
          <RefreshCw aria-hidden="true" />
          <span>
            <dt>运行中</dt>
            <dd>{totals.running}</dd>
          </span>
        </div>
      </dl>

      {showForm && (
        <section
          className="local-project-form"
          aria-labelledby="local-project-form-title"
        >
          <div className="section-header">
            <div>
              <h2 id="local-project-form-title">登记本地项目</h2>
              <p>App 只保存目录索引；不会搬移、覆盖或删除任一目录中的文件。</p>
            </div>
          </div>
          <div className="local-project-form-grid">
            <ProjectPath
              label="课程 Markdown 素材"
              value={sourceRoot}
              kind="source"
              disabled={busyId === "form"}
              onPick={() => void pick("source")}
            />
            <ProjectPath
              label="Obsidian Vault 输出"
              value={targetRoot}
              kind="target"
              disabled={busyId === "form"}
              onPick={() => void pick("target")}
            />
          </div>
          <div className="local-project-form-actions">
            <Button
              type="button"
              variant="ghost"
              disabled={busyId === "form"}
              onClick={() => setShowForm(false)}
            >
              取消
            </Button>
            <Button
              type="button"
              disabled={!sourceRoot || !targetRoot}
              loading={busyId === "form"}
              loadingLabel="正在校验…"
              onClick={() => void add()}
            >
              保存并扫描
            </Button>
          </div>
        </section>
      )}

      {error && (
        <p className="knowledge-error" role="alert">
          {error}
        </p>
      )}

      {projects.length ? (
        <section className="local-project-grid" aria-label="已登记本地项目">
          {projects.map((project) => (
            <ProjectCard
              key={project.id}
              project={project}
              busy={Boolean(busyId)}
              onOpen={() => onCompile(project)}
              onRefresh={() => void refresh(project)}
              onRemove={() => void remove(project)}
            />
          ))}
        </section>
      ) : (
        <section className="local-project-empty">
          <FolderKanban aria-hidden="true" />
          <h2>还没有登记本地项目</h2>
          <p>
            先选择一个课程素材目录和一个独立 Vault，之后即可统一查看与处理。
          </p>
          <Button type="button" onClick={() => setShowForm(true)}>
            <Plus aria-hidden="true" />
            登记第一个项目
          </Button>
        </section>
      )}
    </div>
  );
}
