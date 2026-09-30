import { Activity, Check, Copy, FileText } from "lucide-react";
import { motion, useReducedMotion } from "motion/react";
import { Children, isValidElement, type ReactNode, useState } from "react";
import ReactMarkdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";
import aiAgentLogo from "@/assets/ai-agent-logo.png";
import userAvatar from "@/assets/user-avatar.webp";
import { motionDuration, motionEase } from "@/lib/motion";
import type { AIChatMessage } from "@/lib/types";

function NumberedCodeBlock({ children }: { children?: ReactNode }) {
  const child = Children.count(children) === 1 ? Children.only(children) : null;
  if (!isValidElement<{ children?: ReactNode; className?: string }>(child)) {
    return <pre>{children}</pre>;
  }
  const source = String(child.props.children ?? "").replace(/\n$/, "");
  return (
    <pre className="ai-code-with-lines">
      <code className={child.props.className}>
        {source.split("\n").map((line, index) => (
          <span className="ai-code-line" key={`${index}-${line}`}>
            <span className="ai-code-line-number" aria-hidden="true">
              {index + 1}
            </span>
            <span>{line || " "}</span>
          </span>
        ))}
      </code>
    </pre>
  );
}

export function AIChatMessageBubble({
  message,
  onOpenActivity,
  onRevealAttachment,
  showCodeLineNumbers,
  animateEntry = false,
  queued = false,
}: {
  message: AIChatMessage;
  onOpenActivity: () => void;
  onRevealAttachment: (id: number) => void;
  showCodeLineNumbers: boolean;
  animateEntry?: boolean;
  queued?: boolean;
}) {
  const [copied, setCopied] = useState(false);
  const shouldReduceMotion = useReducedMotion();
  const copy = async () => {
    await navigator.clipboard.writeText(message.content);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  };

  return (
    <motion.article
      className={`ai-message ai-message-${message.role}`}
      initial={
        animateEntry
          ? shouldReduceMotion
            ? false
            : { opacity: 0.72, y: message.role === "user" ? 4 : 6 }
          : false
      }
      animate={{ opacity: 1, y: 0 }}
      transition={{
        duration:
          message.role === "user"
            ? motionDuration.control
            : motionDuration.enter,
        ease: motionEase.out,
      }}
      data-motion-entry={animateEntry ? message.role : undefined}
      aria-label={message.role === "user" ? "你的消息" : "学习 Agent 回复"}
    >
      <span className="ai-message-avatar">
        <img
          src={message.role === "user" ? userAvatar : aiAgentLogo}
          alt=""
          aria-hidden="true"
        />
      </span>
      <div className="ai-message-body">
        {message.role === "assistant" && (
          <header className="ai-message-author">
            <strong>学习 Agent</strong>
          </header>
        )}
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
              table: ({ node: _node, ...props }) => (
                <div
                  className="ai-markdown-table"
                  role="region"
                  aria-label="表格内容"
                  tabIndex={0}
                >
                  <table {...props} />
                </div>
              ),
              ...(showCodeLineNumbers
                ? {
                    pre: ({ node: _node, children }) => (
                      <NumberedCodeBlock>{children}</NumberedCodeBlock>
                    ),
                  }
                : {}),
            }}
          >
            {message.content}
          </ReactMarkdown>
        </div>
        {message.attachments && message.attachments.length > 0 && (
          <div className="ai-message-attachments" aria-label="消息附件">
            {message.attachments.map((attachment) => (
              <button
                key={attachment.id}
                type="button"
                title={
                  attachment.status === "cloud_only"
                    ? "从云端校验恢复后在 Finder 中显示"
                    : "在 Finder 中显示"
                }
                onClick={() => onRevealAttachment(attachment.id)}
              >
                <FileText aria-hidden="true" />
                <span>{attachment.name}</span>
                {attachment.status === "cloud_only" && <small>云端</small>}
              </button>
            ))}
          </div>
        )}
        <footer>
          {queued && (
            <span className="ai-message-queued">将在当前检索完成后执行</span>
          )}
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
    </motion.article>
  );
}
