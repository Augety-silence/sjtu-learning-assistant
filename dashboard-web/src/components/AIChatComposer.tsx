import {
  ArrowUp,
  FolderOpen,
  LoaderCircle,
  Paperclip,
  Plus,
  Server,
  Sparkles,
  Square,
  X,
} from "lucide-react";
import { AnimatePresence } from "motion/react";
import {
  type FormEvent,
  type RefObject,
  useEffect,
  useRef,
  useState,
} from "react";
import {
  AgentChoice,
  type Choice,
  ChoiceMenu,
} from "@/components/AIComposerChoice";
import { AIComposerFooter } from "@/components/AIComposerFooter";
import { AIFloatingMenu } from "@/components/AIFloatingMenu";
import {
  AISelectedSkillAttachments,
  AISelectedSkills,
} from "@/components/AISelectedSkills";
import { Button } from "@/components/ui/Button";
import type {
  AIAgentPreset,
  AIChatSendShortcut,
  AIExtensionSkill,
  AIManagedAttachment,
  AIModel,
  AIThinkingDepth,
  AIWorkspaceStatus,
} from "@/lib/types";
import { useUIRelease } from "@/lib/uiRelease";

const COMPOSER_MIN_HEIGHT = 42;
const COMPOSER_MAX_HEIGHT = 160;
const COMPOSER_MAX_VIEWPORT_RATIO = 0.32;

function attachmentTextStatusLabel(attachment: AIManagedAttachment) {
  if (attachment.text_status === "ready") return "正文已就绪";
  if (attachment.text_status === "unsupported")
    return "仅可查看文件名，正文格式不支持读取";
  if (
    attachment.text_status === "failed" ||
    attachment.text_status === "unavailable"
  )
    return "仅可查看文件名，正文不可读取";
  return "正文仍在处理中";
}

