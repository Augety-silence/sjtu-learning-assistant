import { Paperclip, Plus, Sparkles, Trash2, X } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { toolLabel } from "@/components/AIExecutionCard";
import {
  deleteAiSkill,
  listAiAttachments,
  pickAiAttachment,
  saveAiSkill,
} from "@/lib/api";
import type {
  AIAgentPreset,
  AIAgentPresetsResult,
  AIManagedAttachment,
  AISkillDraft,
} from "@/lib/types";
import { useModalFocus } from "@/lib/useModalFocus";

/** 自定义技能可勾选的工具，按用途分组。 */
export const SKILL_TOOL_GROUPS: Array<{ label: string; tools: string[] }> = [
  {
    label: "课程与资料",
    tools: [
      "list_courses",
      "search_course_files",
      "list_course_files",
      "read_course_file_text",
      "get_material_tree",
    ],
  },
  {
    label: "截止日期与消息",
    tools: ["get_deadlines", "search_messages", "get_message_detail"],
  },
  {
    label: "AI 附件",
    tools: ["search_ai_attachments", "read_ai_attachment_text"],
  },
  {
    label: "本地工作区（修改需你确认）",
    tools: [
      "list_workspace_files",
      "read_workspace_file",
      "propose_file_change",
    ],
  },
];

const EMPTY_DRAFT: AISkillDraft = {
  name: "",
  description: "",
  instructions: "",
  allowed_tools: [
    "search_course_files",
    "read_course_file_text",
    "search_ai_attachments",
    "read_ai_attachment_text",
  ],
  reference_attachment_ids: [],
};

function draftFrom(preset: AIAgentPreset): AISkillDraft {
  return {
    id: preset.id,
    name: preset.name,
    description: preset.description === "自定义技能" ? "" : preset.description,
    instructions: preset.instructions ?? "",
    allowed_tools: [...preset.allowed_tools],
    reference_attachment_ids: [...(preset.reference_attachment_ids ?? [])],
  };
}

