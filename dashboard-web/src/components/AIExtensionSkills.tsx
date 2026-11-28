import { FileArchive, FileText, Folder, Sparkles, Trash2 } from "lucide-react";
import { useRef, useState } from "react";
import { ExtensionConfigDialog } from "@/components/ExtensionConfigDialog";
import { invoke } from "@/lib/api";
import type { AIAgentPresetsResult, AIExtensionSkill } from "@/lib/types";

export function ExtensionSwitch({
  checked,
  onChange,
  label,
  disabled = false,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  label: string;
  disabled?: boolean;
}) {
  return (
    <label className="extension-switch">
      <input
        type="checkbox"
        role="switch"
        checked={checked}
        disabled={disabled}
        aria-label={label}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span aria-hidden="true" />
    </label>
  );
}

export function AIExtensionSkills({
  skills,
  onChanged,
}: {
  skills: AIExtensionSkill[];
  onChanged: (result: AIAgentPresetsResult) => void;
}) {
  const [source, setSource] = useState<"builtin" | "user">("builtin");
  const [selected, setSelected] = useState<string | null>(null);
  const [content, setContent] = useState("");
  const [file, setFile] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const readRequest = useRef(0);
  const current = skills.find((s) => s.id === selected);
  const perform = async (action: string, payload: Record<string, unknown>) => {
    setBusy(true);
    setError("");
    try {
      const result = await invoke<
        AIAgentPresetsResult & { cancelled?: boolean; skill?: { id: string } }
      >(action, payload);
      if (!result.cancelled) {
        onChanged(result);
        if (result.skill) {
          setSelected(result.skill.id);
          setFile("");
          setContent("");
        }
      }
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "技能配置更新失败，请重试。",
      );
    } finally {
      setBusy(false);
    }
  };
  const read = async (skill: AIExtensionSkill, path: string) => {
    const request = ++readRequest.current;
    setSelected(skill.id);
    setFile(path);
    setError("");
    if (path === skill.entry || !skill.files.length) {
      setContent(skill.instructions);
      return;
    }
    setContent("");
    try {
      const result = await invoke<{ text: string; next_offset: number | null }>(
        "ai_skill_read",
        { skill_id: skill.id, path },
      );
      if (request !== readRequest.current) return;
      setContent(
        result.text +
          (result.next_offset ? "\n\n……文件较长，AI 可分页读取后续内容。" : ""),
      );
    } catch (reason) {
      if (request === readRequest.current)
        setError(reason instanceof Error ? reason.message : "无法读取文件。");
    }
  };
  return (
    <div className="extension-library">
      <div className="ui2-tabbar" role="tablist" aria-label="技能来源">
        {(
          [
            ["builtin", "内置技能"],
            ["user", "外部技能"],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={source === id}
            onClick={() => {
              readRequest.current += 1;
              setSource(id);
              setSelected(null);
              setContent("");
            }}
          >
            {label}
          </button>
        ))}
        {source === "user" && (
          <button
            className="ui2-tab-action"
            type="button"
            disabled={busy}
            onClick={() => void perform("ai_skill_import", {})}
          >
            <FileArchive size={15} />
            {busy ? "正在导入…" : "导入 ZIP"}
          </button>
        )}
      </div>
      {error && !current && (
        <p role="alert" className="extension-error">
          {error}
        </p>
      )}
      <div className="extension-rows">
        {skills
          .filter((s) => s.source === source)
          .map((skill) => (
            <div
              key={skill.id}
              className={`extension-row ${selected === skill.id ? "is-selected" : ""}`}
            >
              <Sparkles size={19} />
              <button
                type="button"
                className="extension-row-info"
                aria-label={skill.name}
                onClick={() => void read(skill, skill.entry || "SKILL.md")}
              >
                <strong>{skill.name}</strong>
                <span>{skill.description || "自定义技能指令"}</span>
              </button>
              <ExtensionSwitch
                checked={skill.enabled}
                disabled={busy}
                label={`启用技能 ${skill.name}`}
                onChange={(enabled) =>
                  void perform("ai_skill_update", {
                    skill_id: skill.id,
                    enabled,
                  })
                }
              />
            </div>
          ))}
        {!skills.some((s) => s.source === source) && (
          <div className="extension-empty">
            <FileArchive size={26} />
            <strong>导入你的第一个技能包</strong>
            <p>
              选择 ZIP，保留 SKILL.md、README、scripts 和资源文件的原始结构。
            </p>
          </div>
        )}
      </div>
      {current && (
        <ExtensionConfigDialog
          title={`${current.name} 配置`}
          onClose={() => {
            readRequest.current += 1;
            setSelected(null);
            setError("");
          }}
          actions={
            <>
              {current.source === "user" && (
                <button
                  type="button"
                  className="extension-delete"
                  aria-label={`移除技能 ${current.name}`}
                  disabled={busy}
                  onClick={() => {
                    void perform("ai_skill_delete", { skill_id: current.id });
                    setSelected(null);
                  }}
                >
                  <Trash2 size={15} />
                </button>
              )}
            </>
          }
        >
          {error && (
            <p role="alert" className="extension-error">
              {error}
            </p>
          )}
          <section className="extension-detail">
            {current.source === "user" &&
              current.files.some((f) => f.split("/").includes("scripts")) && (
                <div className="extension-script-permission">
                  <div>
                    <strong>允许运行脚本</strong>
                    <p>由综合助手运行已选技能的脚本，按本机进程权限执行。</p>
                  </div>
                  <ExtensionSwitch
                    checked={current.allow_scripts}
                    disabled={busy}
                    label={`允许 ${current.name} 运行脚本`}
                    onChange={(allow_scripts) =>
                      void perform("ai_skill_update", {
                        skill_id: current.id,
                        allow_scripts,
                      })
                    }
                  />
                </div>
              )}
            <div className="extension-file-layout">
              <nav aria-label="技能包文件">
                {(current.files.length ? current.files : ["SKILL.md"]).map(
                  (path) => (
                    <button
                      type="button"
                      key={path}
                      aria-current={file === path ? "true" : undefined}
                      onClick={() => void read(current, path)}
                    >
                      {path.includes("/") ? (
                        <Folder size={14} />
                      ) : (
                        <FileText size={14} />
                      )}
                      <span>{path}</span>
                    </button>
                  ),
                )}
              </nav>
              <pre aria-label="技能文件内容">{content}</pre>
            </div>
          </section>
        </ExtensionConfigDialog>
      )}
    </div>
  );
}
