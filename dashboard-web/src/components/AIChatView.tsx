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
  useContext,
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
import { AISkillManager } from "@/components/AISkillManager";
import {
  AIWorkspaceNavigation,
  type AIWorkspacePage,
} from "@/components/AIWorkspaceNavigation";
import { AIWorkspacePanel } from "@/components/AIWorkspacePanel";
import { ContentReveal } from "@/components/ContentReveal";
import { UIReleaseToggle } from "@/components/UIReleaseToggle";
import { Button } from "@/components/ui/Button";
import { AIComposerDraftContext } from "@/lib/aiDrafts";
import {
  AI_CHAT_STREAM_EVENT,
  clearAiWorkspace,
  createAiChatSession,
  deleteAiChatSession,
  getAiChatSession,
  getAiChatSessions,
  getAiPresets,
  getAiWorkspaceStatus,
  getSettings,
  ingestAiAttachment,
  pickAiAttachment,
  pickAiWorkspace,
  resumeAiChatMessageTask,
  revealAiAttachment,
  sendAiChatMessage,
  steerAiChatTask,
  updateSettings,
} from "@/lib/api";
import { motionDuration, motionEase } from "@/lib/motion";
import type {
  AIAgentPreset,
  AIChatMessage,
  AIChatPreferences,
  AIChatSession,
  AIChatSessionSummary,
  AIExtensionSkill,
  AIManagedAttachment,
  AIModel,
  AIThinkingDepth,
  AIWorkspaceStatus,
} from "@/lib/types";
import { useUIRelease } from "@/lib/uiRelease";
import { useChatMeasure } from "@/lib/useChatMeasure";
import { useChatSessionTasks } from "@/lib/useChatSessionTasks";
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
  skillIds?: string[];
  steeringTaskId?: string;
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
const RESUME_RETRY_NOTICE = "暂时无法确认后台任务状态，正在自动重新连接…";
const PENDING_STEERING_PREFIX = "ai-chat-pending-steering:";
const STEERING_RETRY_NOTICE =
  "补充指令暂未确认，已保留；重新连接后将按原标识重试。";

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
  const uiRelease = useUIRelease();
  const [configPage, setConfigPage] = useState<AIWorkspacePage | null>(null);
  const { conversationRef, readingStyle } = useChatMeasure(
    uiRelease === "2",
    !configPage,
  );
  useEffect(() => {
    if (uiRelease !== "2") setConfigPage(null);
  }, [uiRelease]);
  const inheritedDrafts = useContext(AIComposerDraftContext);
  const [presets, setPresets] = useState<AIAgentPreset[]>([]);
  const [skills, setSkills] = useState<AIExtensionSkill[]>([]);
  const [selectedSkills, setSelectedSkills] = useState<string[]>([]);
  const skillsBySession = useRef(new Map<string, string[]>());
  const changeSelectedSkills = (ids: string[]) => {
    setSelectedSkills(ids);
    if (activeSessionIdRef.current) {
      skillsBySession.current.set(activeSessionIdRef.current, ids);
      window.localStorage.setItem(
        `ai-chat-skills:${activeSessionIdRef.current}`,
        JSON.stringify(ids),
      );
    }
  };
  const [selectedPresetId, setSelectedPresetId] = useState("");
  const [sessions, setSessions] = useState<AIChatSessionSummary[]>([]);
  const [active, setActive] = useState<AIChatSession | null>(null);
  const [draft, setDraft] = useState("");
  const [contextTitle, setContextTitle] = useState<string | null>(null);
  const [model, setModel] = useState<AIModel>("auto");
  const [depth, setDepth] = useState<AIThinkingDepth>("standard");
  const sessionTasks = useChatSessionTasks();
  const activeTask = active ? sessionTasks.tasks.current.get(active.id) : null;
  const busy = Boolean(activeTask);
  const stopping = activeTask?.stopping ?? false;
  const runningTaskId = activeTask?.taskId ?? null;
  const runningStartedAt = activeTask?.startedAt ?? null;
  const liveStatus = activeTask?.liveStatus ?? null;
  // 自定义技能管理与 AI 工作区。
  const [skillManager, setSkillManager] = useState<{
    skillId: string | null;
  } | null>(null);
  const [workspace, setWorkspace] = useState<AIWorkspaceStatus | null>(null);
  const [workspaceBusy, setWorkspaceBusy] = useState(false);
  const [workspaceError, setWorkspaceError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    // 放进 then 中调用：旧版桥接或测试替身缺少该动作时只当作“未设置工作区”。
    Promise.resolve()
      .then(() => getAiWorkspaceStatus())
      .then((status) => {
        if (!cancelled) setWorkspace(status);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);
  const changeWorkspace = async (action: "pick" | "clear") => {
    if (workspaceBusy) return;
    setWorkspaceBusy(true);
    setWorkspaceError(null);
    try {
      const status = await (action === "pick"
        ? pickAiWorkspace()
        : clearAiWorkspace());
      if (!status.cancelled) setWorkspace(status);
    } catch (reason) {
      const message =
        reason instanceof Error ? reason.message : "设置 AI 工作区失败。";
      setWorkspaceError(message);
      setError(message);
    } finally {
      setWorkspaceBusy(false);
    }
  };
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
  const [activityOpen, setActivityOpen] = useState(() => false);
  const [resumeRetryTick, setResumeRetryTick] = useState(0);
  const pendingQueueRef = useRef<PendingChatRequest[]>([]);
  const steeringInFlightRef = useRef(new Set<string>());
  const steeringFlushingRef = useRef(new Set<string>());
  const composerBySessionRef = useRef(
    inheritedDrafts ??
      new Map<string, { draft: string; attachments: AIManagedAttachment[] }>(),
  );
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
  const restoredFailedRequestIdsRef = useRef(new Set<string>());
  const sessionLoadSequenceRef = useRef(0);
  const nativeDropTimerRef = useRef<number | null>(null);
  const nativeDropSeenRef = useRef(false);
  const sidebarAutoSubmitRef = useRef<string | null>(null);
  const resumeRetryRef = useRef(
    new Map<
      string,
      {
        taskId: string | null;
        failures: number;
        notBefore: number;
        cancel: boolean;
      }
    >(),
  );
  if (active)
    composerBySessionRef.current.set(active.id, { draft, attachments });
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
      // Stop only this component's poller. The backend task remains active and
      // the next AIChatView instance resumes it from the persisted task id.
      for (const task of sessionTasks.tasks.current.values()) {
        task.pollController.abort();
      }
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
      const task = sessionTasks.tasks.current.get(sessionId);
      if (!task || (task.taskId && task.taskId !== taskId)) return;
      if (!task.taskId && detail.type !== "start") return;
      const streamId = `stream-${taskId}`;
      if (detail.type === "steering_applied") {
        setActive((current) =>
          current?.id === sessionId
            ? {
                ...current,
                messages: current.messages.filter(
                  (message) => message.id !== streamId,
                ),
                traces: current.traces.map((trace) =>
                  trace.id === streamId
                    ? { ...trace, pending_text: "" }
                    : trace,
                ),
              }
            : current,
        );
        sessionTasks.update(sessionId, { liveStatus: null });
        return;
      }
      if (detail.type === "status") {
        const raw = detail.status;
        if (!raw || typeof raw !== "object") return;
        const value = raw as Record<string, unknown>;
        if (
          value.state !== "model_call" &&
          value.state !== "retrying" &&
          value.state !== "continuing"
        )
          return;
        const number = (key: string) =>
          typeof value[key] === "number" ? (value[key] as number) : undefined;
        sessionTasks.update(sessionId, {
          liveStatus: {
            taskId,
            status: {
              state: value.state,
              step: number("step"),
              attempt: number("attempt"),
              max_attempts: number("max_attempts"),
              reason:
                typeof value.reason === "string" ? value.reason : undefined,
              delay_seconds: number("delay_seconds"),
              part: number("part"),
              max_parts: number("max_parts"),
              received_at: Date.now(),
            },
          },
        });
        return;
      }
      if (detail.type === "start") {
        sessionTasks.update(sessionId, {
          taskId,
          startedAt:
            typeof detail.started_at === "string" && detail.started_at
              ? detail.started_at
              : Date.now(),
          liveStatus:
            task.liveStatus?.taskId === taskId ? task.liveStatus : null,
        });
        return;
      }
      if (detail.type === "progress_delta" || detail.type === "answer_start") {
        const delta = detail.delta;
        if (detail.type === "progress_delta" && typeof delta !== "string")
          return;
        setActive((current) => {
          if (current?.id !== sessionId) return current;
          const existing = current.traces.findIndex(
            (trace) => trace.id === streamId,
          );
          const trace: import("@/lib/types").AIAgentTrace =
            existing >= 0
              ? current.traces[existing]
              : {
                  id: streamId,
                  preset_id: current.preset_id,
                  status: "running",
                  steps: 0,
                  tool_runs: [],
                };
          const next = {
            ...trace,
            pending_text:
              detail.type === "answer_start"
                ? ""
                : ((trace.pending_text ?? "") + delta).slice(0, 2000),
          };
          const traces = [...current.traces];
          if (existing >= 0) traces[existing] = next;
          else traces.push(next);
          return { ...current, traces };
        });
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
          // A tool event can arrive after the first content/reasoning delta
          // (replayed snapshot ordering, or streamed tool runs mid-answer);
          // only recycle an empty placeholder stream message, never visible text.
          const messages = current.messages.filter(
            (message) =>
              message.id !== streamId ||
              Boolean(
                message.content.trim() || message.reasoning_content?.trim(),
              ),
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
          const next = {
            ...trace,
            pending_text: "",
            tool_runs: [...trace.tool_runs, run],
          };
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

  function persistPendingSteering(sessionId: string) {
    const pending = pendingQueueRef.current.filter(
      (request) => request.sessionId === sessionId,
    );
    const key = `${PENDING_STEERING_PREFIX}${sessionId}`;
    if (pending.length)
      window.localStorage.setItem(key, JSON.stringify(pending));
    else window.localStorage.removeItem(key);
  }

  function restorePendingSteering(sessionId: string) {
    try {
      const raw = window.localStorage.getItem(
        `${PENDING_STEERING_PREFIX}${sessionId}`,
      );
      const pending: PendingChatRequest[] = raw ? JSON.parse(raw) : [];
      if (!Array.isArray(pending)) return;
      for (const item of pending) {
        if (
          item &&
          item.sessionId === sessionId &&
          typeof item.optimisticId === "string" &&
          typeof item.text === "string" &&
          Array.isArray(item.attachments) &&
          item.attachments.every(
            (attachment) => attachment && typeof attachment.id === "number",
          ) &&
          !pendingQueueRef.current.some(
            (existing) => existing.optimisticId === item.optimisticId,
          )
        ) {
          pendingQueueRef.current.push(item);
        }
      }
    } catch {
      /* A corrupt local record never starts a new model turn. */
    }
  }

  function preserveSteeringDraft(request: PendingChatRequest, message: string) {
    setActive((current) =>
      current?.id === request.sessionId
        ? {
            ...current,
            messages: current.messages.filter(
              (item) => item.id !== request.optimisticId,
            ),
          }
        : current,
    );
    const failed = { request, message, retryable: false };
    if (
      activeSessionIdRef.current === request.sessionId &&
      isPresentRef.current
    ) {
      restoreFailedRequest(failed);
    } else if (!deletedSessionIdsRef.current.has(request.sessionId)) {
      const saved = failedRequestsRef.current.get(request.sessionId) ?? [];
      failedRequestsRef.current.set(request.sessionId, [...saved, failed]);
    }
  }

  async function flushSteering(sessionId: string, taskId: string) {
    if (steeringFlushingRef.current.has(sessionId)) return;
    steeringFlushingRef.current.add(sessionId);
    try {
      for (;;) {
        const request = pendingQueueRef.current.find(
          (item) => item.sessionId === sessionId,
        );
        if (!request) return;
        if (sessionTasks.tasks.current.get(sessionId)?.stopping) return;
        if (request.steeringTaskId && request.steeringTaskId !== taskId) {
          pendingQueueRef.current = pendingQueueRef.current.filter(
            (item) => item !== request,
          );
          persistPendingSteering(sessionId);
          preserveSteeringDraft(
            request,
            "原任务已结束，补充指令已保留为草稿。请确认后发送。",
          );
          continue;
        }
        request.steeringTaskId = taskId;
        persistPendingSteering(sessionId);
        steeringInFlightRef.current.add(request.optimisticId);
        try {
          const result = await steerAiChatTask(
            sessionId,
            taskId,
            request.text,
            request.optimisticId,
            request.attachments.map((item) => item.id),
          );
          pendingQueueRef.current = pendingQueueRef.current.filter(
            (item) => item !== request,
          );
          persistPendingSteering(sessionId);
          if (activeSessionIdRef.current === sessionId) {
            setError((current) =>
              current === STEERING_RETRY_NOTICE ? null : current,
            );
          }
          setActive((current) => {
            if (current?.id !== sessionId) return current;
            if (
              current.messages.some(
                (item) => item.id === result.user_message.id,
              )
            ) {
              return {
                ...current,
                messages: current.messages.filter(
                  (item) => item.id !== request.optimisticId,
                ),
              };
            }
            const messages = current.messages.filter(
              (item) => item.id !== result.user_message.id,
            );
            const index = messages.findIndex(
              (item) => item.id === request.optimisticId,
            );
            if (index >= 0) messages[index] = result.user_message;
            else messages.push(result.user_message);
            return { ...current, messages };
          });
        } catch (reason) {
          if (
            (reason as { category?: string })?.category ===
            "steering_unconfirmed"
          ) {
            if (activeSessionIdRef.current === sessionId) {
              setError(STEERING_RETRY_NOTICE);
            }
            return;
          }
          pendingQueueRef.current = pendingQueueRef.current.filter(
            (item) => item !== request,
          );
          persistPendingSteering(sessionId);
          setActive((current) =>
            current?.id === sessionId
              ? {
                  ...current,
                  messages: current.messages.filter(
                    (item) => item.id !== request.optimisticId,
                  ),
                }
              : current,
          );
          preserveSteeringDraft(
            request,
            reason instanceof Error
              ? reason.message
              : "补充指令未被接受，草稿已保留。",
          );
        } finally {
          steeringInFlightRef.current.delete(request.optimisticId);
        }
      }
    } finally {
      steeringFlushingRef.current.delete(sessionId);
    }
  }

  const applySession = useCallback(
    (session: AIChatSession) => {
      if (
        activeSessionIdRef.current &&
        activeSessionIdRef.current !== session.id
      ) {
        const saved = composerBySessionRef.current.get(session.id);
        setDraft(saved?.draft ?? "");
        setAttachments(saved?.attachments ?? []);
      } else if (!activeSessionIdRef.current) {
        const saved = composerBySessionRef.current.get(session.id);
        if (saved) {
          setDraft((current) => (current.trim() ? current : saved.draft));
          setAttachments(saved.attachments);
        }
      }
      setMotionMessageIds([]);
      stickToBottomRef.current = true;
      activeSessionIdRef.current = session.id;
      setActive(session);
      setModel((session.model as AIModel) || "auto");
      setDepth(session.thinking_depth);
      setSelectedPresetId(session.preset_id);
      let attached = skillsBySession.current.get(session.id);
      if (!attached) {
        try {
          const saved = JSON.parse(
            window.localStorage.getItem(`ai-chat-skills:${session.id}`) || "[]",
          );
          attached = Array.isArray(saved)
            ? saved
                .filter((id): id is string => typeof id === "string")
                .slice(0, 8)
            : [];
        } catch {
          attached = [];
        }
        skillsBySession.current.set(session.id, attached);
      }
      setSelectedSkills(attached);
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
        setSkills(presetResult.skills ?? []);
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
      // IME composition (e.g. 微软拼音 Esc 撤销候选词) must not close panels.
      if (event.isComposing || event.keyCode === 229) return;
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
    // 流式渐显时内容逐帧增高、messages 并不变化；运行中观察消息列表尺寸，
    // 仅当用户停留在底部时保持贴底（直接设置 scrollTop，避免平滑滚动抖动）。
    if (!busy || !isPresent || typeof ResizeObserver === "undefined") return;
    const end = endRef.current;
    const list = end?.parentElement;
    const thread = end?.closest(".ai-chat-thread") as HTMLElement | null;
    if (!list || !thread) return;
    const observer = new ResizeObserver(() => {
      if (stickToBottomRef.current) thread.scrollTop = thread.scrollHeight;
    });
    observer.observe(list);
    return () => observer.disconnect();
  }, [busy, isPresent]);

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
    // A new drag gesture starts: forget native paths seen by a previous drop.
    if (dragDepthRef.current === 0) nativeDropSeenRef.current = false;
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
    // On macOS the native event can also arrive *before* this DOM drop (see
    // AssignmentsView); those paths are already being ingested, so do not arm
    // the "path not ready" timer that would report a false error.
    if (paths.length > 0) {
      void ingestDroppedPaths(paths);
    } else if (!nativeDropSeenRef.current) {
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

  const cancelCurrentTask = () => {
    const sessionId = activeSessionIdRef.current;
    const task = sessionId ? sessionTasks.tasks.current.get(sessionId) : null;
    const controller = task?.controller;
    if (controller && !controller.signal.aborted) {
      sessionTasks.update(sessionId as string, { stopping: true });
      controller.abort();
    }
    const queued = pendingQueueRef.current.filter(
      (request) =>
        request.sessionId === sessionId &&
        !request.steeringTaskId &&
        !steeringInFlightRef.current.has(request.optimisticId),
    );
    const queuedIds = new Set(queued.map((request) => request.optimisticId));
    pendingQueueRef.current = pendingQueueRef.current.filter(
      (request) => !queuedIds.has(request.optimisticId),
    );
    if (sessionId) persistPendingSteering(sessionId);
    for (const request of queued)
      preserveSteeringDraft(request, "尚未提交的补充指令已保留为草稿。");
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
  };

  async function runSubmission(
    request: PendingChatRequest,
    resumeTaskId: string | null = null,
  ) {
    const attachmentIds = request.attachments.map(
      (attachment) => attachment.id,
    );
    const task = sessionTasks.begin(request.sessionId, resumeTaskId);
    if (!task) return;
    const { controller, pollController } = task;
    let trackedTaskId = resumeTaskId;
    let retryScheduled = false;
    if (activeSessionIdRef.current === request.sessionId) setCanRetry(false);
    if (resumeTaskId) {
      restorePendingSteering(request.sessionId);
      void flushSteering(request.sessionId, resumeTaskId);
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
                skillIds: request.skillIds,
                onTaskStart: (taskId) => {
                  trackedTaskId = taskId;
                  sessionTasks.update(request.sessionId, { taskId });
                  persistChatTask(taskId, request);
                  void flushSteering(request.sessionId, taskId);
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
                skillIds: request.skillIds,
                onTaskStart: (taskId) => {
                  trackedTaskId = taskId;
                  sessionTasks.update(request.sessionId, { taskId });
                  persistChatTask(taskId, request);
                  void flushSteering(request.sessionId, taskId);
                },
              },
            );
      clearPersistedChatTask(request.sessionId, trackedTaskId);
      resumeRetryRef.current.delete(request.sessionId);
      if (activeSessionIdRef.current === request.sessionId) {
        setError((current) =>
          current === RESUME_RETRY_NOTICE ? null : current,
        );
        setMotionMessageIds([result.assistant_message.id]);
      }
      setActive((current) => {
        if (current?.id !== request.sessionId) return current;
        // A resumed task may already be persisted and loaded with the session
        // (it finished while this view was unmounted); drop those copies so the
        // result is not rendered twice with duplicate React keys.
        let replacedUser = false;
        const messages = current.messages
          .filter(
            (message) =>
              !message.id.startsWith("stream-") &&
              message.id !== result.assistant_message.id,
          )
          .flatMap((message) => {
            if (
              message.id !== request.optimisticId &&
              message.id !== result.user_message.id
            )
              return [message];
            if (replacedUser) return [];
            replacedUser = true;
            return [result.user_message];
          });
        if (!replacedUser) messages.push(result.user_message);
        messages.push(result.assistant_message);
        return {
          ...current,
          ...result.session,
          messages,
          traces: [
            ...current.traces.filter(
              (trace) =>
                !trace.id.startsWith("stream-") && trace.id !== result.trace.id,
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
      if (category === "detached") {
        // The backend may still own and complete this task. Keep its persisted
        // identity and optimistic message so the view can resume polling it.
        return;
      }
      if (
        category === "cancel_unconfirmed" ||
        category === "resume_unconfirmed"
      ) {
        const previous = resumeRetryRef.current.get(request.sessionId);
        const sameTask = previous?.taskId === trackedTaskId;
        const failures = sameTask ? previous.failures + 1 : 1;
        resumeRetryRef.current.set(request.sessionId, {
          taskId: trackedTaskId,
          failures,
          notBefore:
            Date.now() + Math.min(5_000, 500 * 2 ** Math.min(failures - 1, 4)),
          // Keep an unconfirmed stop (user or timeout) alive across retries;
          // otherwise the reconnect silently resumes a task the user stopped.
          cancel:
            category === "cancel_unconfirmed" ||
            controller.signal.aborted ||
            (sameTask && previous.cancel),
        });
        retryScheduled = true;
        // The resumed poll replays every event from sequence 0; drop the partial
        // stream now so the retry does not append the same text/tools twice.
        setActive((current) =>
          current?.id === request.sessionId
            ? {
                ...current,
                messages: current.messages.filter(
                  (item) => !item.id.startsWith("stream-"),
                ),
                traces: current.traces.filter(
                  (item) => !item.id.startsWith("stream-"),
                ),
              }
            : current,
        );
        if (failures >= 3 && activeSessionIdRef.current === request.sessionId)
          setError(RESUME_RETRY_NOTICE);
        return;
      }
      resumeRetryRef.current.delete(request.sessionId);
      clearPersistedChatTask(request.sessionId, trackedTaskId);
      if (activeSessionIdRef.current === request.sessionId)
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
        // A deleted background session cannot replace the active session's error.
      } else {
        // The user switched conversations meanwhile: keep the draft and
        // attachments with their own session instead of the visible composer,
        // so "重试本次" can never send them to the wrong conversation.
        const stash = failedRequestsRef.current.get(request.sessionId) ?? [];
        failedRequestsRef.current.set(request.sessionId, [...stash, failed]);
      }
    } finally {
      sessionTasks.finish(request.sessionId, task);
      if (!retryScheduled && !readPersistedChatTask(request.sessionId)) {
        const unsent = pendingQueueRef.current.filter(
          (item) =>
            item.sessionId === request.sessionId &&
            !item.steeringTaskId &&
            !steeringInFlightRef.current.has(item.optimisticId),
        );
        for (const item of unsent)
          preserveSteeringDraft(
            item,
            "原任务已结束，未确认的补充指令已保留为草稿。",
          );
        const ids = new Set(unsent.map((item) => item.optimisticId));
        pendingQueueRef.current = pendingQueueRef.current.filter(
          (item) => !ids.has(item.optimisticId),
        );
        persistPendingSteering(request.sessionId);
      }
      if (activeSessionIdRef.current === request.sessionId) {
        window.setTimeout(() => {
          // Background reconnect attempts must not pull focus back every few
          // seconds; only recover focus that was lost with the stop button.
          const focused = document.activeElement;
          if (retryScheduled && focused && focused !== document.body) return;
          focusIfPresent(textareaRef.current);
        }, 0);
      }
    }
  }

  useEffect(() => {
    if (loading || !active) return;
    restorePendingSteering(active.id);
    const taskId = sessionTasks.tasks.current.get(active.id)?.taskId;
    if (taskId) void flushSteering(active.id, taskId);
    else {
      const request = pendingQueueRef.current.find(
        (item) => item.sessionId === active.id,
      );
      if (request?.steeringTaskId)
        void flushSteering(active.id, request.steeringTaskId);
    }
  }, [active?.id, loading, busy]);

  useEffect(() => {
    if (loading || !active || busy) return;
    const persisted = readPersistedChatTask(active.id);
    if (!persisted) return;
    const retry = resumeRetryRef.current.get(active.id);
    const retryMatchesTask = retry?.taskId === persisted.taskId;
    if (retryMatchesTask && retry.notBefore > Date.now()) {
      const timer = window.setTimeout(
        () => setResumeRetryTick((value) => value + 1),
        retry.notBefore - Date.now(),
      );
      return () => window.clearTimeout(timer);
    }
    void runSubmission(persisted.request, persisted.taskId);
    if (retryMatchesTask && retry.cancel) {
      // runSubmission installs its AbortController synchronously; abort it so
      // the resumed poll re-sends the stop request once the backend answers.
      sessionTasks.update(active.id, { stopping: true });
      sessionTasks.tasks.current.get(active.id)?.controller.abort();
    }
    // Re-check when busy clears: a session opened while another task was
    // running must still reconnect, otherwise submit() stays blocked.
  }, [active?.id, loading, busy, resumeRetryTick]);

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
      skillIds: selectedSkills.filter((id) =>
        skills.some((skill) => skill.id === id && skill.enabled),
      ),
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

    const running = sessionTasks.tasks.current.get(request.sessionId);
    if (running) {
      if (running.stopping) {
        preserveSteeringDraft(
          request,
          "当前任务正在停止，补充指令已保留为草稿。",
        );
        return;
      }
      request.steeringTaskId = running.taskId ?? undefined;
      pendingQueueRef.current.push(request);
      persistPendingSteering(request.sessionId);
      if (running.taskId) void flushSteering(request.sessionId, running.taskId);
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
      resumeRetryRef.current.delete(sessionId);
      pendingQueueRef.current = pendingQueueRef.current.filter(
        (request) => request.sessionId !== sessionId,
      );
      window.localStorage.removeItem(`${PENDING_STEERING_PREFIX}${sessionId}`);
      sessionTasks.tasks.current.get(sessionId)?.pollController.abort();
      const remaining = await refreshSessions();
      if (sessionId !== active?.id) return;
      if (remaining[0]) await openSession(remaining[0].id);
      else await newSession();
      window.requestAnimationFrame(() => focusIfPresent(textareaRef.current));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "删除对话失败");
    }
  };

  const selectedPreset =
    presets.find((preset) => preset.id === selectedPresetId) ?? null;
  const hasMessages = Boolean(active?.messages.length);
  const visibleBusy = busy;
  const hasVisibleStream = Boolean(
    active?.messages.some(
      (message) =>
        message.id.startsWith("stream-") && Boolean(message.content.trim()),
    ),
  );
  // Streaming deltas are kept quiet by aria-live="off" on the stream bubble
  // (AIChatMessageBubble). aria-busy on this live region is avoided on purpose:
  // NVDA/JAWS on Chromium never announce the region after busy clears.
  // 运行中的临时轨迹（stream-<task>）；任务标识尚未回传时取最近一条临时轨迹。
  // 不用 Array.prototype.findLast/at：旧版 WKWebView 未实现。
  const liveStreamTrace = useMemo(() => {
    const traces = active?.traces ?? [];
    if (runningTaskId) {
      const exact = traces.find(
        (trace) => trace.id === `stream-${runningTaskId}`,
      );
      if (exact) return exact;
    }
    for (let index = traces.length - 1; index >= 0; index -= 1) {
      if (traces[index].id.startsWith("stream-")) return traces[index];
    }
    return null;
  }, [active?.traces, runningTaskId]);
  const currentLiveStatus =
    liveStatus && liveStatus.taskId === runningTaskId
      ? liveStatus.status
      : null;
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
          activityOpen && !configPage
            ? "ai-workspace-body"
            : "ai-workspace-body activity-closed"
        }
      >
        {uiRelease === "2" ? (
          <AIWorkspaceNavigation
            open={historyOpen}
            onClose={() => setHistoryOpen(false)}
            onBack={onBack}
            onNew={() => {
              setConfigPage(null);
              void newSession();
            }}
            presets={presets}
            selectedPresetId={selectedPresetId}
            onSelectPreset={setSelectedPresetId}
            sessions={sessions}
            activeSessionId={active?.id}
            onOpenSession={(id) => {
              setConfigPage(null);
              void openSession(id);
            }}
            onDeleteSession={(id) => void removeSession(id)}
            page={configPage}
            onPage={setConfigPage}
          />
        ) : (
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
            onManageSkills={(skillId) =>
              setSkillManager({ skillId: skillId ?? null })
            }
            workspace={workspace}
            workspaceBusy={workspaceBusy}
            onPickWorkspace={() => void changeWorkspace("pick")}
            onClearWorkspace={() => void changeWorkspace("clear")}
          />
        )}
        {skillManager && uiRelease !== "2" && (
          <AISkillManager
            presets={presets}
            initialSkillId={skillManager.skillId}
            onClose={() => setSkillManager(null)}
            onSaved={(result, selectedId) => {
              setPresets(result.items);
              setSkillManager(null);
              setSelectedPresetId(
                (current) =>
                  selectedId ??
                  (result.items.some((item) => item.id === current)
                    ? current
                    : result.default_preset_id),
              );
            }}
          />
        )}
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

        {uiRelease === "2" && configPage && (
          <AIWorkspacePanel
            key={configPage}
            page={configPage}
            onClose={() => setConfigPage(null)}
            skills={skills}
            onSkillsSaved={(result) => {
              setPresets(result.items);
              setSkills(result.skills ?? []);
              changeSelectedSkills(
                selectedSkills.filter((id) =>
                  result.skills?.some(
                    (skill) => skill.id === id && skill.enabled,
                  ),
                ),
              );
            }}
            sessions={sessions}
            onOpenSession={(id) => {
              setConfigPage(null);
              void openSession(id);
            }}
            workspace={workspace}
            workspaceBusy={workspaceBusy}
            workspaceError={workspaceError}
            onPickWorkspace={() => void changeWorkspace("pick")}
            onClearWorkspace={() => void changeWorkspace("clear")}
            preferences={preferences}
            settingsSaving={settingsSaving}
            settingsStatus={settingsStatus}
            onPreferenceChange={(change) => void updatePreference(change)}
          />
        )}
        <section
          hidden={uiRelease === "2" && Boolean(configPage)}
          ref={conversationRef}
          style={readingStyle}
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
            <UIReleaseToggle />
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
                <h1>
                  {uiRelease === "2"
                    ? "今天想一起完成什么？"
                    : "今天想从学习数据里查什么？"}
                </h1>
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
                  const streaming = message.id.startsWith("stream-");
                  // 还没有正文的流式占位由下方的运行卡片表示，避免出现空白气泡。
                  if (streaming && !message.content.trim()) return null;
                  // 流式消息与其临时轨迹共用 stream-<task> 标识，正文出现后执行详情不丢失。
                  const trace = streaming
                    ? tracesById.get(message.id)
                    : message.trace_id
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
                      queued={false}
                      running={streaming && visibleBusy}
                      startedAt={streaming ? runningStartedAt : undefined}
                      liveStatus={streaming ? currentLiveStatus : undefined}
                    />
                  );
                })}
                <AnimatePresence>
                  {visibleBusy && !hasVisibleStream && (
                    <motion.div
                      className="ai-inline-execution ai-message ai-message-assistant"
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
                      <div className="ai-message-body">
                        <header className="ai-message-author">
                          <img src={aiAgentLogo} alt="" aria-hidden="true" />
                          <strong>
                            {selectedPreset?.name ?? "学习 Agent"}
                          </strong>
                        </header>
                        <InlineAgentExecution
                          running
                          trace={liveStreamTrace}
                          startedAt={runningStartedAt}
                          liveStatus={currentLiveStatus}
                          agentName={selectedPreset?.name ?? "学习 Agent"}
                          onOpenActivity={openActivity}
                        />
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>
                <div ref={endRef} />
              </div>
            )}
          </ContentReveal>

          <AIChatComposer
            draft={draft}
            workspace={workspace}
            onOpenWorkspace={() => setConfigPage("workspace")}
            onOpenResources={setConfigPage}
            onChangeWorkspace={(action) => void changeWorkspace(action)}
            workspaceBusy={workspaceBusy}
            workspaceError={workspaceError}
            error={error}
            attachmentBusy={attachmentBusy}
            attachments={attachments}
            active={Boolean(active)}
            presets={presets}
            selectedPresetId={selectedPresetId}
            skills={skills}
            selectedSkills={selectedSkills.filter((id) =>
              skills.some((skill) => skill.id === id && skill.enabled),
            )}
            onSelectSkills={changeSelectedSkills}
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
            activityOpen && !(uiRelease === "2" && configPage)
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
          open={activityOpen && !(uiRelease === "2" && configPage)}
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
