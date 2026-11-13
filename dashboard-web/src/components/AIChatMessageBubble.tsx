import { Check, ChevronDown, Copy, FileText } from "lucide-react";
import { motion, useReducedMotion } from "motion/react";
import {
  Children,
  isValidElement,
  memo,
  type ReactNode,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";
import ReactMarkdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";
import aiAgentLogo from "@/assets/ai-agent-logo.png";
import { InlineAgentExecution } from "@/components/AIExecutionCard";
import { AIFileChangeCard, fileChangeIds } from "@/components/AIFileChangeCard";
import { motionDuration, motionEase } from "@/lib/motion";
import {
  rehypeStreamFade,
  useStreamFade,
  useStreamReveal,
} from "@/lib/streamReveal";
import type {
  AIAgentLiveStatus,
  AIAgentTrace,
  AIChatMessage,
} from "@/lib/types";

// 桌面端以 file:// 加载，target=_blank 链接由 pywebview 交给系统打开
// （Windows 为 os.startfile）；只放行绝对 http(s)/mailto，相对、file、UNC 链接一律去掉 href。
function safeMarkdownUrl(url: string, key: string) {
  const value = url.trim();
  if (key === "href" && /^(https?:\/\/|mailto:)/i.test(value)) return value;
  if (key !== "href" && /^https?:\/\//i.test(value)) return value;
  return null;
}

// 旧 WKWebView 不支持 GFM 自动链接内部的 lookbehind，降级 CommonMark。
export function safeRemarkPlugins() {
  try {
    new RegExp("(?<=a)b");
    return [remarkGfm];
  } catch {
    return [];
  }
}

const remarkPlugins = safeRemarkPlugins();

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
  clip = false,
  fade = null,
}: {
  content: string;
  showCodeLineNumbers: boolean;
  clip?: boolean;
  fade?: ReturnType<typeof useStreamFade>;
}) {
  const markdown = (
    <ReactMarkdown
      remarkPlugins={remarkPlugins}
      rehypePlugins={
        fade ? [rehypeSanitize, [rehypeStreamFade, fade]] : [rehypeSanitize]
      }
      urlTransform={safeMarkdownUrl}
      components={{
        a: ({ node: _node, ...props }) => (
          <a {...props} target="_blank" rel="noreferrer" />
        ),
        // Never auto-load remote images from model output: a prompt-injected
        // reply could encode local data into the URL and leak it without a
        // click. Render a text placeholder (no <a>, so `[![x](u)](v)` stays
        // valid markup) and keep the address visible on hover.
        img: ({ src, alt }) => (
          <span title={typeof src === "string" ? src : undefined}>
            [图片：{alt || "外部图片"}]
          </span>
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
  );
  return (
    <div className="ai-markdown">
      {clip ? <div className="ai-markdown-clip">{markdown}</div> : markdown}
    </div>
  );
});

// 超过这个长度或行数的用户消息默认折叠，避免一次粘贴就占满整屏、把回复挤出视野。
const USER_COLLAPSE_CHARS = 600;
const USER_COLLAPSE_LINES = 12;

export function isLongUserMessage(content: string) {
  if (content.length > USER_COLLAPSE_CHARS) return true;
  let lines = 1;
  for (const char of content) {
    if (char === "\n") {
      lines += 1;
      if (lines > USER_COLLAPSE_LINES) return true;
    }
  }
  return false;
}

export const AIChatMessageBubble = memo(function AIChatMessageBubble({
  message,
  trace,
  agentName,
  onOpenActivity,
  onRevealAttachment,
  showCodeLineNumbers,
  animateEntry = false,
  queued = false,
  running = false,
  startedAt,
  liveStatus,
}: {
  message: AIChatMessage;
  trace?: AIAgentTrace | null;
  agentName: string;
  onOpenActivity: () => void;
  onRevealAttachment: (id: number) => void;
  showCodeLineNumbers: boolean;
  animateEntry?: boolean;
  queued?: boolean;
  running?: boolean;
  startedAt?: number | string | null;
  liveStatus?: AIAgentLiveStatus | null;
}) {
  const collapsible =
    message.role === "user" && isLongUserMessage(message.content);
  const [expanded, setExpanded] = useState(false);
  const contentId = useId();
  const [copyStatus, setCopyStatus] = useState<"copied" | "failed" | null>(
    null,
  );
  const copyTimerRef = useRef<number | null>(null);
  const shouldReduceMotion = useReducedMotion();
  // 流式回答按平滑节奏逐步显示并渐显；结束后由最终消息整体替换，余下文字一次显现。
  const streamingAnswer = message.role === "assistant" && running;
  const visibleContent = useStreamReveal(
    message.content,
    streamingAnswer,
    Boolean(shouldReduceMotion),
  );
  const fade = useStreamFade(streamingAnswer && !shouldReduceMotion);
  useEffect(
    () => () => {
      if (copyTimerRef.current !== null)
        window.clearTimeout(copyTimerRef.current);
    },
    [],
  );
  const showCopyStatus = (status: "copied" | "failed") => {
    setCopyStatus(status);
    if (copyTimerRef.current !== null)
      window.clearTimeout(copyTimerRef.current);
    copyTimerRef.current = window.setTimeout(() => {
      copyTimerRef.current = null;
      setCopyStatus(null);
    }, 1600);
  };
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(message.content);
    } catch {
      showCopyStatus("failed");
      return;
    }
    showCopyStatus("copied");
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
      // The in-progress stream bubble opts out of the thread's polite live
      // region so each content_delta is not read aloud; the final message is
      // a new node and is announced once by the thread.
      aria-live={
        message.role === "user" || message.id.startsWith("stream-")
          ? "off"
          : undefined
      }
      aria-label={message.role === "user" ? "你的消息" : agentName + " 回复"}
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
            running={running}
            streaming={running}
            startedAt={startedAt}
            liveStatus={liveStatus}
            agentName={agentName}
            onOpenActivity={onOpenActivity}
          />
        )}
        {collapsible ? (
          <div
            id={contentId}
            className={
              expanded
                ? "ai-user-content is-collapsible is-expanded"
                : "ai-user-content is-collapsible is-collapsed"
            }
          >
            <MessageMarkdown
              content={message.content}
              showCodeLineNumbers={showCodeLineNumbers}
              clip
            />
          </div>
        ) : (
          <MessageMarkdown
            content={visibleContent}
            showCodeLineNumbers={showCodeLineNumbers}
            fade={fade}
          />
        )}
        {collapsible && (
          <button
            type="button"
            className="ai-user-expand"
            aria-expanded={expanded}
            aria-controls={contentId}
            onClick={() => setExpanded((value) => !value)}
          >
            <ChevronDown aria-hidden="true" />
            {expanded ? "收起" : `展开全文（共 ${message.content.length} 字）`}
          </button>
        )}
        {message.role === "assistant" &&
          fileChangeIds(trace?.tool_runs ?? message.tool_runs).map(
            (changeId) => (
              <AIFileChangeCard key={changeId} changeId={changeId} />
            ),
          )}
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
          {copyStatus === "failed" && (
            <span className="ai-copy-feedback" role="status">
              复制失败
            </span>
          )}
          <button
            type="button"
            aria-label={
              copyStatus === "copied"
                ? "已复制"
                : copyStatus === "failed"
                  ? "复制失败，请重试"
                  : "复制消息"
            }
            title={copyStatus === "failed" ? "复制失败，请重试" : undefined}
            onClick={() => void copy()}
          >
            {copyStatus === "copied" ? (
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
