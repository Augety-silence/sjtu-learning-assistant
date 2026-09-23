import { Activity, Bot, Check, Copy } from "lucide-react";
import { useState } from "react";
import ReactMarkdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";
import type { AIChatMessage } from "@/lib/types";

export function AIChatMessageBubble({
  message,
  onOpenActivity,
}: {
  message: AIChatMessage;
  onOpenActivity: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    await navigator.clipboard.writeText(message.content);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  };

  return (
    <article className={`ai-message ai-message-${message.role}`}>
      <header>
        <span>
          {message.role === "user" ? "你" : <Bot aria-hidden="true" />}
        </span>
        <strong>{message.role === "user" ? "你" : "学习 Agent"}</strong>
      </header>
      <div className="ai-message-body">
        {message.reasoning_content && (
          <details className="ai-reasoning">
            <summary>查看推理过程</summary>
            <p>{message.reasoning_content}</p>
          </details>
        )}
        <div className="ai-markdown">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            rehypePlugins={[rehypeSanitize]}
            components={{
              a: ({ node: _node, ...props }) => (
                <a {...props} target="_blank" rel="noreferrer" />
              ),
            }}
          >
            {message.content}
          </ReactMarkdown>
        </div>
        <footer>
          {message.model && <span>{message.model}</span>}
          {message.tool_runs && message.tool_runs.length > 0 && (
            <button type="button" onClick={onOpenActivity}>
              <Activity aria-hidden="true" />
              {message.tool_runs.length} 次工具调用
            </button>
          )}
          <button
            type="button"
            aria-label="复制消息"
            onClick={() => void copy()}
          >
            {copied ? (
              <Check aria-hidden="true" />
            ) : (
              <Copy aria-hidden="true" />
            )}
          </button>
        </footer>
      </div>
    </article>
  );
}
