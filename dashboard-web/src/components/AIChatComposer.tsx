import { ArrowUp, Check, LoaderCircle, Paperclip, X } from "lucide-react";
import {
  type FormEvent,
  type RefObject,
  useEffect,
  useRef,
  useState,
} from "react";
import aiAgentLogo from "@/assets/ai-agent-logo.png";
import { Button } from "@/components/ui/Button";
import type {
  AIAgentPreset,
  AIChatSendShortcut,
  AIManagedAttachment,
  AIModel,
  AIThinkingDepth,
} from "@/lib/types";

interface Choice<T extends string> {
  value: T;
  label: string;
  detail?: string;
}

function ChoiceMenu<T extends string>({
  label,
  menuId,
  value,
  items,
  open,
  disabled,
  onToggle,
  onSelect,
}: {
  label: string;
  menuId: string;
  value: T;
  items: Choice<T>[];
  open: boolean;
  disabled: boolean;
  onToggle: () => void;
  onSelect: (value: T) => void;
}) {
  const selected = items.find((item) => item.value === value) ?? items[0];
  return (
    <div className="ai-compact-choice">
      <button
        type="button"
        className="ai-compact-trigger"
        aria-label={`${label}：${selected?.label ?? value}`}
        aria-controls={menuId}
        aria-haspopup="listbox"
        aria-expanded={open}
        disabled={disabled}
        onClick={onToggle}
      >
        <span className="ai-choice-dot" aria-hidden="true" />
        <span>{selected?.label ?? value}</span>
      </button>
      {open && (
        <div
          id={menuId}
          className="ai-choice-menu"
          role="listbox"
          aria-label={label}
        >
          <header>{label}</header>
          {items.map((item) => (
            <button
              key={item.value}
              type="button"
              role="option"
              aria-selected={item.value === value}
              onClick={() => onSelect(item.value)}
            >
              <span>
                <strong>{item.label}</strong>
                {item.detail && <small>{item.detail}</small>}
              </span>
              {item.value === value && <Check aria-hidden="true" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export function AIChatComposer({
  draft,
  error,
  busy,
  attachmentBusy,
  attachments,
  active,
  presets,
  selectedPresetId,
  model,
  depth,
  models,
  depths,
  hasMessages,
  sendShortcut,
  textareaRef,
  onDraftChange,
  onSelectPreset,
  onModelChange,
  onDepthChange,
  onPickAttachment,
  onRemoveAttachment,
  onSubmit,
}: {
  draft: string;
  error: string | null;
  busy: boolean;
  attachmentBusy: boolean;
  attachments: AIManagedAttachment[];
  active: boolean;
  presets: AIAgentPreset[];
  selectedPresetId: string;
  model: AIModel;
  depth: AIThinkingDepth;
  models: Choice<AIModel>[];
  depths: Choice<AIThinkingDepth>[];
  hasMessages: boolean;
  sendShortcut: AIChatSendShortcut;
  textareaRef: RefObject<HTMLTextAreaElement | null>;
  onDraftChange: (value: string) => void;
  onSelectPreset: (value: string) => void;
  onModelChange: (value: AIModel) => void;
  onDepthChange: (value: AIThinkingDepth) => void;
  onPickAttachment: () => void;
  onRemoveAttachment: (id: number) => void;
  onSubmit: (value: string) => void;
}) {
  const [openMenu, setOpenMenu] = useState<"agent" | "model" | "depth" | null>(
    null,
  );
  const controlsRef = useRef<HTMLDivElement>(null);
  const selectedPreset =
    presets.find((preset) => preset.id === selectedPresetId) ?? null;

  useEffect(() => {
    const closeOnOutside = (event: MouseEvent) => {
      if (!controlsRef.current?.contains(event.target as Node))
        setOpenMenu(null);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpenMenu(null);
    };
    document.addEventListener("mousedown", closeOnOutside);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("mousedown", closeOnOutside);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, []);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    onSubmit(draft);
  };

  return (
    <form className="ai-composer" onSubmit={submit}>
      {error && (
        <p className="ai-chat-error" role="alert">
          {error}
        </p>
      )}
      <div className="ai-composer-box">
        <textarea
          ref={textareaRef}
          className="ai-composer-input"
          value={draft}
          maxLength={4000}
          rows={hasMessages ? 2 : 3}
          aria-label="输入问题"
          placeholder="让 Agent 检索课程、文件、截止日期或消息…"
          onChange={(event) => onDraftChange(event.target.value)}
          onKeyDown={(event) => {
            const shouldSend =
              event.key === "Enter" &&
              !event.shiftKey &&
              (sendShortcut === "enter" || event.metaKey);
            if (shouldSend) {
              event.preventDefault();
              onSubmit(draft);
            }
          }}
        />
        <div
          className="ai-composer-toolbar"
          ref={controlsRef}
          role="toolbar"
          aria-label="Agent 与输入工具"
        >
          <div className="ai-chat-controls">
            <button
              type="button"
              className="ai-attachment-button"
              aria-label={attachmentBusy ? "正在添加附件" : "添加本地附件"}
              disabled={busy || attachmentBusy || attachments.length >= 20}
              onClick={onPickAttachment}
            >
              {attachmentBusy ? (
                <LoaderCircle className="ai-spin" aria-hidden="true" />
              ) : (
                <Paperclip aria-hidden="true" />
              )}
            </button>
            {attachments.length > 0 && (
              <div className="ai-attachment-chips" aria-label="待发送附件">
                {attachments.map((attachment) => (
                  <span className="ai-attachment-chip" key={attachment.id}>
                    <span title={attachment.summary ?? attachment.name}>
                      {attachment.name}
                    </span>
                    <button
                      type="button"
                      aria-label={`移除附件 ${attachment.name}`}
                      disabled={busy || attachmentBusy}
                      onClick={() => onRemoveAttachment(attachment.id)}
                    >
                      <X aria-hidden="true" />
                    </button>
                  </span>
                ))}
              </div>
            )}
            <div className="ai-compact-choice ai-agent-choice">
              <button
                type="button"
                className="ai-agent-trigger"
                aria-label={`Agent：${selectedPreset?.name ?? "未选择"}`}
                aria-controls="ai-composer-agent-menu"
                aria-haspopup="listbox"
                aria-expanded={openMenu === "agent"}
                disabled={busy}
                onClick={() =>
                  setOpenMenu((menu) => (menu === "agent" ? null : "agent"))
                }
              >
                <img src={aiAgentLogo} alt="" aria-hidden="true" />
                <span>{selectedPreset?.name ?? "选择 Agent"}</span>
              </button>
              {openMenu === "agent" && (
                <div
                  id="ai-composer-agent-menu"
                  className="ai-choice-menu ai-agent-menu"
                  role="listbox"
                  aria-label="Agent 预设"
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
                        setOpenMenu(null);
                      }}
                    >
                      <img src={aiAgentLogo} alt="" aria-hidden="true" />
                      <span>
                        <strong>{preset.name}</strong>
                        <small>{preset.description}</small>
                      </span>
                      {preset.id === selectedPresetId && (
                        <Check aria-hidden="true" />
                      )}
                    </button>
                  ))}
                </div>
              )}
            </div>
            <ChoiceMenu
              label="模型"
              menuId="ai-model-menu"
              value={model}
              items={models}
              open={openMenu === "model"}
              disabled={busy}
              onToggle={() =>
                setOpenMenu((menu) => (menu === "model" ? null : "model"))
              }
              onSelect={(value) => {
                onModelChange(value);
                setOpenMenu(null);
              }}
            />
            <ChoiceMenu
              label="推理强度"
              menuId="ai-depth-menu"
              value={depth}
              items={depths}
              open={openMenu === "depth"}
              disabled={busy}
              onToggle={() =>
                setOpenMenu((menu) => (menu === "depth" ? null : "depth"))
              }
              onSelect={(value) => {
                onDepthChange(value);
                setOpenMenu(null);
              }}
            />
          </div>
          <Button
            type="submit"
            size="icon"
            aria-label="发送消息"
            disabled={
              busy ||
              attachmentBusy ||
              (!draft.trim() && attachments.length === 0) ||
              !active ||
              !selectedPresetId
            }
          >
            <ArrowUp aria-hidden="true" />
          </Button>
        </div>
      </div>
      <p className="ai-chat-notice">
        只读访问本机同步数据 · AI 可能出错，请核对截止时间与提交要求
      </p>
    </form>
  );
}
