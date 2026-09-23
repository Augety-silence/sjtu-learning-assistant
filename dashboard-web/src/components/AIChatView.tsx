import {
  ArrowLeft,
  ArrowUp,
  Bot,
  Check,
  ChevronDown,
  Copy,
  Menu,
  Plus,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import {
  type FormEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import ReactMarkdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";
import { Button } from "@/components/ui/Button";
import {
  createAiChatSession,
  deleteAiChatSession,
  getAiChatSession,
  getAiChatSessions,
  sendAiChatMessage,
} from "@/lib/api";
import type {
  AIChatMessage,
  AIChatSession,
  AIChatSessionSummary,
  AIModel,
  AIThinkingDepth,
} from "@/lib/types";

const starters = [
  "帮我整理最近一周的待办和优先级",
  "总结最近的课程消息，指出需要我行动的内容",
  "根据近期截止事项，给我一份学习安排",
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
  const [sessions, setSessions] = useState<AIChatSessionSummary[]>([]);
  const [active, setActive] = useState<AIChatSession | null>(null);
  const [draft, setDraft] = useState("");
  const [model, setModel] = useState<AIModel>("auto");
  const [depth, setDepth] = useState<AIThinkingDepth>("standard");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const endRef = useRef<HTMLDivElement>(null);

  const refreshSessions = useCallback(async () => {
    const result = await getAiChatSessions();
    setSessions(result.items);
    return result.items;
  }, []);

  const openSession = useCallback(async (sessionId: string) => {
    setLoading(true);
    setError(null);
    try {
      const session = await getAiChatSession(sessionId);
      setActive(session);
      setModel((session.model as AIModel) || "auto");
      setDepth(session.thinking_depth);
      setHistoryOpen(false);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "对话读取失败");
    } finally {
      setLoading(false);
    }
  }, []);

  const newSession = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const session = await createAiChatSession("auto", "standard");
      setActive(session);
      setModel("auto");
      setDepth("standard");
      setHistoryOpen(false);
      await refreshSessions();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法创建新对话");
    } finally {
      setLoading(false);
    }
  }, [refreshSessions]);

  useEffect(() => {
    let cancelled = false;
    const initialLoad = async () => {
      setLoading(true);
      try {
        const rows = await refreshSessions();
        if (cancelled) return;
        if (rows[0]) await openSession(rows[0].id);
        else await newSession();
      } catch (reason) {
        if (!cancelled) {
          setError(
            reason instanceof Error ? reason.message : "历史对话读取失败",
          );
          setLoading(false);
        }
      }
    };
    void initialLoad();
    return () => {
      cancelled = true;
    };
  }, [newSession, openSession, refreshSessions]);

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ behavior: "smooth", block: "end" });
  }, [active?.messages, busy]);

  const submit = async (content: string) => {
    const text = content.trim();
    if (!text || busy || !active) return;
    const optimistic: AIChatMessage = {
      id: `pending-${Date.now()}`,
      role: "user",
      content: text,
    };
    setActive({ ...active, messages: [...active.messages, optimistic] });
    setDraft("");
    setError(null);
    setBusy(true);
    try {
      const result = await sendAiChatMessage(active.id, text, model, depth);
      setActive((current) =>
        current
          ? {
              ...current,
              ...result.session,
              messages: [
                ...current.messages.filter((item) => item.id !== optimistic.id),
                result.user_message,
                result.assistant_message,
              ],
            }
          : current,
      );
      await refreshSessions();
    } catch (reason) {
      setActive((current) =>
        current
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
          : "AI 暂时无法回答，请稍后重试。",
      );
    } finally {
      setBusy(false);
      window.setTimeout(() => textareaRef.current?.focus(), 0);
    }
  };

  const removeSession = async (sessionId: string) => {
    if (!window.confirm("确定删除这段历史对话？")) return;
    await deleteAiChatSession(sessionId);
    const remaining = await refreshSessions();
    if (remaining[0]) await openSession(remaining[0].id);
    else await newSession();
  };

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    void submit(draft);
  };

  return (
    <main className="ai-workspace">
      <header className="ai-workspace-header">
        <Button variant="ghost" size="sm" onClick={onBack}>
          <ArrowLeft aria-hidden="true" />
          返回
        </Button>
        <div className="ai-workspace-title">
          <Sparkles aria-hidden="true" />
          <div>
            <strong>AI 学习助手</strong>
            <span>{active?.title ?? "新对话"}</span>
          </div>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => void newSession()}
          disabled={busy}
        >
          <Plus aria-hidden="true" />
          新对话
        </Button>
      </header>

      <div className="ai-workspace-body">
        <aside className={historyOpen ? "ai-history is-open" : "ai-history"}>
          <div className="ai-history-heading">
            <strong>历史对话</strong>
            <Button
              variant="ghost"
              size="icon"
              aria-label="关闭历史"
              onClick={() => setHistoryOpen(false)}
            >
              <X />
            </Button>
          </div>
          <div className="ai-history-list">
            {sessions.length === 0 && <p>还没有历史对话</p>}
            {sessions.map((session) => (
              <div
                className={
                  session.id === active?.id
                    ? "ai-history-item active"
                    : "ai-history-item"
                }
                key={session.id}
              >
                <button
                  type="button"
                  onClick={() => void openSession(session.id)}
                >
                  <strong>{session.title}</strong>
                  <span>
                    {
                      depths.find(
                        (item) => item.value === session.thinking_depth,
                      )?.label
                    }{" "}
                    · {session.model === "auto" ? "自动选择" : session.model}
                  </span>
                </button>
                <button
                  type="button"
                  aria-label={`删除对话：${session.title}`}
                  onClick={() => void removeSession(session.id)}
                >
                  <Trash2 aria-hidden="true" />
                </button>
              </div>
            ))}
          </div>
        </aside>

        <section className="ai-conversation" aria-label="AI 对话">
          <div className="ai-mobile-history">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setHistoryOpen(true)}
            >
              <Menu aria-hidden="true" />
              历史对话
            </Button>
          </div>
          <div className="ai-chat-thread" aria-live="polite">
            {loading ? (
              <div className="ai-thinking" role="status">
                正在读取对话…
              </div>
            ) : !active?.messages.length ? (
              <div className="ai-chat-empty">
                <span className="ai-chat-mark">
                  <Bot aria-hidden="true" />
                </span>
                <h2>今天想先处理什么？</h2>
                <p>我可以结合最近同步的课程、消息与截止事项，帮你梳理重点。</p>
                <div className="ai-chat-starters">
                  {starters.map((starter) => (
                    <button
                      key={starter}
                      type="button"
                      onClick={() => void submit(starter)}
                    >
                      {starter}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <div className="ai-chat-messages">
                {active.messages.map((message) => (
                  <MessageBubble key={message.id} message={message} />
                ))}
                {busy && (
                  <div className="ai-thinking" role="status">
                    AI 正在整理学习信息…
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
            <div className="ai-chat-controls">
              <label>
                <span className="sr-only">模型</span>
                <select
                  aria-label="模型"
                  value={model}
                  disabled={busy}
                  onChange={(event) => setModel(event.target.value as AIModel)}
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
              {model === "auto" && (
                <span>自动：深度思考将切换为 deepseek-reasoner</span>
              )}
            </div>
            <div className="ai-composer-box">
              <textarea
                ref={textareaRef}
                value={draft}
                maxLength={4000}
                rows={3}
                aria-label="输入问题"
                placeholder="询问课程消息、截止事项或学习安排…"
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    void submit(draft);
                  }
                }}
              />
              <Button
                type="submit"
                aria-label="发送消息"
                disabled={busy || !draft.trim() || !active}
              >
                <ArrowUp aria-hidden="true" />
                发送
              </Button>
            </div>
            <p className="ai-chat-notice">
              对话保存在本机 SQLite；AI 可能出错，请核对截止时间与提交要求。
            </p>
          </form>
        </section>
      </div>
    </main>
  );
}

function MessageBubble({ message }: { message: AIChatMessage }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    await navigator.clipboard.writeText(message.content);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  };
  return (
    <article className={`ai-message ai-message-${message.role}`}>
      <header>
        <span>{message.role === "user" ? "你" : "AI"}</span>
        <Button
          variant="ghost"
          size="icon"
          aria-label="复制消息"
          onClick={() => void copy()}
        >
          {copied ? <Check aria-hidden="true" /> : <Copy aria-hidden="true" />}
        </Button>
      </header>
      {message.reasoning_content && (
        <details className="ai-reasoning">
          <summary>查看思考过程</summary>
          <p>{message.reasoning_content}</p>
        </details>
      )}
      <div className="ai-markdown">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          rehypePlugins={[rehypeSanitize]}
        >
          {message.content}
        </ReactMarkdown>
      </div>
      {message.model && <footer>{message.model}</footer>}
    </article>
  );
}
