import { Check, ChevronDown } from "lucide-react";
import { AnimatePresence, motion, useIsPresent } from "motion/react";
import {
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
  type RefObject,
  useEffect,
  useRef,
} from "react";
import aiAgentLogo from "@/assets/ai-agent-logo.png";
import { AIFloatingMenu } from "@/components/AIFloatingMenu";
import { motionDuration, motionEase } from "@/lib/motion";
import type { AIAgentPreset } from "@/lib/types";
export interface Choice<T extends string> {
  value: T;
  label: string;
  detail?: string;
}

export function AnimatedChoiceMenu({
  id,
  className = "ai-choice-menu",
  label,
  children,
  menuRef,
  onKeyDown,
  anchorRef,
  onClose,
}: {
  id: string;
  className?: string;
  label: string;
  children: ReactNode;
  menuRef?: RefObject<HTMLDivElement | null>;
  onKeyDown?: (event: ReactKeyboardEvent<HTMLDivElement>) => void;
  anchorRef?: RefObject<HTMLElement | null>;
  onClose?: () => void;
}) {
  const isPresent = useIsPresent();
  const content = (
    <motion.div
      ref={menuRef}
      id={id}
      className={className}
      role="listbox"
      aria-label={label}
      aria-hidden={!isPresent}
      inert={!isPresent ? true : undefined}
      onKeyDown={onKeyDown}
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
    </motion.div>
  );
  return anchorRef && onClose ? (
    <AIFloatingMenu anchorRef={anchorRef} onClose={onClose} width={280}>
      {content}
    </AIFloatingMenu>
  ) : (
    content
  );
}

export function ChoiceMenu<T extends string>({
  label,
  triggerLabel = label,
  menuId,
  value,
  items,
  open,
  disabled,
  onToggle,
  onSelect,
}: {
  label: string;
  triggerLabel?: string;
  menuId: string;
  value: T;
  items: Choice<T>[];
  open: boolean;
  disabled: boolean;
  onToggle: () => void;
  onSelect: (value: T) => void;
}) {
  const selected = items.find((item) => item.value === value) ?? items[0];
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    queueMicrotask(() => {
      menuRef.current
        ?.querySelector<HTMLButtonElement>(
          '[role="option"][aria-selected="true"]',
        )
        ?.focus();
    });
  }, [open]);

  const focusRelativeOption = (
    event: ReactKeyboardEvent<HTMLDivElement>,
    direction: -1 | 1,
  ) => {
    const options = Array.from(
      menuRef.current?.querySelectorAll<HTMLButtonElement>('[role="option"]') ??
        [],
    );
    if (options.length === 0) return;
    event.preventDefault();
    const current = options.indexOf(
      document.activeElement as HTMLButtonElement,
    );
    const next =
      current < 0 ? 0 : (current + direction + options.length) % options.length;
    options[next]?.focus();
  };

  const closeAndRestoreFocus = () => {
    if (open) onToggle();
    queueMicrotask(() => triggerRef.current?.focus());
  };

  const handleMenuKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key === "ArrowDown") focusRelativeOption(event, 1);
    else if (event.key === "ArrowUp") focusRelativeOption(event, -1);
    else if (event.key === "Home" || event.key === "End") {
      const options = Array.from(
        menuRef.current?.querySelectorAll<HTMLButtonElement>(
          '[role="option"]',
        ) ?? [],
      );
      event.preventDefault();
      options[event.key === "Home" ? 0 : options.length - 1]?.focus();
    } else if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      closeAndRestoreFocus();
    }
  };

  return (
    <div className="ai-compact-choice">
      <button
        ref={triggerRef}
        type="button"
        className="ai-compact-trigger"
        aria-label={`${label}：${selected?.label ?? value}`}
        aria-controls={menuId}
        aria-haspopup="listbox"
        aria-expanded={open}
        disabled={disabled}
        onClick={onToggle}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            if (!open) onToggle();
          }
        }}
      >
        <span className="ai-compact-trigger-copy">
          <span>{triggerLabel}：</span>
          <strong>{selected?.label ?? value}</strong>
        </span>
        <ChevronDown aria-hidden="true" />
      </button>
      <AnimatePresence>
        {open && (
          <AnimatedChoiceMenu
            id={menuId}
            label={label}
            menuRef={menuRef}
            onKeyDown={handleMenuKeyDown}
            anchorRef={triggerRef}
            onClose={closeAndRestoreFocus}
          >
            <header>{label}</header>
            {items.map((item) => (
              <button
                key={item.value}
                type="button"
                role="option"
                aria-selected={item.value === value}
                onClick={() => {
                  onSelect(item.value);
                  queueMicrotask(() => triggerRef.current?.focus());
                }}
              >
                <span>
                  <strong>{item.label}</strong>
                  {item.detail && <small>{item.detail}</small>}
                </span>
                {item.value === value && <Check aria-hidden="true" />}
              </button>
            ))}
          </AnimatedChoiceMenu>
        )}
      </AnimatePresence>
    </div>
  );
}

