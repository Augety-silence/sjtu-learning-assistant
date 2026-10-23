import { MessageCircle, Plus, Send, Sparkles } from "lucide-react";
import { useEffect, useState } from "react";
import { getAiChatSessions } from "@/lib/api";
import type { AIChatSessionSummary } from "@/lib/types";

export interface SidebarAIRequest {
  prompt?: string;
  sessionId?: string;
  newSession?: boolean;
}

function updatedLabel(session: AIChatSessionSummary) {
  const value = session.updated_at ?? session.created_at;
  if (!value) return "最近";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "最近";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
  }).format(date);
}

export function SidebarAssistant({
  onOpen,
}: {
  onOpen: (request?: SidebarAIRequest) => void;
}) {
  const [prompt, setPrompt] = useState("");
  const [sessions, setSessions] = useState<AIChatSessionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [unavailable, setUnavailable] = useState(false);

  useEffect(() => {
    let active = true;
    void getAiChatSessions()
      .then((result) => {
        if (!active) return;
        setSessions(result.items.slice(0, 3));
        setUnavailable(false);
      })
      .catch(() => {
        if (active) setUnavailable(true);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const submit = () => {
    const value = prompt.trim();
    if (!value) return;
    onOpen({ prompt: value, newSession: true });
    setPrompt("");
  };

  return (
    <section className="sidebar-assistant" aria-label="AI 对话">
      <button
        type="button"
        className="sidebar-ai-compact"
        aria-label="打开 AI 对话"
        title="AI 对话"
        onClick={() => onOpen()}
      >
        <Sparkles aria-hidden="true" />
      </button>

      <div className="sidebar-ai-expanded">
        <div className="sidebar-ai-heading">
          <span>
            <Sparkles aria-hidden="true" />
            AI 对话
          </span>
          <button
            type="button"
            aria-label="新建 AI 对话"
            title="新建 AI 对话"
            onClick={() => onOpen({ newSession: true })}
          >
            <Plus aria-hidden="true" />
          </button>
        </div>

        <form
          className="sidebar-ai-composer"
          onSubmit={(event) => {
            event.preventDefault();
            submit();
          }}
        >
          <textarea
            value={prompt}
            rows={2}
            aria-label="直接询问 AI"
            placeholder="直接询问课程、作业或资料…"
            onChange={(event) => setPrompt(event.target.value)}
            onKeyDown={(event) => {
              if (event.key !== "Enter" || event.shiftKey) return;
              event.preventDefault();
              submit();
            }}
          />
          <button
            type="submit"
            aria-label="发送给 AI"
            disabled={!prompt.trim()}
          >
            <Send aria-hidden="true" />
          </button>
        </form>

        <div className="sidebar-ai-history">
          <div className="sidebar-ai-history-label">
            <span>最近对话</span>
            <button type="button" onClick={() => onOpen()}>
              查看全部
            </button>
          </div>
          {loading && <p>正在读取…</p>}
          {!loading && unavailable && (
            <button
              type="button"
              className="sidebar-ai-fallback"
              onClick={() => onOpen()}
            >
              <MessageCircle aria-hidden="true" />
              进入 AI 工作区
            </button>
          )}
          {!loading && !unavailable && sessions.length === 0 && (
            <p>开始第一段学习对话</p>
          )}
          {!loading &&
            !unavailable &&
            sessions.map((session) => (
              <button
                type="button"
                className="sidebar-ai-session"
                key={session.id}
                title={session.title}
                onClick={() => onOpen({ sessionId: session.id })}
              >
                <span>{session.title}</span>
                <small>{updatedLabel(session)}</small>
              </button>
            ))}
        </div>
      </div>
    </section>
  );
}