export function AIChatComposer({
  draft,
  error,
  attachmentBusy,
  attachments,
  active,
  presets,
  selectedPresetId,
  skills = [],
  selectedSkills = [],
  onSelectSkills = () => undefined,
  model,
  depth,
  models,
  depths,
  hasMessages,
  sendShortcut,
  textareaRef,
  onDraftChange,
  workspace,
  onOpenWorkspace,
  onOpenResources,
  onChangeWorkspace,
  workspaceBusy = false,
  workspaceError,
  onSelectPreset,
  onModelChange,
  onDepthChange,
  onPickAttachment,
  onRemoveAttachment,
  onSubmit,
  onRetry,
  retryDisabled,
  busy,
  stopping,
  onCancel,
}: {
  draft: string;
  workspace?: AIWorkspaceStatus | null;
  onOpenWorkspace?: () => void;
  onOpenResources?: (page: "skills" | "mcp") => void;
  onChangeWorkspace?: (action: "pick" | "clear") => void;
  workspaceBusy?: boolean;
  workspaceError?: string | null;
  error: string | null;
  attachmentBusy: boolean;
  attachments: AIManagedAttachment[];
  active: boolean;
  presets: AIAgentPreset[];
  selectedPresetId: string;
  skills?: AIExtensionSkill[];
  selectedSkills?: string[];
  onSelectSkills?: (ids: string[]) => void;
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
  onRetry?: () => void;
  retryDisabled?: boolean;
  busy?: boolean;
  stopping?: boolean;
  onCancel?: () => void;
}) {
  const uiRelease = useUIRelease();
  const [openMenu, setOpenMenu] = useState<
    | "agent"
    | "model"
    | "depth"
    | "resources"
    | "workspace"
    | "permission"
    | null
  >(null);
  const controlsRef = useRef<HTMLDivElement>(null);
  const resourceTrigger = useRef<HTMLButtonElement>(null);
  const hasDraft = Boolean(draft.trim()) || attachments.length > 0;
  const showStop = Boolean(
    busy && onCancel && (uiRelease !== "2" || !hasDraft),
  );

  useEffect(() => {
    const input = textareaRef.current;
    if (!input) return;
    const maxHeight = Math.min(
      COMPOSER_MAX_HEIGHT,
      window.innerHeight * COMPOSER_MAX_VIEWPORT_RATIO,
    );
    input.style.height = "0px";
    const contentHeight = draft ? input.scrollHeight : COMPOSER_MIN_HEIGHT;
    input.style.height = `${Math.min(
      Math.max(contentHeight, COMPOSER_MIN_HEIGHT),
      maxHeight,
    )}px`;
    input.style.overflowY = contentHeight > maxHeight ? "auto" : "hidden";
  }, [draft, hasMessages, textareaRef]);

  useEffect(() => {
    const closeOnOutside = (event: MouseEvent) => {
      if (
        !controlsRef.current?.contains(event.target as Node) &&
        !(event.target as Element)?.closest?.("[data-ai-floating-menu]")
      )
        setOpenMenu(null);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (
        event.key === "Escape" &&
        !event.isComposing &&
        !event.defaultPrevented
      )
        setOpenMenu(null);
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

  const attachmentChips = attachments.length > 0 && (
    <div className="ai-attachment-chips" role="group" aria-label="待发送附件">
      {attachments.map((attachment) => (
        <span className="ai-attachment-chip" key={attachment.id}>
          <span
            aria-label={
              attachment.name + "：" + attachmentTextStatusLabel(attachment)
            }
            title={
              attachment.text_status === "ready"
                ? (attachment.summary ?? attachment.name)
                : attachment.name + "：" + attachmentTextStatusLabel(attachment)
            }
          >
            {attachment.name}
          </span>
          <button
            type="button"
            aria-label={`移除附件 ${attachment.name}`}
            disabled={attachmentBusy}
            onClick={(event) => {
              // 被点击的按钮会随 chip 卸载，焦点会掉到 <body>；先移到相邻 chip 的移除按钮，没有则回到输入框。
              const chip = event.currentTarget.closest(".ai-attachment-chip");
              const sibling = (
                chip?.nextElementSibling ?? chip?.previousElementSibling
              )?.querySelector<HTMLButtonElement>("button");
              onRemoveAttachment(attachment.id);
              (sibling ?? textareaRef.current)?.focus();
            }}
          >
            <X aria-hidden="true" />
          </button>
        </span>
      ))}
    </div>
  );

  const modelControls = (
    <>
      <ChoiceMenu
        label="模型"
        menuId="ai-model-menu"
        value={model}
        items={models}
        open={openMenu === "model"}
        disabled={false}
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
        triggerLabel="推理"
        menuId="ai-depth-menu"
        value={depth}
        items={depths}
        open={openMenu === "depth"}
        disabled={false}
        onToggle={() =>
          setOpenMenu((menu) => (menu === "depth" ? null : "depth"))
        }
        onSelect={(value) => {
          onDepthChange(value);
          setOpenMenu(null);
        }}
      />
    </>
  );
  return (
    <form className="ai-composer" onSubmit={submit}>
      {error && (
        <div className="ai-chat-error" role="status">
          <span>{error}</span>
          {onRetry && (
            <button
              type="button"
              disabled={retryDisabled}
              title={retryDisabled ? "当前任务完成或停止后可重试" : undefined}
              onClick={onRetry}
            >
              {retryDisabled ? "当前任务结束后可重试" : "重试本次"}
            </button>
          )}
        </div>
      )}
      <div className="ai-composer-box">
        {uiRelease === "2" && attachmentChips}
        {uiRelease === "2" && (
          <AISelectedSkillAttachments
            skills={skills}
            selected={selectedSkills}
            onChange={onSelectSkills}
            busy={busy}
          />
        )}
        <textarea
          ref={textareaRef}
          className="ai-composer-input"
          value={draft}
          maxLength={4000}
          rows={1}
          aria-label="输入问题"
          placeholder={
            uiRelease === "2"
              ? "输入你的问题，添加文件或选择技能与工具"
              : "让 Agent 检索课程、文件、截止日期或消息…"
          }
          onChange={(event) => onDraftChange(event.target.value)}
          onKeyDown={(event) => {
            // Ignore Enter while an IME (e.g. Chinese pinyin) is composing a candidate.
            if (event.nativeEvent.isComposing || event.keyCode === 229) return;
            const shouldSend =
              event.key === "Enter" &&
              !event.shiftKey &&
              (sendShortcut === "enter" || event.metaKey || event.ctrlKey);
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
              ref={resourceTrigger}
              className="ai-attachment-button"
              aria-label={
                attachmentBusy
                  ? "正在添加附件"
                  : attachments.length >= 20
                    ? "已达到20个附件上限"
                    : uiRelease === "2"
                      ? "唤起技能与工具"
                      : "添加本地附件"
              }
              title={
                attachments.length >= 20
                  ? "每段对话最多添加 20 个附件"
                  : undefined
              }
              disabled={attachmentBusy || attachments.length >= 20}
              onClick={() =>
                uiRelease === "2"
                  ? setOpenMenu(openMenu === "resources" ? null : "resources")
                  : onPickAttachment()
              }
            >
              {attachmentBusy ? (
                <LoaderCircle className="ai-spin" aria-hidden="true" />
              ) : uiRelease === "2" ? (
                <Plus aria-hidden="true" />
              ) : (
                <Paperclip aria-hidden="true" />
              )}
            </button>
            {uiRelease === "2" && openMenu === "resources" && (
              <AIFloatingMenu
                anchorRef={resourceTrigger}
                onClose={() => setOpenMenu(null)}
                width={260}
              >
                <div
                  className="ai-resource-actions"
                  role="menu"
                  aria-label="添加与控制"
                >
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => {
                      setOpenMenu(null);
                      onPickAttachment();
                    }}
                  >
                    <Paperclip />
                    添加本地文件
                  </button>
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => {
                      setOpenMenu(null);
                      onOpenWorkspace?.();
                    }}
                  >
                    <FolderOpen />
                    工作区文件
                  </button>
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => {
                      setOpenMenu(null);
                      onOpenResources?.("skills");
                    }}
                  >
                    <Sparkles />
                    技能配置
                  </button>
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => {
                      setOpenMenu(null);
                      onOpenResources?.("mcp");
                    }}
                  >
                    <Server />
                    MCP 与工具权限
                  </button>
                </div>
              </AIFloatingMenu>
            )}
            {uiRelease !== "2" && attachmentChips}
            <AgentChoice
              presets={presets}
              selectedPresetId={selectedPresetId}
              open={openMenu === "agent"}
              onToggle={() =>
                setOpenMenu((menu) => (menu === "agent" ? null : "agent"))
              }
              onClose={() => setOpenMenu(null)}
              onSelectPreset={onSelectPreset}
            />
            {uiRelease === "2" && (
              <AISelectedSkills
                skills={skills}
                selected={selectedSkills}
                onChange={onSelectSkills}
                busy={busy}
              />
            )}
            {uiRelease !== "2" && modelControls}
          </div>
          <div
            className="ai-composer-actions"
            style={uiRelease !== "2" ? { display: "contents" } : undefined}
          >
            {uiRelease === "2" && modelControls}
            {showStop && (
              <Button
                type="button"
                className="ai-stop-button"
                size={uiRelease === "2" ? "icon" : stopping ? "sm" : "icon"}
                aria-label={stopping ? "正在停止本次任务" : "停止本次任务"}
                title={
                  stopping
                    ? "正在等待当前模型或工具请求返回"
                    : "停止当前会话的任务"
                }
                disabled={stopping}
                onClick={onCancel}
              >
                {uiRelease === "2" ? (
                  stopping ? (
                    <LoaderCircle className="ai-spin" aria-hidden="true" />
                  ) : (
                    <Square aria-hidden="true" />
                  )
                ) : stopping ? (
                  <>
                    <LoaderCircle className="ai-spin" aria-hidden="true" />
                    正在停止
                  </>
                ) : (
                  <X aria-hidden="true" />
                )}
              </Button>
            )}
            {!(uiRelease === "2" && showStop) && (
              <Button
                type="submit"
                size="icon"
                aria-label={busy ? "补充指令" : "发送消息"}
                title={busy ? "立即补充当前任务的要求" : "发送消息"}
                disabled={
                  attachmentBusy ||
                  (!draft.trim() && attachments.length === 0) ||
                  !active ||
                  !selectedPresetId
                }
              >
                <ArrowUp aria-hidden="true" />
              </Button>
            )}
          </div>
        </div>
      </div>
      {uiRelease === "2" ? (
        <AIComposerFooter
          workspace={workspace}
          workspaceBusy={workspaceBusy}
          workspaceError={workspaceError}
          onChangeWorkspace={onChangeWorkspace}
          onOpenWorkspace={onOpenWorkspace}
          attachmentBusy={attachmentBusy}
          attachmentCount={attachments.length}
          onPickAttachment={onPickAttachment}
          models={models}
          model={model}
          onModelChange={onModelChange}
          openMenu={openMenu}
          setOpenMenu={setOpenMenu}
          length={draft.length}
        />
      ) : (
        <div className="ai-composer-footer">
          <p className="ai-chat-notice">{draft.length}/4000 字</p>
        </div>
      )}
    </form>
  );
}
