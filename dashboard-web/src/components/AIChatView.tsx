import { Activity, ArrowUp, PanelLeftOpen, PanelRightOpen } from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import {
  type DragEvent as ReactDragEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import aiAgentLogo from "@/assets/ai-agent-logo.png";
import { AIChatComposer } from "@/components/AIChatComposer";
import { AIChatMessageBubble } from "@/components/AIChatMessageBubble";
import { AIChatSidebar } from "@/components/AIChatSidebar";
import { AIChatTracePanel } from "@/components/AIChatTracePanel";
import { Button } from "@/components/ui/Button";
import {
  createAiChatSession,
  deleteAiChatSession,
  getAiChatSession,
  getAiChatSessions,
  getAiPresets,
  getSettings,
  ingestAiAttachment,
  pickAiAttachment,
  revealAiAttachment,
  sendAiChatMessage,
  updateSettings,
} from "@/lib/api";
import type {
  AIAgentPreset,
  AIChatMessage,
  AIChatPreferences,
  AIChatSession,
  AIChatSessionSummary,
  AIManagedAttachment,
  AIModel,
  AIThinkingDepth,
} from "@/lib/types";
import { useResizablePanes } from "@/lib/useResizablePanes";

const starters = [
  "列出本周所有课程的截止事项，并按紧急程度排序",
  "搜索最近的课程消息，告诉我有哪些需要处理",
  "梳理每门课程的文件，并给出本周复习建议",
];
const models: Array<{ value: AIModel; label: string; detail: string }> = [
  { value: "auto", label: "自动", detail: "按任务自动匹配模型" },
  { value: "deepseek-chat", label: "DeepSeek Flash", detail: "快速通用对话" },
  {
    value: "deepseek-reasoner",
    label: "DeepSeek Reasoner",
    detail: "复杂推理任务",
  },
  { value: "minimax-m2.7", label: "MiniMax M2.7", detail: "长文本理解" },
  { value: "qwen3.8-27b", label: "Qwen 3.8", detail: "均衡通用能力" },
];
const depths: Array<{
  value: AIThinkingDepth;
  label: string;
  detail: string;
}> = [
  { value: "quick", label: "快速", detail: "更快响应" },
  { value: "standard", label: "标准", detail: "速度与质量平衡" },
  { value: "deep", label: "深度", detail: "更多推理步骤" },
];

const defaultPreferences: AIChatPreferences = {
  ai_chat_send_shortcut: "enter",
  ai_reply_language: "auto",
  ai_attachment_context_budget: "balanced",
  ai_auto_open_activity: true,
  ai_code_line_numbers: false,
};

function chatPreferences(settings: AIChatPreferences): AIChatPreferences {
  return {
    ai_chat_send_shortcut: settings.ai_chat_send_shortcut,
    ai_reply_language: settings.ai_reply_language,
    ai_attachment_context_budget: settings.ai_attachment_context_budget,
    ai_auto_open_activity: settings.ai_auto_open_activity,
    ai_code_line_numbers: settings.ai_code_line_numbers,
  };
}

export function AIChatView({ onBack }: { onBack: () => void }) {
  const [presets, setPresets] = useState<AIAgentPreset[]>([]);
  const [selectedPresetId, setSelectedPresetId] = useState("");
  const [sessions, setSessions] = useState<AIChatSessionSummary[]>([]);
  const [active, setActive] = useState<AIChatSession | null>(null);
  const [draft, setDraft] = useState("");
  const [model, setModel] = useState<AIModel>("auto");
  const [depth, setDepth] = useState<AIThinkingDepth>("standard");
  const [busy, setBusy] = useState(false);
  const [attachmentBusy, setAttachmentBusy] = useState(false);
  const [attachments, setAttachments] = useState<AIManagedAttachment[]>([]);
  const [dragActive, setDragActive] = useState(false);
  const [preferences, setPreferences] =
    useState<AIChatPreferences>(defaultPreferences);
  const [settingsSaving, setSettingsSaving] = useState(false);
  const [settingsStatus, setSettingsStatus] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [motionMessageIds, setMotionMessageIds] = useState<string[]>([]);
  const [activityOpen, setActivityOpen] = useState(() =>
    typeof window === "undefined" ? true : window.innerWidth > 1180,
  );
  const wideActivityRef = useRef(
    typeof window === "undefined" ? true : window.innerWidth > 1180,
  );
  const shouldReduceMotion = useReducedMotion();
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const dragDepthRef = useRef(0);
  const {
    leftWidth,
    rightWidth,
    style: paneStyle,
    beginResize,
    resizeWithKeyboard,
  } = useResizablePanes();

  const refreshSessions = useCallback(async () => {
    const result = await getAiChatSessions();
    setSessions(result.items);
    return result.items;
  }, []);

  const applySession = useCallback((session: AIChatSession) => {
    setMotionMessageIds([]);
    setActive(session);
    setModel((session.model as AIModel) || "auto");
    setDepth(session.thinking_depth);
    setSelectedPresetId(session.preset_id);
    setAttachments([]);
  }, []);

  const openSession = useCallback(
    async (sessionId: string) => {
      setLoading(true);
      setError(null);
      try {
        applySession(await getAiChatSession(sessionId));
        setHistoryOpen(false);
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "对话读取失败");
      } finally {
        setLoading(false);
      }
    },
    [applySession],
  );

  const newSession = useCallback(
    async (presetId = selectedPresetId) => {
      if (!presetId) return;
      setLoading(true);
      setError(null);
      try {
        const session = await createAiChatSession("auto", "standard", presetId);
        applySession(session);
        setHistoryOpen(false);
        await refreshSessions();
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "无法创建新对话");
      } finally {
        setLoading(false);
      }
    },
    [applySession, refreshSessions, selectedPresetId],
  );

  useEffect(() => {
    let cancelled = false;
    const initialLoad = async () => {
      try {
        const [presetResult, sessionResult] = await Promise.all([
          getAiPresets(),
          getAiChatSessions(),
        ]);
        if (cancelled) return;
        setPresets(presetResult.items);
        setSessions(sessionResult.items);
        const defaultPresetId =
          presetResult.default_preset_id || presetResult.items[0]?.id || "";
        setSelectedPresetId(defaultPresetId);
        if (!defaultPresetId) throw new Error("没有可用的 Agent 预设。");
        const session = sessionResult.items[0]
          ? await getAiChatSession(sessionResult.items[0].id)
          : await createAiChatSession("auto", "standard", defaultPresetId);
        if (cancelled) return;
        applySession(session);
        if (!sessionResult.items[0]) await refreshSessions();
      } catch (reason) {
        if (!cancelled) {
          setError(
            reason instanceof Error ? reason.message : "AI 工作区加载失败",
          );
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void initialLoad();
    return () => {
      cancelled = true;
    };
  }, [applySession, refreshSessions]);

  useEffect(() => {
    let cancelled = false;
    void getSettings()
      .then((settings) => {
        if (!cancelled) setPreferences(chatPreferences(settings));
      })
      .catch((reason) => {
        if (!cancelled) {
          setSettingsStatus(
            reason instanceof Error
              ? `设置读取失败：${reason.message}`
              : "设置读取失败，正在使用默认值。",
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const handleWorkspaceResize = () => {
      const isWide = window.innerWidth > 1180;
      if (isWide !== wideActivityRef.current) {
        wideActivityRef.current = isWide;
        setActivityOpen(isWide);
      }
      if (window.innerWidth >= 800) setHistoryOpen(false);
    };
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setHistoryOpen(false);
        if (window.innerWidth <= 1180) setActivityOpen(false);
      }
    };
    window.addEventListener("resize", handleWorkspaceResize);
    window.addEventListener("keydown", handleEscape);
    return () => {
      window.removeEventListener("resize", handleWorkspaceResize);
      window.removeEventListener("keydown", handleEscape);
    };
  }, []);

  useEffect(() => {
    endRef.current?.scrollIntoView?.({
      behavior: shouldReduceMotion ? "auto" : "smooth",
      block: "end",
    });
  }, [active?.messages, busy, shouldReduceMotion]);

  const updatePreference = async (change: Partial<AIChatPreferences>) => {
    if (settingsSaving) return;
    const previous = preferences;
    setPreferences((current) => ({ ...current, ...change }));
    setSettingsSaving(true);
    setSettingsStatus(null);
    try {
      const settings = await updateSettings(change);
      setPreferences(chatPreferences(settings));
      setSettingsStatus("已保存");
    } catch (reason) {
      setPreferences(previous);
      setSettingsStatus(
        reason instanceof Error
          ? `保存失败：${reason.message}`
          : "保存失败，请重试。",
      );
    } finally {
      setSettingsSaving(false);
    }
  };

  const isFileDrag = (event: ReactDragEvent) =>
    Array.from(event.dataTransfer.types).includes("Files");

  const handleDragEnter = (event: ReactDragEvent<HTMLElement>) => {
    if (!isFileDrag(event)) return;
    event.preventDefault();
    dragDepthRef.current += 1;
    setDragActive(true);
  };

  const handleDragOver = (event: ReactDragEvent<HTMLElement>) => {
    if (!isFileDrag(event)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
  };

  const handleDragLeave = (event: ReactDragEvent<HTMLElement>) => {
    if (!isFileDrag(event)) return;
    event.preventDefault();
    dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
    if (dragDepthRef.current === 0) setDragActive(false);
  };

  const ingestDroppedFiles = async (files: File[]) => {
    if (busy || attachmentBusy || files.length === 0) return;
    const availableSlots = 20 - attachments.length;
    if (availableSlots <= 0) {
      setError("一次最多添加 20 个附件，请先移除部分附件。");
      return;
    }
    const selectedFiles = files.slice(0, availableSlots);
    const paths = selectedFiles.map(
      (file) => (file as File & { path?: string }).path,
    );
    if (paths.some((path) => !path)) {
      setError(
        "当前 WebView 无法取得拖入文件的位置，请使用回形针按钮选择文件。",
      );
      return;
    }

    setAttachmentBusy(true);
    setError(null);
    const ingested: AIManagedAttachment[] = [];
    let failure: string | null = null;
    for (const path of paths) {
      try {
        ingested.push(await ingestAiAttachment(path as string));
      } catch (reason) {
        failure = reason instanceof Error ? reason.message : "附件安全摄取失败";
      }
    }
    if (ingested.length > 0) {
      setAttachments((current) => {
        const next = [...current];
        for (const attachment of ingested) {
          if (!next.some((item) => item.id === attachment.id))
            next.push(attachment);
        }
        return next.slice(0, 20);
      });
    }
    if (failure) setError(`部分附件未能添加：${failure}`);
    setAttachmentBusy(false);
    window.setTimeout(() => textareaRef.current?.focus(), 0);
  };

  const handleDrop = (event: ReactDragEvent<HTMLElement>) => {
    if (!isFileDrag(event)) return;
    event.preventDefault();
    dragDepthRef.current = 0;
    setDragActive(false);
    void ingestDroppedFiles(Array.from(event.dataTransfer.files));
  };

  const pickAttachment = async () => {
    if (busy || attachmentBusy || attachments.length >= 20) return;
    setAttachmentBusy(true);
    setError(null);
    try {
      const result = await pickAiAttachment();
      if (!result.cancelled && result.attachment) {
        setAttachments((current) =>
          current.some((item) => item.id === result.attachment?.id)
            ? current
            : [...current, result.attachment as AIManagedAttachment],
        );
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "附件上传或解析失败");
    } finally {
      setAttachmentBusy(false);
      window.setTimeout(() => textareaRef.current?.focus(), 0);
    }
  };

  const revealAttachment = async (attachmentId: number) => {
    try {
      await revealAiAttachment(attachmentId);
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "无法在 Finder 中显示附件",
      );
    }
  };

  const submit = async (content: string) => {
    const text =
      content.trim() ||
      (attachments.length > 0 ? "请总结并分析这些附件。" : "");
    if (!text || busy || attachmentBusy || !active || !selectedPresetId) return;
    if (preferences.ai_auto_open_activity) setActivityOpen(true);
    const sessionId = active.id;
    const selectedAttachments = attachments;
    const attachmentIds = selectedAttachments.map(
      (attachment) => attachment.id,
    );
    const optimistic: AIChatMessage = {
      id: `pending-${Date.now()}`,
      role: "user",
      content: text,
      attachments: selectedAttachments,
    };
    setMotionMessageIds([optimistic.id]);
    setActive((current) =>
      current?.id === sessionId
        ? { ...current, messages: [...current.messages, optimistic] }
        : current,
    );
    setDraft("");
    setError(null);
    setBusy(true);
    try {
      const result =
        attachmentIds.length > 0
          ? await sendAiChatMessage(
              sessionId,
              text,
              model,
              depth,
              selectedPresetId,
              attachmentIds,
            )
          : await sendAiChatMessage(
              sessionId,
              text,
              model,
              depth,
              selectedPresetId,
            );
      setMotionMessageIds([result.assistant_message.id]);
      setActive((current) =>
        current?.id === sessionId
          ? {
              ...current,
              ...result.session,
              messages: [
                ...current.messages.filter((item) => item.id !== optimistic.id),
                result.user_message,
                result.assistant_message,
              ],
              traces: [...current.traces, result.trace],
            }
          : current,
      );
      setAttachments([]);
      await refreshSessions();
    } catch (reason) {
      setMotionMessageIds([]);
      setActive((current) =>
        current?.id === sessionId
          ? {
              ...current,
              messages: current.messages.filter(
                (item) => item.id !== optimistic.id,
              ),
            }
          : current,
      );
      setDraft(text);
      setError(
        reason instanceof Error
          ? reason.message
          : "Agent 暂时无法完成检索，请稍后重试。",
      );
    } finally {
      setBusy(false);
      window.setTimeout(() => textareaRef.current?.focus(), 0);
    }
  };

  const removeSession = async (sessionId: string) => {
    if (!window.confirm("确定删除这段历史对话？")) return;
    try {
      await deleteAiChatSession(sessionId);
      const remaining = await refreshSessions();
      if (sessionId !== active?.id) return;
      if (remaining[0]) await openSession(remaining[0].id);
      else await newSession();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "删除对话失败");
    }
  };

  const selectedPreset =
    presets.find((preset) => preset.id === selectedPresetId) ?? null;
  const hasMessages = Boolean(active?.messages.length);

  return (
    <motion.main
      className="ai-workspace"
      initial={{ opacity: 0.01 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
      onDragEnter={handleDragEnter}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      <div
        style={paneStyle}
        className={
          activityOpen
            ? "ai-workspace-body"
            : "ai-workspace-body activity-closed"
        }
      >
        <AIChatSidebar
          open={historyOpen}
          onClose={() => setHistoryOpen(false)}
          onBack={onBack}
          onNew={() => void newSession()}
          presets={presets}
          selectedPresetId={selectedPresetId}
          onSelectPreset={setSelectedPresetId}
          sessions={sessions}
          activeSessionId={active?.id}
          onOpenSession={(id) => void openSession(id)}
          onDeleteSession={(id) => void removeSession(id)}
          busy={busy}
          preferences={preferences}
          settingsSaving={settingsSaving}
          settingsStatus={settingsStatus}
          onPreferenceChange={(change) => void updatePreference(change)}
        />
        <div
          className="ai-pane-resizer ai-pane-resizer-left"
          role="separator"
          aria-label="调整历史会话栏宽度"
          aria-orientation="vertical"
          aria-valuemin={208}
          aria-valuemax={320}
          aria-valuenow={leftWidth}
          tabIndex={0}
          onPointerDown={(event) => beginResize("left", event)}
          onKeyDown={(event) => resizeWithKeyboard("left", event)}
        >
          <span aria-hidden="true" />
        </div>
        <AnimatePresence>
          {historyOpen && (
            <motion.button
              type="button"
              className="ai-drawer-backdrop ai-sidebar-backdrop"
              aria-label="关闭对话导航"
              tabIndex={-1}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.16 }}
              onClick={() => setHistoryOpen(false)}
            />
          )}
        </AnimatePresence>

        <section
          className={
            hasMessages
              ? "ai-conversation has-messages"
              : "ai-conversation is-empty"
          }
          aria-label="AI Agent 对话"
        >
          <header className="ai-conversation-header">
            <Button
              variant="ghost"
              size="icon"
              className="ai-panel-toggle ai-left-toggle"
              aria-label="打开对话导航"
              onClick={() => setHistoryOpen(true)}
            >
              <PanelLeftOpen />
            </Button>
            <div className="ai-conversation-heading">
              <img src={aiAgentLogo} alt="" aria-hidden="true" />
              <span>
                <span className="ai-header-agent">
                  {selectedPreset?.name ?? "学习 Agent"}
                </span>
                <strong>{active?.title ?? "新对话"}</strong>
              </span>
            </div>
            <Button
              variant={activityOpen ? "outline" : "ghost"}
              size="sm"
              aria-expanded={activityOpen}
              aria-label="切换 Activity 检索轨迹"
              onClick={() => setActivityOpen((open) => !open)}
            >
              {activityOpen ? (
                <Activity aria-hidden="true" />
              ) : (
                <PanelRightOpen aria-hidden="true" />
              )}
              Activity
            </Button>
          </header>

          {dragActive && (
            <div
              className="ai-file-drop-overlay"
              role="status"
              aria-live="assertive"
            >
              <div>
                <strong>将文件拖拽到这里</strong>
                <span>
                  支持 TXT、Markdown、CSV / JSON 与常见代码文件 · 单文件最大 2GB
                </span>
              </div>
            </div>
          )}

          <div className="ai-chat-thread" aria-live="polite">
            {loading ? (
              <motion.div
                className="ai-thinking ai-initial-loading"
                role="status"
                initial={{ opacity: 0.01 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.18 }}
              >
                <span aria-hidden="true" />
                正在准备本地学习 Agent…
              </motion.div>
            ) : !hasMessages ? (
              <div className="ai-chat-empty">
                <span className="ai-chat-mark">
                  <img src={aiAgentLogo} alt="学习 Agent" />
                </span>
                <h1>今天想从学习数据里查什么？</h1>
                <p>
                  {selectedPreset?.name ?? "学习 Agent"}
                  会按需调用只读工具，查询课程、课程文件、截止日期、消息和资料树；每一步都可在
                  Activity 中核验。
                </p>
                <div className="ai-chat-starters">
                  {starters.map((starter) => (
                    <button
                      key={starter}
                      type="button"
                      onClick={() => void submit(starter)}
                    >
                      <span>{starter}</span>
                      <ArrowUp aria-hidden="true" />
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <div className="ai-chat-messages">
                {active?.messages.map((message) => (
                  <AIChatMessageBubble
                    key={message.id}
                    message={message}
                    onOpenActivity={() => setActivityOpen(true)}
                    onRevealAttachment={(id) => void revealAttachment(id)}
                    showCodeLineNumbers={preferences.ai_code_line_numbers}
                    animateEntry={motionMessageIds.includes(message.id)}
                  />
                ))}
                <AnimatePresence>
                  {busy && (
                    <motion.div
                      className="ai-thinking"
                      role="status"
                      initial={{ opacity: 0.01, y: 3 }}
                      animate={{ opacity: 1, y: 0 }}
                      exit={{ opacity: 0, y: 2 }}
                      transition={{ duration: 0.14, ease: [0.16, 1, 0.3, 1] }}
                    >
                      <span aria-hidden="true" />
                      Agent 正在检索本地学习数据…
                    </motion.div>
                  )}
                </AnimatePresence>
                <div ref={endRef} />
              </div>
            )}
          </div>

          <AIChatComposer
            draft={draft}
            error={error}
            busy={busy}
            attachmentBusy={attachmentBusy}
            attachments={attachments}
            active={Boolean(active)}
            presets={presets}
            selectedPresetId={selectedPresetId}
            model={model}
            depth={depth}
            models={models}
            depths={depths}
            hasMessages={hasMessages}
            sendShortcut={preferences.ai_chat_send_shortcut}
            textareaRef={textareaRef}
            onDraftChange={setDraft}
            onSelectPreset={setSelectedPresetId}
            onModelChange={setModel}
            onDepthChange={setDepth}
            onPickAttachment={() => void pickAttachment()}
            onRemoveAttachment={(id) =>
              setAttachments((current) =>
                current.filter((attachment) => attachment.id !== id),
              )
            }
            onSubmit={(value) => void submit(value)}
          />
        </section>

        <div
          className={
            activityOpen
              ? "ai-pane-resizer ai-pane-resizer-right"
              : "ai-pane-resizer ai-pane-resizer-right is-hidden"
          }
          role="separator"
          aria-label="调整 Activity 栏宽度"
          aria-orientation="vertical"
          aria-valuemin={260}
          aria-valuemax={420}
          aria-valuenow={rightWidth}
          tabIndex={activityOpen ? 0 : -1}
          onPointerDown={(event) => beginResize("right", event)}
          onKeyDown={(event) => resizeWithKeyboard("right", event)}
        >
          <span aria-hidden="true" />
        </div>

        <AnimatePresence>
          {activityOpen && (
            <motion.button
              type="button"
              className="ai-drawer-backdrop ai-activity-backdrop"
              aria-label="关闭 Activity"
              tabIndex={-1}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.16 }}
              onClick={() => setActivityOpen(false)}
            />
          )}
        </AnimatePresence>
        <AIChatTracePanel
          open={activityOpen}
          onClose={() => setActivityOpen(false)}
          preset={selectedPreset}
          traces={active?.traces ?? []}
          busy={busy}
        />
      </div>
    </motion.main>
  );
}
