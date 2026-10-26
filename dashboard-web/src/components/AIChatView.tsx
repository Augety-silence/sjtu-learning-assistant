import { Activity, ArrowUp, PanelLeftOpen, PanelRightOpen } from "lucide-react";
import {
  AnimatePresence,
  motion,
  useIsPresent,
  useReducedMotion,
} from "motion/react";
import {
  type DragEvent as ReactDragEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import aiAgentLogo from "@/assets/ai-agent-logo.png";
import aiChatNewIllustration from "@/assets/empty-states/ai-chat-new.webp";
import { AIChatComposer } from "@/components/AIChatComposer";
import { AIChatMessageBubble } from "@/components/AIChatMessageBubble";
import { AIChatSidebar } from "@/components/AIChatSidebar";
import { AIChatTracePanel } from "@/components/AIChatTracePanel";
import { InlineAgentExecution } from "@/components/AIExecutionCard";
import { ContentReveal } from "@/components/ContentReveal";
import { Button } from "@/components/ui/Button";
import {
  AI_CHAT_STREAM_EVENT,
  createAiChatSession,
  deleteAiChatSession,
  getAiChatSession,
  getAiChatSessions,
  getAiPresets,
  getSettings,
  ingestAiAttachment,
  pickAiAttachment,
  resumeAiChatMessageTask,
  revealAiAttachment,
  sendAiChatMessage,
  updateSettings,
} from "@/lib/api";
import { motionDuration, motionEase } from "@/lib/motion";
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

const NATIVE_FILE_DROP_EVENT = "sjtu-native-file-drop";

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
  ai_auto_open_activity: false,
  ai_code_line_numbers: false,
};

interface PendingChatRequest {
  sessionId: string;
  text: string;
  optimisticId: string;
  attachments: AIManagedAttachment[];
  model: AIModel;
  depth: AIThinkingDepth;
  presetId: string;
}

interface FailedChatRequest {
  request: PendingChatRequest;
  message: string;
  retryable: boolean;
}

interface PersistedChatTask {
  taskId: string;
  request: PendingChatRequest;
}

const ACTIVE_CHAT_TASK_PREFIX = "ai-chat-running-task:";

function readPersistedChatTask(sessionId: string): PersistedChatTask | null {
  const key = `${ACTIVE_CHAT_TASK_PREFIX}${sessionId}`;
  const raw = window.localStorage.getItem(key);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as Partial<PersistedChatTask>;
    if (
      typeof parsed.taskId !== "string" ||
      !parsed.taskId ||
      !parsed.request ||
      parsed.request.sessionId !== sessionId
    ) {
      window.localStorage.removeItem(key);
      return null;
    }
    return parsed as PersistedChatTask;
  } catch {
    window.localStorage.removeItem(key);
    return null;
  }
}

function persistChatTask(taskId: string, request: PendingChatRequest) {
  window.localStorage.setItem(
    `${ACTIVE_CHAT_TASK_PREFIX}${request.sessionId}`,
    JSON.stringify({ taskId, request } satisfies PersistedChatTask),
  );
}

