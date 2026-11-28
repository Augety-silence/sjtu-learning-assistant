import { Check, Sparkles, X } from "lucide-react";
import { useRef, useState } from "react";
import { AIFloatingMenu } from "@/components/AIFloatingMenu";
import type { AIExtensionSkill } from "@/lib/types";

export function AISelectedSkillAttachments({
  skills,
  selected,
  onChange,
  busy = false,
}: {
  skills: AIExtensionSkill[];
  selected: string[];
  onChange: (ids: string[]) => void;
  busy?: boolean;
}) {
  const available = skills.filter((s) => s.enabled);
  return (
    <>
      {selected.length > 0 && (
        <div className="ai-attachment-chips" aria-label="已附加技能">
          {available
            .filter((s) => selected.includes(s.id))
            .map((s) => (
              <span key={s.id} className="ai-attachment-chip">
                <Sparkles size={13} />
                <span>{s.name}</span>
                <button
                  type="button"
                  disabled={busy}
                  aria-label={`移除技能 ${s.name}`}
                  onClick={() => onChange(selected.filter((id) => id !== s.id))}
                >
                  <X size={12} />
                </button>
              </span>
            ))}
        </div>
      )}
    </>
  );
}

export function AISelectedSkills({
  skills,
  selected,
  onChange,
  busy = false,
}: {
  skills: AIExtensionSkill[];
  selected: string[];
  onChange: (ids: string[]) => void;
  busy?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [source, setSource] = useState<"builtin" | "user">("builtin");
  const anchor = useRef<HTMLButtonElement>(null);
  const available = skills.filter((s) => s.enabled);
  return (
    <div className="ai-skill-selection">
      <button
        type="button"
        ref={anchor}
        className="ai-skill-select-trigger"
        aria-label="选择技能"
        aria-haspopup="dialog"
        aria-expanded={open}
        disabled={busy}
        onClick={() => setOpen((v) => !v)}
      >
        <Sparkles size={14} />
        <span>技能{selected.length ? ` · ${selected.length}` : ""}</span>
      </button>
      {open && (
        <AIFloatingMenu
          anchorRef={anchor}
          onClose={() => setOpen(false)}
          placement="above"
          width={300}
        >
          <section aria-label="附加技能" role="dialog">
            <div
              className="ui2-tabbar"
              role="tablist"
              aria-label="附加技能来源"
            >
              {(
                [
                  ["builtin", "内置"],
                  ["user", "外部"],
                ] as const
              ).map(([id, label]) => (
                <button
                  type="button"
                  key={id}
                  role="tab"
                  aria-selected={source === id}
                  onClick={() => setSource(id)}
                >
                  {label}
                </button>
              ))}
            </div>
            <div className="ai-skill-select-options">
              {available
                .filter((s) => s.source === source)
                .map((s) => (
                  <label key={s.id}>
                    <input
                      type="checkbox"
                      aria-label={s.name}
                      checked={selected.includes(s.id)}
                      disabled={
                        !selected.includes(s.id) && selected.length >= 8
                      }
                      onChange={(e) =>
                        onChange(
                          e.target.checked
                            ? [...selected, s.id]
                            : selected.filter((id) => id !== s.id),
                        )
                      }
                    />
                    <span>
                      <strong>{s.name}</strong>
                      <small>{s.description}</small>
                    </span>
                    {selected.includes(s.id) && <Check size={13} />}
                  </label>
                ))}
            </div>
            <p className="ai-skill-select-note">
              不选择技能时使用当前助理的基础能力。
            </p>
          </section>
        </AIFloatingMenu>
      )}
    </div>
  );
}
