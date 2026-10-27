import { Check, Settings2, X } from "lucide-react";
import { AnimatePresence, motion, useIsPresent } from "motion/react";
import { useEffect, useRef, useState } from "react";
import {
  SelectionIndicator,
  useSelectionIndicatorId,
} from "@/components/ui/SelectionIndicator";
import { motionDuration, motionEase } from "@/lib/motion";
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
      aria-label="AI 助手个性化设置"
      aria-modal="false"
      aria-hidden={!isPresent}
      inert={!isPresent ? true : undefined}
      initial={{ opacity: 0.65 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{
        type: "tween",
        duration: isPresent ? motionDuration.popoverEnter : motionDuration.fast,
        ease: motionEase.out,
      }}
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

const isMacPlatform = () =>
  !/Win|Linux|Android/i.test(navigator.platform || navigator.userAgent);

function buildShortcutChoices(mac: boolean): Choice<AIChatSendShortcut>[] {
  return [
    { value: "enter", label: "Enter 发送", detail: "Shift + Enter 换行" },
    {
      value: "cmd_enter",
      label: mac ? "⌘ + Enter 发送" : "Ctrl + Enter 发送",
      detail: "Enter 或 Shift + Enter 换行",
    },
  ];
}

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
  const indicatorId = useSelectionIndicatorId("ai-preference");
  return (
    // 保存期间不禁用 fieldset：Chromium/WebView2 会把已聚焦的禁用按钮焦点移到 <body>，
    // 方向键切换一次后键盘导航即失效。改为 aria-disabled 并在处理函数中忽略输入。
    <fieldset className="ai-preference-group">
      <legend>{label}</legend>
      <div
        className="ai-preference-options"
        role="radiogroup"
        aria-label={label}
        aria-disabled={disabled || undefined}
        onKeyDown={(event) => {
          const current = choices.findIndex((choice) => choice.value === value);
          const delta =
            event.key === "ArrowRight" || event.key === "ArrowDown"
              ? 1
              : event.key === "ArrowLeft" || event.key === "ArrowUp"
                ? -1
                : 0;
          const index =
            event.key === "Home"
              ? 0
              : event.key === "End"
                ? choices.length - 1
                : delta
                  ? (current + delta + choices.length) % choices.length
                  : -1;
          if (index < 0) return;
          event.preventDefault();
          if (disabled) return;
          const next = choices[index];
          onChange(next.value);
          event.currentTarget
            .querySelectorAll<HTMLElement>("[role='radio']")
            [index]?.focus();
        }}
      >
        {choices.map((choice) => (
          <button
            key={choice.value}
            type="button"
            data-selection-layout-id={indicatorId}
            role="radio"
            aria-checked={choice.value === value}
            tabIndex={choice.value === value ? 0 : -1}
            className={
              choice.value === value
                ? "selection-host selection-active"
                : "selection-host"
            }
            title={choice.detail}
            onClick={() => {
              if (!disabled) onChange(choice.value);
            }}
          >
            {choice.value === value && (
              <SelectionIndicator
                layoutId={indicatorId}
                radius={7}
                className="ai-preference-indicator"
              />
            )}
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
  const shortcutChoices = buildShortcutChoices(isMacPlatform());
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
      if (
        event.key === "Escape" &&
        !event.isComposing &&
        !event.defaultPrevented
      ) {
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

  useEffect(() => {
    if (!open) return;
    rootRef.current
      ?.querySelector<HTMLElement>(
        ".ai-personalization-panel [role='radio'][aria-checked='true']",
      )
      ?.focus();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const root = rootRef.current;
    const handleFocusOut = (event: FocusEvent) => {
      if (
        event.relatedTarget instanceof Node &&
        !root?.contains(event.relatedTarget)
      )
        close();
    };
    root?.addEventListener("focusout", handleFocusOut);
    return () => root?.removeEventListener("focusout", handleFocusOut);
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
                  <strong>代码块行号</strong>
                  <small>在多行代码旁显示行号</small>
                </span>
                {/* 与上方单选组相同：保存期间不用 disabled，否则 WebView2 会把已聚焦的
                    checkbox 焦点丢到 <body>；受控 checkbox 忽略 onChange 时保持原值。 */}
                <input
                  type="checkbox"
                  checked={preferences.ai_code_line_numbers}
                  aria-disabled={saving || undefined}
                  onChange={(event) => {
                    if (!saving)
                      onChange({ ai_code_line_numbers: event.target.checked });
                  }}
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
