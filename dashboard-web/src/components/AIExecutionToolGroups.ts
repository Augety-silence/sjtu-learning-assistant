import type { AIToolRun } from "@/lib/types";

export interface ToolRunGroupData {
  start: number;
  runs: AIToolRun[];
}

/** Adjacent repetitions only; notes, rejected calls and proposals keep their place. */
export function groupToolRuns(runs: AIToolRun[]): ToolRunGroupData[] {
  const groups: ToolRunGroupData[] = [];
  runs.forEach((run, index) => {
    const group = groups[groups.length - 1];
    const previous = group?.runs[group.runs.length - 1];
    if (
      previous &&
      run.status === "ok" &&
      previous.status === "ok" &&
      run.tool_name !== "propose_file_change" &&
      run.tool_name === previous.tool_name &&
      run.phase === previous.phase &&
      !run.progress_text?.trim()
    ) {
      group.runs.push(run);
    } else groups.push({ start: index, runs: [run] });
  });
  return groups;
}
