import {
  ArrowUp,
  Bot,
  CheckCircle2,
  CircleAlert,
  Clock3,
  Wrench,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { RichPreviewContent } from "@/components/RichPreviewContent";
import { EmptyState, ErrorState, LoadingState } from "@/components/States";
import { useToast } from "@/components/Toast";
import { Button } from "@/components/ui/Button";

export type CanvasAgentRole = "user" | "assistant";
export type CanvasToolStatus = "queued" | "running" | "completed" | "failed";

export interface CanvasToolCall {
  id: string;
  name: string;
  label?: string;
  status: CanvasToolStatus;
  argumentsSummary?: string | null;
  resultSummary?: string | null;
}

export interface CanvasAgentMessage {
  id: string;
  role: CanvasAgentRole;
  content: string;
  createdAt?: string | null;
  toolCalls?: CanvasToolCall[];
  error?: boolean;
}

export interface CanvasAgentOptions {
  maxTurns: number;
  maxTokens: number;
}

export interface CanvasAgentSendRequest {
  message: string;
  history: CanvasAgentMessage[];
  options: CanvasAgentOptions;
}

export interface CanvasAgentViewProps {
  messages: CanvasAgentMessage[];
  tools?: Array<{ name: string; label: string; enabled?: boolean }>;
  options?: CanvasAgentOptions;
  suggestions?: string[];
  loading?: boolean;
  responding?: boolean;
  error?: string | null;
  permissionDenied?: boolean;
  onRetry?: () => void | Promise<void>;
  onSend: (request: CanvasAgentSendRequest) => void | Promise<void>;
  onOptionsChange?: (options: CanvasAgentOptions) => void;
}

const defaultSuggestions = [
  "列出未来 7 天内截止的作业，并按时间排序",
  "查看当前课程，并汇总每门课的未完成事项",
  "帮我查找课程文件和最近公告",
];
const defaultOptions: CanvasAgentOptions = { maxTurns: 6, maxTokens: 1400 };
const toolStatusLabels: Record<CanvasToolStatus, string> = {
  queued: "等待调用",
  running: "调用中",
  completed: "已完成",
  failed: "失败",
};

function clamp(value: number, min: number, max: number, fallback: number) {
  return Number.isFinite(value)
    ? Math.min(max, Math.max(min, value))
    : fallback;
}

export function CanvasAgentView({
  messages,
  tools = [],
  options = defaultOptions,
  suggestions = defaultSuggestions,
  loading = false,
  responding = false,
  error,
  permissionDenied = false,
  onRetry,
  onSend,
  onOptionsChange,
}: CanvasAgentViewProps) {
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const { showToast } = useToast();
  const busy = responding || sending;

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ behavior: "auto", block: "end" });
  }, [messages, responding]);

  const send = async (value: string) => {
    const message = value.trim();
    if (!message || busy) return;
    setSending(true);
    setSendError(null);
    try {
      await onSend({ message, history: messages, options });
      setDraft("");
    } catch (reason) {
      const messageText =
        reason instanceof Error ? reason.message : "Canvas Agent 调用失败";
      setSendError(messageText);
      showToast({ kind: "error", message: messageText });
    } finally {
      setSending(false);
      window.setTimeout(() => inputRef.current?.focus(), 0);
    }
  };

  const updateOption = (field: keyof CanvasAgentOptions, value: string) => {
    const parsed = Number.parseInt(value, 10);
    const next = {
      ...options,
      [field]:
        field === "maxTurns"
          ? clamp(parsed, 1, 12, defaultOptions.maxTurns)
          : clamp(parsed, 256, 8192, defaultOptions.maxTokens),
    };
    onOptionsChange?.(next);
  };

  if (permissionDenied)
    return (
      <EmptyState
        title="Canvas Agent 不可用"
        description="当前账号没有 Canvas 工具权限，请检查登录状态和授权范围。"
      />
    );
  if (error) return <ErrorState message={error} retry={onRetry} />;
  if (loading) return <LoadingState label="正在准备 Canvas Agent…" />;

  return (
    <main className="ai-chat" aria-label="Canvas Agent 对话">
      <header className="ai-chat-toolbar">
        <div>
          <span className="eyebrow">
            <Bot aria-hidden="true" />
            Canvas Agent
          </span>
          <p>通过受控工具查询课程、作业、成员、成绩与文件。</p>
        </div>
        <span className={`status-tag ${responding ? "" : "status-downloaded"}`}>
          {responding ? "处理中" : "可提问"}
        </span>
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-[240px_minmax(0,1fr)] overflow-hidden max-[760px]:grid-cols-1">
        <aside
          className="border-r border-neutral-divider bg-surface-elevated p-4 max-[760px]:hidden"
          aria-label="Canvas Agent 设置"
        >
          <div className="ai-capability-card">
            <strong>可用 Canvas 工具</strong>
            <div>
              {tools.length ? (
                tools.map((tool) => (
                  <span
                    className={tool.enabled === false ? "is-disabled" : ""}
                    key={tool.name}
                  >
                    <Wrench aria-hidden="true" />
                    {tool.label}
                  </span>
                ))
              ) : (
                <span>由服务端按需提供</span>
              )}
            </div>
            <p>工具调用记录会附在对应回答中，便于核验。</p>
          </div>
          <div className="settings-list mt-4">
            <label className="settings-row settings-account-row">
              <span>
                <strong>最大工具轮次</strong>
                <small>范围 1–6</small>
              </span>
              <input
                className="h-9 w-20 rounded-control border border-component bg-surface-elevated px-2"
                aria-label="最大工具轮次"
                type="number"
                min={1}
                max={6}
                value={options.maxTurns}
                disabled={busy || !onOptionsChange}
                onChange={(event) =>
                  updateOption("maxTurns", event.target.value)
                }
              />
            </label>
            <label className="settings-row settings-account-row">
              <span>
                <strong>最大输出 Tokens</strong>
                <small>范围 256–3000</small>
              </span>
              <input
                className="h-9 w-24 rounded-control border border-component bg-surface-elevated px-2"
                aria-label="最大输出 Tokens"
                type="number"
                min={256}
                max={3000}
                step={256}
                value={options.maxTokens}
                disabled={busy || !onOptionsChange}
                onChange={(event) =>
                  updateOption("maxTokens", event.target.value)
                }
              />
            </label>
          </div>
        </aside>

        <section
          className={`ai-conversation ${messages.length ? "has-messages" : "is-empty"}`}
        >
          <div className="ai-chat-thread" aria-live="polite" aria-busy={busy}>
            {messages.length === 0 ? (
              <div className="ai-chat-empty">
                <span className="ai-chat-mark">
                  <Bot aria-hidden="true" />
                </span>
                <h2>开始和 Canvas Agent 对话</h2>
                <p>
                  直接描述课程范围、时间范围或需要查找的内容，Agent 会按需调用
                  Canvas 工具。
                </p>
                <div className="ai-chat-starters">
                  {suggestions.map((suggestion) => (
                    <button
                      type="button"
                      key={suggestion}
                      disabled={busy}
                      onClick={() => void send(suggestion)}
                    >
                      <span>{suggestion}</span>
                      <ArrowUp aria-hidden="true" />
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <div className="ai-chat-messages">
                {messages.map((message) => (
                  <article
                    className={`ai-message ai-message-${message.role}`}
                    key={message.id}
                  >
                    <header>
                      <span>
                        {message.role === "user" ? (
                          "你"
                        ) : (
                          <Bot aria-hidden="true" />
                        )}
                      </span>
                      <strong>
                        {message.role === "user" ? "你" : "Canvas Agent"}
                      </strong>
                    </header>
                    <div className="ai-message-body">
                      <RichPreviewContent
                        kind="markdown"
                        title={`${message.role === "user" ? "你的" : "Canvas Agent"}消息`}
                        content={message.content}
                        emptyLabel="正在生成回复…"
                      />
                      {message.toolCalls && message.toolCalls.length > 0 && (
                        <div
                          className="mt-3 grid gap-2"
                          aria-label="Canvas 工具调用"
                        >
                          {message.toolCalls.map((tool) => (
                            <details className="ai-reasoning" key={tool.id}>
                              <summary className="flex items-center gap-2">
                                {tool.status === "completed" ? (
                                  <CheckCircle2 aria-hidden="true" />
                                ) : tool.status === "failed" ? (
                                  <CircleAlert aria-hidden="true" />
                                ) : (
                                  <Clock3 aria-hidden="true" />
                                )}
                                {tool.label ?? tool.name} ·{" "}
                                {toolStatusLabels[tool.status]}
                              </summary>
                              {tool.argumentsSummary && (
                                <p>参数：{tool.argumentsSummary}</p>
                              )}
                              {tool.resultSummary && (
                                <p>结果：{tool.resultSummary}</p>
                              )}
                            </details>
                          ))}
                        </div>
                      )}
                    </div>
                  </article>
                ))}
                {responding && (
                  <div className="ai-thinking" role="status">
                    <span aria-hidden="true" />
                    Canvas Agent 正在调用工具并整理结果…
                  </div>
                )}
                <div ref={endRef} />
              </div>
            )}
          </div>

          <form
            className="ai-composer"
            onSubmit={(event) => {
              event.preventDefault();
              void send(draft);
            }}
          >
            {sendError && (
              <p className="ai-chat-error" role="alert">
                {sendError}
              </p>
            )}
            <div className="ai-composer-box">
              <textarea
                ref={inputRef}
                className="ai-composer-input"
                aria-label="输入 Canvas 问题"
                placeholder="例如：谁还没交本周的作业？"
                value={draft}
                maxLength={4000}
                rows={3}
                disabled={busy}
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    void send(draft);
                  }
                }}
              />
              <div className="ai-composer-toolbar">
                <span className="text-xs text-caption">
                  Enter 发送 · Shift+Enter 换行
                </span>
                <Button
                  type="submit"
                  size="icon"
                  aria-label="发送 Canvas 问题"
                  loading={busy}
                  disabled={!draft.trim()}
                >
                  <ArrowUp aria-hidden="true" />
                </Button>
              </div>
            </div>
            <p className="ai-chat-notice">
              Agent 可能出错，请核对课程权限、截止时间与成绩结果
            </p>
          </form>
        </section>
      </div>
    </main>
  );
}
