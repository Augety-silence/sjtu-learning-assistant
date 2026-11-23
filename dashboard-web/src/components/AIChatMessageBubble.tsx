import { Check, ChevronDown, Copy, FileText } from "lucide-react";
import { motion, useReducedMotion } from "motion/react";
import { memo, useEffect, useId, useRef, useState } from "react";
import aiAgentLogo from "@/assets/ai-agent-logo.png";
import { InlineAgentExecution } from "@/components/AIExecutionCard";
import { AIFileChangeCard, fileChangeIds } from "@/components/AIFileChangeCard";
import { MessageMarkdown } from "@/components/AIMarkdown";
import { motionDuration, motionEase } from "@/lib/motion";
import { useStreamFade, useStreamReveal } from "@/lib/streamReveal";
import type {
  AIAgentLiveStatus,
  AIAgentTrace,
  AIChatMessage,
} from "@/lib/types";

export { safeRemarkPlugins } from "@/components/AIMarkdown";

// 长用户消息默认折叠，避免一次粘贴占满阅读区。
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
