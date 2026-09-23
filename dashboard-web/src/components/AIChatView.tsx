import {
  Activity,
  ArrowUp,
  Bot,
  ChevronDown,
  PanelLeftOpen,
  PanelRightOpen,
  Sparkles,
} from "lucide-react";
import {
  type FormEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
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
  sendAiChatMessage,
} from "@/lib/api";
import type {
  AIAgentPreset,
  AIChatMessage,
  AIChatSession,
  AIChatSessionSummary,
  AIModel,
  AIThinkingDepth,
} from "@/lib/types";

const starters = [
  "列出本周所有课程的截止事项，并按紧急程度排序",
  "搜索最近的课程消息，告诉我有哪些需要处理",
  "梳理每门课程的文件，并给出本周复习建议",
];
const models: Array<{ value: AIModel; label: string }> = [
  { value: "auto", label: "自动选择" },
  { value: "deepseek-chat", label: "DeepSeek V4 Flash" },
  { value: "deepseek-reasoner", label: "DeepSeek V4 Reasoner" },
  { value: "minimax-m2.7", label: "MiniMax M2.7" },
  { value: "qwen3.8-27b", label: "Qwen 3.8 27B" },
];
const depths: Array<{ value: AIThinkingDepth; label: string }> = [
  { value: "quick", label: "快速" },
  { value: "standard", label: "标准" },
  { value: "deep", label: "深度思考" },
];

export function AIChatView({ onBack }: { onBack: () => void }) {
  const [presets, setPresets] = useState<AIAgentPreset[]>([]);
  const [selectedPresetId, setSelectedPresetId] = useState("");
  const [sessions, setSessions] = useState<AIChatSessionSummary[]>([]);
  const [active, setActive] = useState<AIChatSession | null>(null);
  const [draft, setDraft] = useState("");
  const [model, setModel] = useState<AIModel>("auto");
  const [depth, setDepth] = useState<AIThinkingDepth>("standard");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [activityOpen, setActivityOpen] = useState(true);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const endRef = useRef<HTMLDivElement>(null);

  const refreshSessions = useCallback(async () => {
    const result = await getAiChatSessions();
    setSessions(result.items);
    return result.items;
  }, []);

  const applySession = useCallback((session: AIChatSession) => {
    setActive(session);
    setModel((session.model as AIModel) || "auto");
    setDepth(session.thinking_depth);
    setSelectedPresetId(session.preset_id);
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
    endRef.current?.scrollIntoView?.({ behavior: "smooth", block: "end" });
  }, [active?.messages, busy]);

  const submit = async (content: string) => {
    const text = content.trim();
    if (!text || busy || !active || !selectedPresetId) return;
    const sessionId = active.id;
    const optimistic: AIChatMessage = {
      id: `pending-${Date.now()}`,
      role: "user",
      content: text,
    };
    setActive((current) =>
      current?.id === sessionId
        ? { ...current, messages: [...current.messages, optimistic] }
        : current,
    );
    setDraft("");
    setError(null);
    setBusy(true);
    setActivityOpen(true);
    try {
      const result = await sendAiChatMessage(
        sessionId,
        text,
        model,
        depth,
        selectedPresetId,
      );
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
      await refreshSessions();
    } catch (reason) {
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
  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    void submit(draft);
  };

  return (
    <main className="ai-workspace">
      <div
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
        />

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
            <div>
              <span className="ai-header-agent">
                <Bot aria-hidden="true" />
                {selectedPreset?.name ?? "学习 Agent"}
              </span>
              <strong>{active?.title ?? "新对话"}</strong>
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

          <div className="ai-chat-thread" aria-live="polite">
            {loading ? (
              <div className="ai-thinking" role="status">
                正在准备本地学习 Agent…
              </div>
            ) : !hasMessages ? (
              <div className="ai-chat-empty">
                <span className="ai-chat-mark">
                  <Sparkles aria-hidden="true" />
                </span>
                <p className="ai-empty-kicker">
                  {selectedPreset?.name ?? "学习 Agent"}
                </p>
                <h1>今天想从学习数据里查什么？</h1>
                <p>
                  我会按需调用只读工具，查询课程、课程文件、截止日期、消息和资料树，并在右侧展示完整检索轨迹。
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
                  />
                ))}
                {busy && (
                  <div className="ai-thinking" role="status">
                    <span />
                    Agent 正在检索本地学习数据…
                  </div>
                )}
                <div ref={endRef} />
              </div>
            )}
          </div>

          <form className="ai-composer" onSubmit={onSubmit}>
            {error && (
              <p className="ai-chat-error" role="alert">
                {error}
              </p>
            )}
            <div className="ai-composer-box">
              <textarea
                ref={textareaRef}
                value={draft}
                maxLength={4000}
                rows={hasMessages ? 2 : 3}
                aria-label="输入问题"
                placeholder="让 Agent 检索课程、文件、截止日期或消息…"
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    void submit(draft);
                  }
                }}
              />
              <div className="ai-composer-toolbar">
                <div className="ai-chat-controls">
                  <span className="ai-agent-control">
                    <Bot aria-hidden="true" />
                    {selectedPreset?.name ?? "Agent"}
                  </span>
                  <label>
                    <span className="sr-only">模型</span>
                    <select
                      aria-label="模型"
                      value={model}
                      disabled={busy}
                      onChange={(event) =>
                        setModel(event.target.value as AIModel)
                      }
                    >
                      {models.map((item) => (
                        <option key={item.value} value={item.value}>
                          {item.label}
                        </option>
                      ))}
                    </select>
                    <ChevronDown aria-hidden="true" />
                  </label>
                  <label>
                    <span className="sr-only">思考深度</span>
                    <select
                      aria-label="思考深度"
                      value={depth}
                      disabled={busy}
                      onChange={(event) =>
                        setDepth(event.target.value as AIThinkingDepth)
                      }
                    >
                      {depths.map((item) => (
                        <option key={item.value} value={item.value}>
                          {item.label}
                        </option>
                      ))}
                    </select>
                    <ChevronDown aria-hidden="true" />
                  </label>
                </div>
                <Button
                  type="submit"
                  size="icon"
                  aria-label="发送消息"
                  disabled={
                    busy || !draft.trim() || !active || !selectedPresetId
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
        </section>

        <AIChatTracePanel
          open={activityOpen}
          onClose={() => setActivityOpen(false)}
          preset={selectedPreset}
          traces={active?.traces ?? []}
          busy={busy}
        />
      </div>
    </main>
  );
}
