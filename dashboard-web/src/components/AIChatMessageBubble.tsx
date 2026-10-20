import { Check, Copy, FileText } from "lucide-react";
import { motion, useReducedMotion } from "motion/react";
import {
  Children,
  isValidElement,
  memo,
  type ReactNode,
  useState,
} from "react";
import ReactMarkdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";
import aiAgentLogo from "@/assets/ai-agent-logo.png";
import { InlineAgentExecution } from "@/components/AIExecutionCard";
import { motionDuration, motionEase } from "@/lib/motion";
import type { AIAgentTrace, AIChatMessage } from "@/lib/types";

// 桌面端以 file:// 加载，target=_blank 链接由 pywebview 交给系统打开
// （Windows 为 os.startfile）；只放行绝对 http(s)/mailto，相对、file、UNC 链接一律去掉 href。
function safeMarkdownUrl(url: string, key: string) {
  const value = url.trim();
  if (key === "href" && /^(https?:\/\/|mailto:)/i.test(value)) return value;
  if (key !== "href" && /^https?:\/\//i.test(value)) return value;
  return null;
}

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

const MessageMarkdown = memo(function MessageMarkdown({
  content,
  showCodeLineNumbers,
}: {
  content: string;
  showCodeLineNumbers: boolean;
}) {
  return (
    <div className="ai-markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeSanitize]}
        urlTransform={safeMarkdownUrl}
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
        {content}
      </ReactMarkdown>
    </div>
  );
});

export const AIChatMessageBubble = memo(function AIChatMessageBubble({
  message,
  trace,
  agentName,
  onOpenActivity,
  onRevealAttachment,
  showCodeLineNumbers,
  animateEntry = false,
  queued = false,
}: {
  message: AIChatMessage;
  trace?: AIAgentTrace | null;
  agentName: string;
  onOpenActivity: () => void;
  onRevealAttachment: (id: number) => void;
  showCodeLineNumbers: boolean;
  animateEntry?: boolean;
  queued?: boolean;
}) {
  const [copied, setCopied] = useState(false);
  const shouldReduceMotion = useReducedMotion();
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(message.content);
    } catch {
      return;
    }
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
      <div className="ai-message-body">
        {message.role === "assistant" && (
          <header className="ai-message-author">
            <img src={aiAgentLogo} alt="" aria-hidden="true" />
            <strong>{agentName}</strong>
          </header>
        )}
        {message.role === "assistant" && (
          <InlineAgentExecution
            trace={trace}
            toolRuns={message.tool_runs}
            onOpenActivity={onOpenActivity}
          />
        )}
        <MessageMarkdown
          content={message.content}
          showCodeLineNumbers={showCodeLineNumbers}
        />
        {message.attachments && message.attachments.length > 0 && (
          <div
            className="ai-message-attachments"
            role="group"
            aria-label="消息附件"
          >
            {message.attachments.map((attachment) => (
              <button
                key={attachment.id}
                type="button"
                title={
                  attachment.status === "cloud_only"
                    ? "从云端校验恢复后在文件夹中显示"
                    : "在文件夹中显示"
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
});
