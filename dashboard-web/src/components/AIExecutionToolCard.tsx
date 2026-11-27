import { ChevronRight, CircleSlash2, Wrench } from "lucide-react";
import { memo, useState } from "react";
import { ExecutionNote } from "@/components/AIExecutionNote";
import { ToolResultPreview } from "@/components/AIExecutionToolPreview";
import {
  compactJson,
  formatToolDuration,
  toolArgumentHint,
  toolIcons,
  toolLabel,
  toolResultLabel,
} from "@/components/AIExecutionTools";
import type { AIToolRun } from "@/lib/types";

export const TraceRun = memo(function TraceRun({
  run,
  defaultOpen = false,
}: {
  run: AIToolRun;
  defaultOpen?: boolean;
}) {
  const ok = run.status === "ok";
  const [userOpen, setOpen] = useState<boolean | null>(null);
  const open = userOpen ?? defaultOpen;
  const Icon = toolIcons[run.tool_name] ?? Wrench;
  const hint = toolArgumentHint(run);
  const duration = formatToolDuration(run.duration_ms);
  return (
    <>
      <ExecutionNote text={run.progress_text} />
      <details
        className={ok ? "ai-tool-run" : "ai-tool-run is-rejected"}
        open={open}
        onToggle={(event) => {
          if (event.target === event.currentTarget)
            setOpen(event.currentTarget.open);
        }}
      >
        <summary>
          <span
            className={ok ? "ai-tool-icon is-ok" : "ai-tool-icon is-rejected"}
          >
            {ok ? <Icon aria-hidden /> : <CircleSlash2 aria-hidden="true" />}
          </span>
          <span className="ai-tool-run-copy">
            <strong>
              {toolLabel(run.tool_name)}
              {hint && <em className="ai-tool-hint">{hint}</em>}
            </strong>
            <span>
              {toolResultLabel(run)}
              {duration && (
                <span className="ai-tool-duration"> · {duration}</span>
              )}
            </span>
          </span>
          <span
            className={
              run.phase === "prefetch"
                ? "ai-tool-phase is-prefetch"
                : "ai-tool-phase is-model"
            }
          >
            {run.phase === "prefetch" ? "预检索" : "模型调用"}
          </span>
          <ChevronRight className="ai-tool-caret" aria-hidden="true" />
        </summary>
        <ToolResultPreview run={run} />
        <details className="ai-tool-technical">
          <summary>查看技术详情</summary>
          <div className="ai-tool-detail">
            <div>
              <span>安全参数</span>
              <pre>{compactJson(run.arguments_summary)}</pre>
            </div>
            <div>
              <span>结果摘要</span>
              <pre>{compactJson(run.result_summary)}</pre>
            </div>
          </div>
        </details>
      </details>
    </>
  );
});
