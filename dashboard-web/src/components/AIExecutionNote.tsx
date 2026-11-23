import { useReducedMotion } from "motion/react";
import { memo } from "react";
import { MessageMarkdown } from "@/components/AIMarkdown";
import { useStreamReveal } from "@/lib/streamReveal";

export const ExecutionNote = memo(function ExecutionNote({
  text = "",
  streaming = false,
}: {
  text?: string;
  streaming?: boolean;
}) {
  const reduceMotion = useReducedMotion();
  // 长执行说明也要解析 Markdown；限制在约 12 次/秒，避免每帧重解析全文。
  const visible = useStreamReveal(text, streaming, Boolean(reduceMotion), 80);
  if (!text.trim()) return null;
  return (
    <div className="ai-execution-note" aria-live="off">
      <MessageMarkdown content={visible} execution />
    </div>
  );
});