export function AgentChoice({
  presets,
  selectedPresetId,
  open,
  onToggle,
  onClose,
  onSelectPreset,
}: {
  presets: AIAgentPreset[];
  selectedPresetId: string;
  open: boolean;
  onToggle: () => void;
  onClose: () => void;
  onSelectPreset: (id: string) => void;
}) {
  const agentTriggerRef = useRef<HTMLButtonElement>(null);
  const agentMenuRef = useRef<HTMLDivElement>(null);
  const selectedPreset = presets.find(
    (preset) => preset.id === selectedPresetId,
  );
  useEffect(() => {
    if (open)
      queueMicrotask(() =>
        (
          agentMenuRef.current?.querySelector<HTMLButtonElement>(
            '[aria-selected="true"]',
          ) ||
          agentMenuRef.current?.querySelector<HTMLButtonElement>(
            '[role="option"]',
          )
        )?.focus(),
      );
  }, [open]);
  return (
    <div className="ai-compact-choice ai-agent-choice">
      <button
        ref={agentTriggerRef}
        type="button"
        className="ai-agent-trigger"
        onKeyDown={(event) => {
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            if (!open) onToggle();
          }
        }}
        aria-label={`Agent：${selectedPreset?.name ?? "未选择"}`}
        aria-controls="ai-composer-agent-menu"
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => onToggle()}
      >
        <img src={aiAgentLogo} alt="" aria-hidden="true" />
        <span>{selectedPreset?.name ?? "选择 Agent"}</span>
      </button>
      <AnimatePresence>
        {open && (
          <AnimatedChoiceMenu
            anchorRef={agentTriggerRef}
            onClose={() => onClose()}
            id="ai-composer-agent-menu"
            className="ai-choice-menu ai-agent-menu"
            label="Agent 预设"
            menuRef={agentMenuRef}
            onKeyDown={(event) => {
              const options = Array.from(
                event.currentTarget.querySelectorAll<HTMLButtonElement>(
                  '[role="option"]',
                ),
              );
              const current = options.indexOf(
                document.activeElement as HTMLButtonElement,
              );
              let next: number;
              if (event.key === "ArrowDown")
                next = (current + 1) % options.length;
              else if (event.key === "ArrowUp")
                next = current <= 0 ? options.length - 1 : current - 1;
              else if (event.key === "Home") next = 0;
              else if (event.key === "End") next = options.length - 1;
              else if (event.key === "Escape") {
                event.preventDefault();
                event.stopPropagation();
                onClose();
                queueMicrotask(() => agentTriggerRef.current?.focus());
                return;
              } else return;
              event.preventDefault();
              options[next]?.focus();
            }}
          >
            <header>选择 Agent</header>
            {presets.map((preset) => (
              <button
                key={preset.id}
                type="button"
                role="option"
                aria-selected={preset.id === selectedPresetId}
                onClick={() => {
                  onSelectPreset(preset.id);
                  onClose();
                  queueMicrotask(() => agentTriggerRef.current?.focus());
                }}
              >
                <img src={aiAgentLogo} alt="" aria-hidden="true" />
                <span>
                  <strong>{preset.name}</strong>
                  <small>{preset.description}</small>
                </span>
                {preset.id === selectedPresetId && <Check aria-hidden="true" />}
              </button>
            ))}
          </AnimatedChoiceMenu>
        )}
      </AnimatePresence>
    </div>
  );
}
