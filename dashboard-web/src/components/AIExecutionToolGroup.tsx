import { ChevronRight } from "lucide-react";
import { useState } from "react";
import { ExecutionNote } from "@/components/AIExecutionNote";
import { TraceRun } from "@/components/AIExecutionToolCard";
import type { ToolRunGroupData } from "@/components/AIExecutionToolGroups";
import { formatToolDuration, toolLabel } from "@/components/AIExecutionTools";

export function ToolRunGroup({
  group,
  defaultOpen = false,
}: {
  group: ToolRunGroupData;
  defaultOpen?: boolean;
}) {
  const [userOpen, setOpen] = useState<boolean | null>(null);
  if (group.runs.length === 1)
    return <TraceRun run={group.runs[0]} defaultOpen={defaultOpen} />;
  const first = group.runs[0];
  const knownDuration = group.runs.every(
    (run) =>
      typeof run.duration_ms === "number" &&
      Number.isFinite(run.duration_ms) &&
      run.duration_ms >= 0,
  );
  const duration = knownDuration
    ? formatToolDuration(
        group.runs.reduce((sum, run) => sum + (run.duration_ms ?? 0), 0),
      )
    : null;
  return (
    <>
      <ExecutionNote text={first.progress_text} />
      <details
        className="ai-tool-group"
        open={userOpen ?? defaultOpen}
        onToggle={(event) => {
          if (event.target === event.currentTarget)
            setOpen(event.currentTarget.open);
        }}
      >
        <summary>
          <strong>{toolLabel(first.tool_name)}</strong>
          <span>
            {group.runs.length} 次调用{duration ? ` · 合计 ${duration}` : ""}
          </span>
          <ChevronRight className="ai-tool-caret" aria-hidden="true" />
        </summary>
        <div className="ai-tool-group-runs">
          {group.runs.map((run, index) => (
            <TraceRun
              defaultOpen={defaultOpen}
              key={group.start + index}
              run={
                index === 0 && run.progress_text
                  ? { ...run, progress_text: undefined }
                  : run
              }
            />
          ))}
        </div>
      </details>
    </>
  );
}
