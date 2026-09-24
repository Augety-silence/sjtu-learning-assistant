import { Check, Settings2, X } from "lucide-react";
import { AnimatePresence, motion, useIsPresent } from "motion/react";
import { useEffect, useRef, useState } from "react";
import type {
  AIAttachmentContextBudget,
  AIChatPreferences,
  AIChatSendShortcut,
  AIReplyLanguage,
} from "@/lib/types";

type PreferenceChange = Partial<AIChatPreferences>;

function PersonalizationSurface({ children }: { children: React.ReactNode }) {
  const isPresent = useIsPresent();
  return (
    <motion.section
      className="ai-personalization-panel"
      role="dialog"
      aria-label="AI Chat 个性化设置"
      aria-modal="false"
      aria-hidden={!isPresent}
      inert={!isPresent ? true : undefined}
      initial={{ opacity: 0.01, y: 6, scale: 0.985 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: 4, scale: 0.99 }}
      transition={{ duration: isPresent ? 0.2 : 0.15, ease: [0.16, 1, 0.3, 1] }}
    >
      {children}
    </motion.section>
  );
}

interface Choice<T extends string> {
  value: T;
  label: string;
  detail: string;
}

const shortcutChoices: Choice<AIChatSendShortcut>[] = [
  { value: "enter", label: "Enter 发送", detail: "Shift + Enter 换行" },
  {
    value: "cmd_enter",
    label: "⌘ + Enter 发送",
    detail: "Enter 或 Shift + Enter 换行",
  },
];

const languageChoices: Choice<AIReplyLanguage>[] = [
  { value: "auto", label: "自动", detail: "跟随问题语言" },
  { value: "zh", label: "中文", detail: "偏好中文回复" },
  { value: "en", label: "英文", detail: "Prefer English replies" },
];

const budgetChoices: Choice<AIAttachmentContextBudget>[] = [
  { value: "economy", label: "省流", detail: "保留更短的附件上下文" },
  { value: "balanced", label: "均衡", detail: "兼顾细节与响应速度" },
  { value: "deep", label: "深入", detail: "为长文分析预留更多上下文" },
];

function SegmentedChoice<T extends string>({
  label,
  value,
  choices,
  disabled,
  onChange,
}: {
  label: string;
  value: T;
  choices: Choice<T>[];
  disabled: boolean;
  onChange: (value: T) => void;
}) {
  return (
    <fieldset className="ai-preference-group" disabled={disabled}>
      <legend>{label}</legend>
      <div className="ai-preference-options">
        {choices.map((choice) => (
          <button
            key={choice.value}
            type="button"
            role="radio"
            aria-checked={choice.value === value}
            title={choice.detail}
            onClick={() => onChange(choice.value)}
          >
            <span>{choice.label}</span>
            {choice.value === value && <Check aria-hidden="true" />}
          </button>
        ))}
      </div>
      <small>{choices.find((choice) => choice.value === value)?.detail}</small>
    </fieldset>
  );
}

export function AIChatSettingsPanel({
  preferences,
  saving,
  status,
  onChange,
}: {
  preferences: AIChatPreferences;
  saving: boolean;
  status: string | null;
  onChange: (change: PreferenceChange) => void;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const close = (restoreFocus = false) => {
    setOpen(false);
    if (restoreFocus) window.setTimeout(() => triggerRef.current?.focus(), 0);
  };

  useEffect(() => {
    if (!open) return;
    const handleOutside = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) close();
    };
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        close(true);
      }
    };
    document.addEventListener("mousedown", handleOutside);
    document.addEventListener("keydown", handleEscape);
    return () => {
      document.removeEventListener("mousedown", handleOutside);
      document.removeEventListener("keydown", handleEscape);
    };
  }, [open]);

  return (
    <div className="ai-personalization" ref={rootRef}>
      <AnimatePresence>
        {open && (
          <PersonalizationSurface>
            <header>
              <span>
                <strong>个性化设置</strong>
                <small>只保存在这台设备</small>
              </span>
              <button
                type="button"
                className="ai-personalization-close"
                aria-label="关闭个性化设置"
                onClick={() => close(true)}
              >
                <X aria-hidden="true" />
              </button>
            </header>

            <div className="ai-personalization-scroll">
              <SegmentedChoice
                label="发送快捷键"
                value={preferences.ai_chat_send_shortcut}
                choices={shortcutChoices}
                disabled={saving}
                onChange={(value) => onChange({ ai_chat_send_shortcut: value })}
              />
              <SegmentedChoice
                label="回复语言"
                value={preferences.ai_reply_language}
                choices={languageChoices}
                disabled={saving}
                onChange={(value) => onChange({ ai_reply_language: value })}
              />
              <SegmentedChoice
                label="附件上下文预算"
                value={preferences.ai_attachment_context_budget}
                choices={budgetChoices}
                disabled={saving}
                onChange={(value) =>
                  onChange({ ai_attachment_context_budget: value })
                }
              />
              <p className="ai-preference-note">
                回复语言与附件预算会保存为偏好；当前 Agent
                运行时尚未提供安全扩展点，因此不会改写既有请求契约。
              </p>
              <label className="ai-preference-toggle">
                <span>
                  <strong>自动打开 Activity</strong>
                  <small>发送任务时展开执行轨迹</small>
                </span>
                <input
                  type="checkbox"
                  checked={preferences.ai_auto_open_activity}
                  disabled={saving}
                  onChange={(event) =>
                    onChange({ ai_auto_open_activity: event.target.checked })
                  }
                />
              </label>
              <label className="ai-preference-toggle">
                <span>
                  <strong>代码块行号</strong>
                  <small>在多行代码旁显示行号</small>
                </span>
                <input
                  type="checkbox"
                  checked={preferences.ai_code_line_numbers}
                  disabled={saving}
                  onChange={(event) =>
                    onChange({ ai_code_line_numbers: event.target.checked })
                  }
                />
              </label>
            </div>
            <p className="ai-personalization-status" role="status">
              {saving ? "正在保存…" : status || "设置会自动保存"}
            </p>
          </PersonalizationSurface>
        )}
      </AnimatePresence>
      <button
        ref={triggerRef}
        type="button"
        className="ai-personalization-trigger"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
      >
        <Settings2 aria-hidden="true" />
        <span>个性化设置</span>
      </button>
    </div>
  );
}