function clearPersistedChatTask(sessionId: string, taskId: string | null) {
  if (!taskId) return;
  const current = readPersistedChatTask(sessionId);
  if (current?.taskId === taskId) {
    window.localStorage.removeItem(`${ACTIVE_CHAT_TASK_PREFIX}${sessionId}`);
  }
}

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
  const [contextTitle, setContextTitle] = useState<string | null>(null);
  const [model, setModel] = useState<AIModel>("auto");
  const [depth, setDepth] = useState<AIThinkingDepth>("standard");
  const [busy, setBusy] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [runningSessionId, setRunningSessionId] = useState<string | null>(null);
  const [runningTaskId, setRunningTaskId] = useState<string | null>(null);
  const [attachmentBusy, setAttachmentBusy] = useState(false);
  const [attachments, setAttachments] = useState<AIManagedAttachment[]>([]);
  const [dragActive, setDragActive] = useState(false);
  const [preferences, setPreferences] =
    useState<AIChatPreferences>(defaultPreferences);
  const [settingsSaving, setSettingsSaving] = useState(false);
  const [settingsStatus, setSettingsStatus] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [canRetry, setCanRetry] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [motionMessageIds, setMotionMessageIds] = useState<string[]>([]);
  const [queuedMessageIds, setQueuedMessageIds] = useState<string[]>([]);
  const [activityOpen, setActivityOpen] = useState(() => false);
  const pendingQueueRef = useRef<PendingChatRequest[]>([]);
  // Failed requests whose session was no longer active; restored on reopen.
  const failedRequestsRef = useRef(new Map<string, FailedChatRequest[]>());
  const deletedSessionIdsRef = useRef(new Set<string>());
  const activeSessionIdRef = useRef<string | null>(null);
  activeSessionIdRef.current = active?.id ?? null;
  const pendingMessageCounterRef = useRef(0);
  const restoreComposerFocusRef = useRef(false);
  const isPresent = useIsPresent();
  const isPresentRef = useRef(isPresent);
  isPresentRef.current = isPresent;
  const shouldReduceMotion = useReducedMotion();
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const activityToggleRef = useRef<HTMLButtonElement>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const stickToBottomRef = useRef(true);
  const lastThreadScrollTopRef = useRef(0);
  const pendingComposerFocusRef = useRef(false);
  const taskAbortRef = useRef<AbortController | null>(null);
  const taskPollAbortRef = useRef<AbortController | null>(null);
  const restoredFailedRequestIdsRef = useRef(new Set<string>());
  const sessionLoadSequenceRef = useRef(0);
  const nativeDropTimerRef = useRef<number | null>(null);
  const nativeDropSeenRef = useRef(false);
  const sidebarAutoSubmitRef = useRef<string | null>(null);
  const focusIfPresent = useCallback((target: HTMLElement | null) => {
    if (
      !isPresentRef.current ||
      !target?.isConnected ||
      target.closest("[inert]")
    )
      return false;
    target.focus({ preventScroll: true });
    return true;
  }, []);
  useEffect(() => {
    isPresentRef.current = isPresent;
    return () => {
      isPresentRef.current = false;
    };
  }, [isPresent]);
  useEffect(
    () => () => {
      // Leaving the workspace must not cancel the in-flight Agent task: after
      // unmount there is no composer to restore the prompt or show the stop
      // notice, so aborting here would silently drop the user's question.
      // The backend task finishes and persists; only explicit "停止" cancels.
      pendingQueueRef.current.splice(0);
      // Stop only this component's poller. The backend task remains active and
      // the next AIChatView instance resumes it from the persisted task id.
      taskPollAbortRef.current?.abort();
    },
    [],
  );
  useEffect(() => {
    const pendingPrompt = window.localStorage.getItem("ai-chat-pending-prompt");
    const pendingContext = window.localStorage.getItem(
      "ai-chat-pending-context",
    );
    if (pendingContext) {
      window.localStorage.setItem("ai-chat-active-context", pendingContext);
      window.localStorage.removeItem("ai-chat-pending-context");
      try {
        const parsed = JSON.parse(pendingContext) as { title?: unknown };
        if (typeof parsed.title === "string") setContextTitle(parsed.title);
      } catch {
        window.localStorage.removeItem("ai-chat-active-context");
      }
    } else {
      window.localStorage.removeItem("ai-chat-active-context");
    }
    const autoSubmit =
      window.localStorage.getItem("ai-chat-auto-submit") === "true";
    window.localStorage.removeItem("ai-chat-auto-submit");
    if (pendingPrompt) {
      window.localStorage.removeItem("ai-chat-pending-prompt");
      setDraft(pendingPrompt);
      if (autoSubmit) sidebarAutoSubmitRef.current = pendingPrompt;
      else pendingComposerFocusRef.current = true;
    }
  }, []);
  useEffect(() => {
    const receive = (rawEvent: Event) => {
      const detail = (rawEvent as CustomEvent<Record<string, unknown>>).detail;
      if (!detail) return;
      const sessionId = detail.session_id;
      const taskId = detail.task_id;
      if (typeof sessionId !== "string" || typeof taskId !== "string") return;
      const streamId = `stream-${taskId}`;
      if (detail.type === "start") {
        setRunningTaskId(taskId);
        return;
      }
      if (
        detail.type === "tool" &&
        detail.run &&
        typeof detail.run === "object"
      ) {
        const run = detail.run as import("@/lib/types").AIToolRun;
        setActive((current) => {
          if (current?.id !== sessionId) return current;
          const messages = current.messages.filter(
            (message) => message.id !== streamId,
          );
          const existing = current.traces.findIndex(
            (trace) => trace.id === streamId,
          );
          const trace =
            existing >= 0
              ? current.traces[existing]
              : {
                  id: streamId,
                  preset_id: current.preset_id,
                  status: "running",
                  steps: 0,
                  tool_runs: [],
                };
          const traces = [...current.traces];
          const next = { ...trace, tool_runs: [...trace.tool_runs, run] };
          if (existing >= 0) traces[existing] = next;
          else traces.push(next);
          return { ...current, messages, traces };
        });
        return;
      }
      if (detail.type !== "content_delta" && detail.type !== "reasoning_delta")
        return;
      const delta = detail.delta;
      if (typeof delta !== "string" || !delta) return;
      setActive((current) => {
        if (current?.id !== sessionId) return current;
        const existing = current.messages.findIndex(
          (message) => message.id === streamId,
        );
        if (existing < 0) {
          const streamingMessage: AIChatMessage = {
            id: streamId,
            role: "assistant",
            content: detail.type === "content_delta" ? delta : "",
            reasoning_content: detail.type === "reasoning_delta" ? delta : null,
          };
          return {
            ...current,
            messages: [...current.messages, streamingMessage],
          };
        }
        const messages = [...current.messages];
        if (detail.type === "reasoning_delta") {
          messages[existing] = {
            ...messages[existing],
            reasoning_content:
              (messages[existing].reasoning_content ?? "") + delta,
          };
          return { ...current, messages };
        }
        messages[existing] = {
          ...messages[existing],
          content: messages[existing].content + delta,
        };
        return { ...current, messages };
      });
    };
    window.addEventListener(AI_CHAT_STREAM_EVENT, receive);
    return () => window.removeEventListener(AI_CHAT_STREAM_EVENT, receive);
  }, []);
  useEffect(() => {
    if (!isPresent || !pendingComposerFocusRef.current) return;
    const timer = window.setTimeout(() => {
      focusIfPresent(textareaRef.current);
      pendingComposerFocusRef.current = false;
    }, 60);
    return () => window.clearTimeout(timer);
  }, [isPresent, focusIfPresent]);
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

  const restoreFailedRequest = useCallback((failed: FailedChatRequest) => {
    const { request } = failed;
    const alreadyRestored = restoredFailedRequestIdsRef.current.has(
      request.optimisticId,
    );
    restoredFailedRequestIdsRef.current.add(request.optimisticId);
    if (!alreadyRestored) {
      setDraft((current) =>
        !current.trim() ? request.text : [current, request.text].join("\n\n"),
      );
    }
    setAttachments((current) => {
      const restored = [...current];
      for (const attachment of request.attachments) {
        if (!restored.some((item) => item.id === attachment.id)) {
          restored.push(attachment);
        }
      }
      return restored;
    });
    setCanRetry(failed.retryable);
    setError(failed.message);
  }, []);

  const applySession = useCallback(
    (session: AIChatSession) => {
      setMotionMessageIds([]);
      stickToBottomRef.current = true;
      activeSessionIdRef.current = session.id;
      setActive(session);
      setModel((session.model as AIModel) || "auto");
      setDepth(session.thinking_depth);
      setSelectedPresetId(session.preset_id);
      setAttachments([]);
      setCanRetry(false);
      const failed = failedRequestsRef.current.get(session.id);
      if (failed) {
        failedRequestsRef.current.delete(session.id);
        for (const item of failed) restoreFailedRequest(item);
      }
    },
    [restoreFailedRequest],
  );

  const openSession = useCallback(
    async (sessionId: string) => {
      const sequence = ++sessionLoadSequenceRef.current;
      setLoading(true);
      setError(null);
      try {
        const session = await getAiChatSession(sessionId);
        if (sequence !== sessionLoadSequenceRef.current) return;
        applySession(session);
        setHistoryOpen(false);
      } catch (reason) {
        if (sequence === sessionLoadSequenceRef.current)
          setError(reason instanceof Error ? reason.message : "对话读取失败");
      } finally {
        if (sequence === sessionLoadSequenceRef.current) setLoading(false);
      }
    },
    [applySession],
  );

  const newSession = useCallback(
    async (presetId = selectedPresetId) => {
      if (!presetId) return;
      const sequence = ++sessionLoadSequenceRef.current;
      setLoading(true);
      setError(null);
      try {
        const session = await createAiChatSession("auto", "standard", presetId);
        if (sequence !== sessionLoadSequenceRef.current) return;
        applySession(session);
        setHistoryOpen(false);
        await refreshSessions();
      } catch (reason) {
        if (sequence === sessionLoadSequenceRef.current)
          setError(reason instanceof Error ? reason.message : "无法创建新对话");
      } finally {
        if (sequence === sessionLoadSequenceRef.current) setLoading(false);
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
        const requestedSessionId = window.localStorage.getItem(
          "ai-chat-pending-session",
        );
        const forceNewSession =
          window.localStorage.getItem("ai-chat-new-session") === "true";
        window.localStorage.removeItem("ai-chat-pending-session");
        window.localStorage.removeItem("ai-chat-new-session");
        const fallbackSession = () =>
          sessionResult.items[0]
            ? getAiChatSession(sessionResult.items[0].id)
            : createAiChatSession("auto", "standard", defaultPresetId);
        let session: AIChatSession;
        let fallbackNotice: string | null = null;
        if (forceNewSession) {
          session = await createAiChatSession(
            "auto",
            "standard",
            defaultPresetId,
          );
        } else if (requestedSessionId) {
          try {
            session = await getAiChatSession(requestedSessionId);
          } catch {
            sidebarAutoSubmitRef.current = null;
            session = await fallbackSession();
            fallbackNotice =
              "指定的对话无法读取，已打开最近对话；请确认后再发送消息。";
          }
        } else {
          session = await fallbackSession();
        }
        if (cancelled) return;
        applySession(session);
        if (fallbackNotice) setError(fallbackNotice);
        if (forceNewSession || !sessionResult.items[0]) await refreshSessions();
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
    if (!isPresent) return;
    const handleWorkspaceResize = () => {
      if (window.innerWidth >= 1050) setHistoryOpen(false);
    };
    const handleEscape = (event: KeyboardEvent) => {
      // A nested popover (settings menu etc.) already handled this Escape.
      if (event.defaultPrevented) return;
      if (event.key === "Escape") {
        setHistoryOpen(false);
        if (activityOpen) {
          setActivityOpen(false);
          window.requestAnimationFrame(() =>
            focusIfPresent(activityToggleRef.current),
          );
        }
      }
    };
    window.addEventListener("resize", handleWorkspaceResize);
    window.addEventListener("keydown", handleEscape);
    return () => {
      window.removeEventListener("resize", handleWorkspaceResize);
      window.removeEventListener("keydown", handleEscape);
    };
  }, [activityOpen, isPresent, focusIfPresent]);

  useEffect(() => {
    if (!isPresent || !stickToBottomRef.current) return;
    endRef.current?.scrollIntoView?.({
      behavior: shouldReduceMotion ? "auto" : "smooth",
      block: "end",
    });
  }, [active?.messages, busy, shouldReduceMotion, isPresent]);

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

  const pickAttachment = async () => {
    if (attachmentBusy || attachments.length >= 20) return;
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
      window.setTimeout(() => focusIfPresent(textareaRef.current), 0);
    }
  };

  const ingestDroppedPaths = useCallback(
    async (paths: string[]) => {
      if (paths.length === 0) return;
      if (attachmentBusy) {
        setError("附件处理中，请稍候再拖入文件。");
        return;
      }
      const availableSlots = 20 - attachments.length;
      if (availableSlots <= 0) {
        setError("一次最多添加 20 个附件，请先移除部分附件。");
        return;
      }

      setAttachmentBusy(true);
      setError(null);
      const ingested: AIManagedAttachment[] = [];
      const failures: string[] = [];
      for (const path of paths.slice(0, availableSlots)) {
        try {
          ingested.push(await ingestAiAttachment(path as string));
        } catch (reason) {
          failures.push(
            `${path.split(/[\\/]/).pop()}: ${reason instanceof Error ? reason.message : "附件安全摄取失败"}`,
          );
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
      if (failures.length > 0) {
        setError(`部分附件未能添加：${failures.join("；")}`);
      } else if (paths.length > availableSlots) {
        setError(
          `只添加了前 ${availableSlots} 个文件；每段对话最多添加 20 个附件。`,
        );
      }
      setAttachmentBusy(false);
      window.setTimeout(() => focusIfPresent(textareaRef.current), 0);
    },
    [attachmentBusy, attachments.length, focusIfPresent],
  );

  useEffect(() => {
    const handleNativeDrop = (event: Event) => {
      const detail = (event as CustomEvent<unknown>).detail;
      if (
        typeof detail !== "object" ||
        detail === null ||
        !("paths" in detail) ||
        !Array.isArray(detail.paths) ||
        detail.paths.some((path) => typeof path !== "string")
      )
        return;
      nativeDropSeenRef.current = true;
      if (nativeDropTimerRef.current !== null) {
        window.clearTimeout(nativeDropTimerRef.current);
        nativeDropTimerRef.current = null;
      }
      void ingestDroppedPaths(detail.paths);
    };
    window.addEventListener(NATIVE_FILE_DROP_EVENT, handleNativeDrop);
    return () => {
      window.removeEventListener(NATIVE_FILE_DROP_EVENT, handleNativeDrop);
      if (nativeDropTimerRef.current !== null)
        window.clearTimeout(nativeDropTimerRef.current);
    };
  }, [ingestDroppedPaths]);

  const handleDrop = (event: ReactDragEvent<HTMLElement>) => {
    if (!isFileDrag(event)) return;
    event.preventDefault();
    dragDepthRef.current = 0;
    setDragActive(false);
    const paths = Array.from(event.dataTransfer.files).flatMap((file) => {
      const path = (file as File & { path?: string }).path;
      return path ? [path] : [];
    });
    // Native pywebview drops omit File.path in JavaScript. Its DOM bridge emits
    // NATIVE_FILE_DROP_EVENT with OS-validated paths immediately after this event.
    if (paths.length > 0) {
      void ingestDroppedPaths(paths);
    } else {
      nativeDropSeenRef.current = false;
      nativeDropTimerRef.current = window.setTimeout(() => {
        nativeDropTimerRef.current = null;
        if (!nativeDropSeenRef.current) {
          setError(
            "桌面文件路径尚未就绪；请使用“添加本地附件”选择文件，或稍后重试拖放。",
          );
        }
      }, 900);
    }
  };

  const revealAttachment = useCallback(async (attachmentId: number) => {
    try {
      await revealAiAttachment(attachmentId);
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "无法在文件夹中显示附件",
      );
    }
  }, []);

  const openActivity = useCallback(() => setActivityOpen(true), []);

  const cancelCurrentTask = useCallback(() => {
    const controller = taskAbortRef.current;
    if (controller && !controller.signal.aborted) {
      setStopping(true);
      controller.abort();
    }
    const queued = pendingQueueRef.current.splice(0);
    const queuedIds = new Set(queued.map((request) => request.optimisticId));
    setQueuedMessageIds((current) =>
      current.filter((id) => !queuedIds.has(id)),
    );
    setActive((current) =>
      current
        ? {
            ...current,
            messages: current.messages.filter(
              (message) => !queuedIds.has(message.id),
            ),
          }
        : current,
    );
  }, []);

  async function runSubmission(
    request: PendingChatRequest,
    resumeTaskId: string | null = null,
  ) {
    const attachmentIds = request.attachments.map(
      (attachment) => attachment.id,
    );
    setQueuedMessageIds((current) =>
      current.filter((id) => id !== request.optimisticId),
    );
    setBusy(true);
    setStopping(false);
    setRunningSessionId(request.sessionId);
    const controller = new AbortController();
    const pollController = new AbortController();
    taskAbortRef.current = controller;
    taskPollAbortRef.current = pollController;
    let trackedTaskId = resumeTaskId;
    setRunningTaskId(resumeTaskId);
    setCanRetry(false);
    if (resumeTaskId) {
      setActive((current) => {
        if (
          current?.id !== request.sessionId ||
          current.messages.some(
            (message) => message.id === request.optimisticId,
          )
        )
          return current;
        return {
          ...current,
          messages: [
            ...current.messages,
            {
              id: request.optimisticId,
              role: "user",
              content: request.text,
              attachments: request.attachments,
            },
          ],
        };
      });
    }
    try {
      const result = resumeTaskId
        ? await resumeAiChatMessageTask(request.sessionId, resumeTaskId, {
            signal: controller.signal,
            pollSignal: pollController.signal,
          })
        : attachmentIds.length > 0
          ? await sendAiChatMessage(
              request.sessionId,
              request.text,
              request.model,
              request.depth,
              request.presetId,
              attachmentIds,
              {
                signal: controller.signal,
                pollSignal: pollController.signal,
                onTaskStart: (taskId) => {
                  trackedTaskId = taskId;
                  setRunningTaskId(taskId);
                  persistChatTask(taskId, request);
                },
              },
            )
          : await sendAiChatMessage(
              request.sessionId,
              request.text,
              request.model,
              request.depth,
              request.presetId,
              undefined,
              {
                signal: controller.signal,
                pollSignal: pollController.signal,
                onTaskStart: (taskId) => {
                  trackedTaskId = taskId;
                  setRunningTaskId(taskId);
                  persistChatTask(taskId, request);
                },
              },
            );
      clearPersistedChatTask(request.sessionId, trackedTaskId);
      setMotionMessageIds([result.assistant_message.id]);
      setActive((current) => {
        if (current?.id !== request.sessionId) return current;
        const messages = current.messages.filter(
          (message) => !message.id.startsWith("stream-"),
        );
        const optimisticIndex = messages.findIndex(
          (item) => item.id === request.optimisticId,
        );
        if (optimisticIndex >= 0) {
          messages.splice(
            optimisticIndex,
            1,
            result.user_message,
            result.assistant_message,
          );
        } else {
          messages.push(result.user_message, result.assistant_message);
        }
        return {
          ...current,
          ...result.session,
          messages,
          traces: [
            ...current.traces.filter(
              (trace) => !trace.id.startsWith("stream-"),
            ),
            result.trace,
          ],
        };
      });
      await refreshSessions();
    } catch (reason) {
      const category =
        reason instanceof Error
          ? (reason as Error & { category?: string }).category
          : undefined;
      if (category === "detached") return;
      clearPersistedChatTask(request.sessionId, trackedTaskId);
      setMotionMessageIds([]);
      setActive((current) =>
        current?.id === request.sessionId
          ? {
              ...current,
              messages: current.messages.filter(
                (item) =>
                  item.id !== request.optimisticId &&
                  !item.id.startsWith("stream-"),
              ),
              traces: current.traces.filter(
                (item) => !item.id.startsWith("stream-"),
              ),
            }
          : current,
      );
      const failed: FailedChatRequest = {
        request,
        message:
          reason instanceof Error
            ? reason.message
            : "Agent 暂时无法完成检索，请稍后重试。",
        retryable:
          !(reason instanceof Error) ||
          (reason as Error & { retryable?: boolean }).retryable !== false,
      };
      if (activeSessionIdRef.current === request.sessionId) {
        restoreFailedRequest(failed);
      } else if (deletedSessionIdsRef.current.has(request.sessionId)) {
        // The original conversation was deleted: nothing to restore into.
        setError(failed.message);
      } else {
        // The user switched conversations meanwhile: keep the draft and
        // attachments with their own session instead of the visible composer,
        // so "重试本次" can never send them to the wrong conversation.
        const stash = failedRequestsRef.current.get(request.sessionId) ?? [];
        failedRequestsRef.current.set(request.sessionId, [...stash, failed]);
        setError(
          `另一段对话的消息发送失败（${failed.message}），草稿已保留在原对话中，切回后可重试。`,
        );
      }
    } finally {
      if (taskAbortRef.current === controller) taskAbortRef.current = null;
      if (taskPollAbortRef.current === pollController)
        taskPollAbortRef.current = null;
      setStopping(false);
      const next = pendingQueueRef.current.shift();
      if (next) {
        void runSubmission(next);
      } else {
        setBusy(false);
        setRunningSessionId(null);
        setRunningTaskId(null);
        window.setTimeout(() => focusIfPresent(textareaRef.current), 0);
      }
    }
  }

  useEffect(() => {
    if (loading || !active || busy) return;
    const persisted = readPersistedChatTask(active.id);
    if (!persisted) return;
    void runSubmission(persisted.request, persisted.taskId);
  }, [active?.id, loading]);

  const submit = (content: string) => {
    const text =
      content.trim() ||
      (attachments.length > 0 ? "请总结并分析这些附件。" : "");
    if (!text || attachmentBusy || !active || !selectedPresetId) return;
    if (!busy && readPersistedChatTask(active.id)) {
      setError("这段对话仍有任务在运行，正在重新连接，请稍候。");
      return;
    }
    pendingMessageCounterRef.current += 1;
    const optimisticId = `pending-${Date.now()}-${pendingMessageCounterRef.current}`;
    const selectedAttachments = attachments;
    const optimistic: AIChatMessage = {
      id: optimisticId,
      role: "user",
      content: text,
      attachments: selectedAttachments,
    };
    const request: PendingChatRequest = {
      sessionId: active.id,
      text,
      optimisticId,
      attachments: selectedAttachments,
      model,
      depth,
      presetId: selectedPresetId,
    };

    stickToBottomRef.current = true;
    setMotionMessageIds([optimisticId]);
    setActive((current) =>
      current?.id === request.sessionId
        ? { ...current, messages: [...current.messages, optimistic] }
        : current,
    );
    setDraft("");
    setAttachments([]);
    setError(null);
    setCanRetry(false);

    if (busy) {
      pendingQueueRef.current.push(request);
      setQueuedMessageIds((current) => [...current, optimisticId]);
      window.setTimeout(() => focusIfPresent(textareaRef.current), 0);
      return;
    }
    void runSubmission(request);
  };

  useEffect(() => {
    const prompt = sidebarAutoSubmitRef.current;
    if (!prompt || loading || !active || !selectedPresetId) return;
    sidebarAutoSubmitRef.current = null;
    submit(prompt);
  }, [active, loading, selectedPresetId, submit]);

  const toggleActivity = () => {
    const input = textareaRef.current;
    const selectionStart = input?.selectionStart ?? null;
    const selectionEnd = input?.selectionEnd ?? null;
    setActivityOpen((open) => !open);
    if (restoreComposerFocusRef.current) {
      window.requestAnimationFrame(() => {
        if (
          focusIfPresent(input) &&
          selectionStart !== null &&
          selectionEnd !== null
        ) {
          input?.setSelectionRange(selectionStart, selectionEnd);
        }
      });
    }
  };

  const closeActivity = () => {
    setActivityOpen(false);
    window.requestAnimationFrame(() =>
      focusIfPresent(activityToggleRef.current),
    );
  };

  const removeSession = async (sessionId: string) => {
    if (!window.confirm("确定删除这段历史对话？")) return;
    try {
      await deleteAiChatSession(sessionId);
      deletedSessionIdsRef.current.add(sessionId);
      failedRequestsRef.current.delete(sessionId);
      const removed = pendingQueueRef.current.filter(
        (request) => request.sessionId === sessionId,
      );
      pendingQueueRef.current = pendingQueueRef.current.filter(
        (request) => request.sessionId !== sessionId,
      );
      const removedIds = new Set(
        removed.map((request) => request.optimisticId),
      );
      setQueuedMessageIds((current) =>
        current.filter((id) => !removedIds.has(id)),
      );
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
  const visibleBusy = busy && active?.id === runningSessionId;
  const hasVisibleStream = Boolean(
    active?.messages.some(
      (message) =>
        message.id.startsWith("stream-") && Boolean(message.content.trim()),
    ),
  );
  // Streaming deltas are kept quiet by aria-live="off" on the stream bubble
  // (AIChatMessageBubble). aria-busy on this live region is avoided on purpose:
  // NVDA/JAWS on Chromium never announce the region after busy clears.
  const tracesById = useMemo(
    () => new Map(active?.traces.map((trace) => [trace.id, trace]) ?? []),
    [active?.traces],
  );

  return (
    <ContentReveal
      as="main"
      sequence="manual"
      className="ai-workspace"
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
              transition={{ duration: motionDuration.control }}
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
          <header
            className="ai-conversation-header"
            data-page-reveal-part="heading"
          >
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
              ref={activityToggleRef}
              variant={activityOpen ? "outline" : "ghost"}
              size="sm"
              className="ai-activity-toggle"
              aria-expanded={activityOpen}
              aria-controls="ai-activity-panel"
              aria-label="切换 Activity 检索轨迹"
              onPointerDown={() => {
                restoreComposerFocusRef.current =
                  document.activeElement === textareaRef.current;
              }}
              onClick={toggleActivity}
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
            <div className="ai-file-drop-overlay" role="status">
              <div>
                <strong>将文件拖拽到这里</strong>
                <span>
                  支持 TXT、Markdown、CSV / JSON、常见代码文件与图片；2GB
                  是摄取上限，附件摘要和可分析内容受限
                </span>
              </div>
            </div>
          )}

          <ContentReveal
            variant="detail"
            ready={!loading}
            className="ai-chat-thread"
            data-page-reveal-part="thread"
            role="log"
            aria-live="polite"
            onScroll={(event) => {
              const thread = event.currentTarget;
              const nearBottom =
                thread.scrollHeight - thread.scrollTop - thread.clientHeight <
                48;
              if (nearBottom) stickToBottomRef.current = true;
              else if (thread.scrollTop < lastThreadScrollTopRef.current - 2)
                stickToBottomRef.current = false;
              lastThreadScrollTopRef.current = thread.scrollTop;
            }}
          >
            {loading ? (
              <motion.div
                className="ai-thinking ai-initial-loading"
                role="status"
                initial={shouldReduceMotion ? false : { opacity: 0.72 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: motionDuration.enter }}
              >
                <span aria-hidden="true" />
                正在准备本地学习 Agent…
              </motion.div>
            ) : !hasMessages ? (
              <div className="ai-chat-empty">
                <img
                  className="ai-chat-empty-illustration"
                  src={aiChatNewIllustration}
                  alt=""
                  aria-hidden="true"
                />
                <h1>今天想从学习数据里查什么？</h1>
                <p>
                  {selectedPreset?.name ?? "学习 Agent"}
                  会按需调用只读工具，查询课程、课程文件、截止日期、消息和资料树；每一步都可在
                  Activity 中核验。
                </p>
                {contextTitle && (
                  <span className="ai-context-chip">来自：{contextTitle}</span>
                )}
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
                {active?.messages.map((message) => {
                  const trace = message.trace_id
                    ? tracesById.get(message.trace_id)
                    : null;
                  return (
                    <AIChatMessageBubble
                      key={message.id}
                      message={message}
                      trace={trace}
                      agentName={selectedPreset?.name ?? "学习 Agent"}
                      onOpenActivity={openActivity}
                      onRevealAttachment={revealAttachment}
                      showCodeLineNumbers={preferences.ai_code_line_numbers}
                      animateEntry={motionMessageIds.includes(message.id)}
                      queued={queuedMessageIds.includes(message.id)}
                    />
                  );
                })}
                <AnimatePresence>
                  {visibleBusy && !hasVisibleStream && (
                    <motion.div
                      className="ai-inline-execution"
                      initial={
                        shouldReduceMotion ? false : { opacity: 0.72, y: 3 }
                      }
                      animate={{ opacity: 1, y: 0 }}
                      exit={{ opacity: 0, y: 2 }}
                      transition={{
                        duration: motionDuration.control,
                        ease: motionEase.out,
                      }}
                    >
                      <InlineAgentExecution
                        running
                        agentName={selectedPreset?.name ?? "学习 Agent"}
                        onOpenActivity={openActivity}
                      />
                    </motion.div>
                  )}
                </AnimatePresence>
                <div ref={endRef} />
              </div>
            )}
          </ContentReveal>

          <AIChatComposer
            draft={draft}
            error={error}
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
            busy={busy}
            stopping={stopping}
            onCancel={cancelCurrentTask}
            retryDisabled={busy}
            onRetry={
              canRetry && draft.trim() && !attachmentBusy
                ? () => submit(draft)
                : undefined
            }
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
          aria-valuemin={300}
          aria-valuemax={340}
          aria-valuenow={rightWidth}
          tabIndex={activityOpen ? 0 : -1}
          onPointerDown={(event) => beginResize("right", event)}
          onKeyDown={(event) => resizeWithKeyboard("right", event)}
        >
          <span aria-hidden="true" />
        </div>

        <AIChatTracePanel
          open={activityOpen}
          onClose={closeActivity}
          preset={selectedPreset}
          traces={active?.traces ?? []}
          busy={visibleBusy}
          runningTaskId={visibleBusy ? runningTaskId : null}
        />
      </div>
    </ContentReveal>
  );
}
