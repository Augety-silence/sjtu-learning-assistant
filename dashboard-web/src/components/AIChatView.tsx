import { ArrowUp, Bot, RotateCcw, Sparkles } from "lucide-react";
import { type FormEvent, useRef, useState } from "react";
import { Button } from "@/components/ui/Button";
import { sendAiChat } from "@/lib/api";
import type { AIChatMessage } from "@/lib/types";

const starters = [
  "帮我整理最近一周的待办和优先级",
  "总结最近的课程消息，指出需要我行动的内容",
  "根据近期截止事项，给我一份学习安排",
];

function messageId() {
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export function AIChatView() {
  const [messages, setMessages] = useState<AIChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const submit = async (content: string) => {
    const text = content.trim();
    if (!text || busy) return;
    const userMessage: AIChatMessage = {
      id: messageId(),
      role: "user",
      content: text,
    };
    const next = [...messages, userMessage];
    setMessages(next);
    setDraft("");
    setError(null);
    setBusy(true);
    try {
      const result = await sendAiChat(
        next
          .slice(-12)
          .map(({ role, content: value }) => ({ role, content: value })),
      );
      setMessages((current) => [
        ...current,
        { id: messageId(), role: "assistant", content: result.reply },
      ]);
    } catch (reason) {
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

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    void submit(draft);
  };

  return (
    <section className="ai-chat" aria-label="AI 学习助手">
      <div className="ai-chat-toolbar">
        <div>
          <span className="eyebrow">
            <Sparkles aria-hidden="true" />
            AI 学习助手
          </span>
          <p>
            结合最近同步的课程、消息与截止事项回答；重要信息请返回原页面核对。
          </p>
        </div>
        {messages.length > 0 && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setMessages([]);
              setError(null);
            }}
          >
            <RotateCcw aria-hidden="true" />
            新对话
          </Button>
        )}
      </div>

      <div className="ai-chat-thread" aria-live="polite">
        {messages.length === 0 ? (
          <div className="ai-chat-empty">
            <span className="ai-chat-mark">
              <Bot aria-hidden="true" />
            </span>
            <h2>今天想先处理什么？</h2>
            <p>
              我可以根据 App
              中最近同步的数据，帮你梳理消息、截止事项和学习安排。
            </p>
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
            {messages.map((message) => (
              <article
                key={message.id}
                className={`ai-message ai-message-${message.role}`}
              >
                <span>{message.role === "user" ? "你" : "AI"}</span>
                <p>{message.content}</p>
              </article>
            ))}
            {busy && (
              <div className="ai-thinking" role="status">
                AI 正在整理学习信息…
              </div>
            )}
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
            disabled={busy || !draft.trim()}
          >
            <ArrowUp aria-hidden="true" />
            发送
          </Button>
        </div>
        <p className="ai-chat-notice">
          AI 可能出错，请核对截止时间与提交要求。对话仅保留在当前窗口。
        </p>
      </form>
    </section>
  );
}