export function AISkillManager({
  presets,
  initialSkillId,
  onClose,
  onSaved,
}: {
  presets: AIAgentPreset[];
  initialSkillId?: string | null;
  onClose: () => void;
  onSaved: (result: AIAgentPresetsResult, selectedId: string | null) => void;
}) {
  const userSkills = presets.filter((preset) => preset.source === "user");
  const initial = userSkills.find((preset) => preset.id === initialSkillId);
  const [draft, setDraft] = useState<AISkillDraft>(
    initial ? draftFrom(initial) : EMPTY_DRAFT,
  );
  const [attachments, setAttachments] = useState<AIManagedAttachment[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const nameRef = useRef<HTMLInputElement>(null);
  const titleId = useId();

  useModalFocus(dialogRef, onClose, {
    initialFocusRef: nameRef,
    dismissible: !saving,
  });

  useEffect(() => {
    let cancelled = false;
    listAiAttachments(200)
      .then((result) => {
        if (!cancelled) setAttachments(result.items);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  const update = (change: Partial<AISkillDraft>) =>
    setDraft((current) => ({ ...current, ...change }));

  const toggle = (
    key: "allowed_tools" | "reference_attachment_ids",
    value: never,
  ) =>
    setDraft((current) => {
      const list = current[key] as unknown[];
      return {
        ...current,
        [key]: list.includes(value)
          ? list.filter((item) => item !== value)
          : [...list, value],
      };
    });

  const addAttachment = async () => {
    try {
      const picked = await pickAiAttachment();
      const attachment = picked.attachment;
      if (picked.cancelled || !attachment) return;
      setAttachments((current) =>
        current.some((item) => item.id === attachment.id)
          ? current
          : [attachment, ...current],
      );
      setDraft((current) =>
        current.reference_attachment_ids.includes(attachment.id)
          ? current
          : {
              ...current,
              reference_attachment_ids: [
                ...current.reference_attachment_ids,
                attachment.id,
              ],
            },
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "添加附件失败。");
    }
  };

  const save = async () => {
    if (saving) return;
    if (!draft.name.trim() || !draft.instructions.trim()) {
      setError("请填写技能名称和指令。");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const result = await saveAiSkill({
        ...draft,
        name: draft.name.trim(),
        description: draft.description.trim(),
      });
      onSaved(result, result.skill.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "保存技能失败。");
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    if (!draft.id || saving) return;
    setSaving(true);
    setError(null);
    try {
      onSaved(await deleteAiSkill(draft.id), null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "删除技能失败。");
    } finally {
      setSaving(false);
    }
  };

  return createPortal(
    <div className="confirm-layer ai-skill-layer" data-modal-layer>
      <button
        type="button"
        className="confirm-backdrop"
        aria-label="关闭技能管理"
        disabled={saving}
        onClick={onClose}
      />
      <section
        ref={dialogRef}
        className="confirm-dialog ai-skill-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-busy={saving}
        tabIndex={-1}
      >
        <header className="ai-skill-header">
          <h3 id={titleId}>
            <Sparkles aria-hidden="true" />
            自定义技能
          </h3>
          <button
            type="button"
            className="ai-skill-close"
            aria-label="关闭"
            onClick={onClose}
          >
            <X aria-hidden="true" />
          </button>
        </header>
        <div className="ai-skill-body">
          <nav className="ai-skill-list" aria-label="我的技能">
            <button
              type="button"
              className={draft.id ? "" : "is-active"}
              onClick={() => setDraft(EMPTY_DRAFT)}
            >
              <Plus aria-hidden="true" />
              新建技能
            </button>
            {userSkills.map((skill) => (
              <button
                type="button"
                key={skill.id}
                className={draft.id === skill.id ? "is-active" : ""}
                onClick={() => {
                  setDraft(draftFrom(skill));
                  setError(null);
                }}
              >
                {skill.name}
              </button>
            ))}
          </nav>
          <form
            className="ai-skill-form"
            onSubmit={(event) => {
              event.preventDefault();
              void save();
            }}
          >
            <label>
              <span>名称</span>
              <input
                ref={nameRef}
                value={draft.name}
                maxLength={40}
                placeholder="例如：论文精读、宏观经济复习"
                onChange={(event) => update({ name: event.target.value })}
              />
            </label>
            <label>
              <span>一句话说明</span>
              <input
                value={draft.description}
                maxLength={240}
                placeholder="在助手列表中显示"
                onChange={(event) =>
                  update({ description: event.target.value })
                }
              />
            </label>
            <label>
              <span>
                指令 <small>{draft.instructions.length}/20000</small>
              </span>
              <textarea
                value={draft.instructions}
                maxLength={20000}
                rows={8}
                placeholder={
                  "告诉助手它扮演什么角色、按什么步骤工作、输出什么格式。\n例如：先用 3 句话概括，再按章节列要点，最后给 5 道自测题。"
                }
                onChange={(event) =>
                  update({ instructions: event.target.value })
                }
              />
            </label>
            <fieldset>
              <legend>可用工具</legend>
              {SKILL_TOOL_GROUPS.map((group) => (
                <div className="ai-skill-tool-group" key={group.label}>
                  <small>{group.label}</small>
                  {group.tools.map((tool) => (
                    <label className="ai-skill-check" key={tool}>
                      <input
                        type="checkbox"
                        checked={draft.allowed_tools.includes(tool)}
                        onChange={() => toggle("allowed_tools", tool as never)}
                      />
                      {toolLabel(tool)}
                    </label>
                  ))}
                </div>
              ))}
            </fieldset>
            <fieldset>
              <legend>
                参考资料（增强上下文，最多 10 个）
                <button
                  type="button"
                  className="ai-skill-add"
                  onClick={() => void addAttachment()}
                >
                  <Paperclip aria-hidden="true" />
                  添加文件
                </button>
              </legend>
              <p className="ai-skill-hint">
                每次使用该技能时，助手都会看到这些资料的摘要，并可按需读取全文（PDF、PPTX、DOCX、文本等）。
              </p>
              <div className="ai-skill-references">
                {attachments.length === 0 && (
                  <small>还没有 AI 附件，点击“添加文件”选择本地资料。</small>
                )}
                {attachments.map((attachment) => (
                  <label
                    className="ai-skill-check"
                    key={attachment.id}
                    title={attachment.summary ?? undefined}
                  >
                    <input
                      type="checkbox"
                      checked={draft.reference_attachment_ids.includes(
                        attachment.id,
                      )}
                      disabled={
                        !draft.reference_attachment_ids.includes(
                          attachment.id,
                        ) && draft.reference_attachment_ids.length >= 10
                      }
                      onChange={() =>
                        toggle(
                          "reference_attachment_ids",
                          attachment.id as never,
                        )
                      }
                    />
                    <span>{attachment.name}</span>
                  </label>
                ))}
              </div>
            </fieldset>
            {error && (
              <p className="submission-error" role="alert">
                {error}
              </p>
            )}
            <div className="confirm-actions ai-skill-actions">
              {draft.id && (
                <button
                  type="button"
                  className="ai-skill-delete"
                  disabled={saving}
                  onClick={() => void remove()}
                >
                  <Trash2 aria-hidden="true" />
                  删除
                </button>
              )}
              <button type="button" disabled={saving} onClick={onClose}>
                取消
              </button>
              <button type="submit" className="is-primary" disabled={saving}>
                {saving ? "正在保存…" : draft.id ? "保存修改" : "创建技能"}
              </button>
            </div>
          </form>
        </div>
      </section>
    </div>,
    document.body,
  );
}
