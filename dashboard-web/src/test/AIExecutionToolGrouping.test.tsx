// @vitest-environment jsdom

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AIChatTracePanel } from "@/components/AIChatTracePanel";
import { InlineAgentExecution } from "@/components/AIExecutionCard";
import { TraceRun } from "@/components/AIExecutionToolCard";
import { groupToolRuns } from "@/components/AIExecutionToolGroups";
import { applyThemeMode } from "@/lib/theme";
import type { AIAgentTrace, AIToolRun } from "@/lib/types";

const run: AIToolRun = {
  tool_name: "read_compiled_knowledge",
  phase: "model",
  status: "ok",
  arguments_summary: {},
  result_summary: {
    returned_chars: 600,
    offset: 0,
    total_chars: 1800,
    next_offset: 600,
  },
  duration_ms: 120,
};
afterEach(cleanup);

describe("grouped execution records", () => {
  it("opens latest Activity with saved names and excerpts and respects manual collapse", async () => {
    const trace: AIAgentTrace = {
      id: "latest",
      status: "completed",
      preset_id: "general",
      steps: 1,
      tool_runs: [
        {
          ...run,
          result_summary: {
            count: 1,
            preview: {
              version: 1,
              items: [
                {
                  title: "图论讲义",
                  detail: "离散数学",
                  excerpt: "欧拉路径经过每条边一次",
                },
              ],
              text: "",
            },
          },
        },
      ],
    };
    const props = {
      open: true,
      onClose: vi.fn(),
      preset: null,
      traces: [trace],
      busy: false,
      runningTaskId: null,
    };
    const { container, rerender } = render(<AIChatTracePanel {...props} />);
    const turn = container.querySelector<HTMLDetailsElement>(".ai-trace-step")!;
    const tool = container.querySelector<HTMLDetailsElement>(".ai-tool-run")!;
    expect(turn.open).toBe(true);
    expect(tool.open).toBe(true);
    expect(screen.getByText("图论讲义")).toBeTruthy();
    expect(screen.getByText("欧拉路径经过每条边一次")).toBeTruthy();
    const technical =
      tool.querySelector<HTMLDetailsElement>(".ai-tool-technical")!;
    technical.open = true;
    fireEvent(technical, new Event("toggle", { bubbles: true }));
    expect(tool.open).toBe(true);
    tool.open = false;
    fireEvent(tool, new Event("toggle"));
    await waitFor(() => expect(tool.open).toBe(false));
    rerender(
      <AIChatTracePanel {...props} traces={[{ ...trace, duration_ms: 300 }]} />,
    );
    expect(tool.open).toBe(false);
  });
  it("labels legacy records accurately", () => {
    render(<TraceRun run={run} defaultOpen />);
    expect(screen.getByText(/旧记录未保存具体条目和正文/)).toBeTruthy();
  });
  it("preserves order and separates phases, notes, rejections and proposals", () => {
    const calls = [
      run,
      run,
      { ...run, phase: "prefetch" as const },
      { ...run, progress_text: "现在核对引用" },
      { ...run, status: "rejected" as const },
      { ...run, tool_name: "propose_file_change" },
      { ...run, tool_name: "propose_file_change" },
    ];
    const groups = groupToolRuns(calls);
    expect(groups.map((g) => g.runs.length)).toEqual([2, 1, 1, 1, 1, 1]);
    expect(groups.flatMap((g) => g.runs)).toEqual(calls);
  });
  it("keeps the turn call count and exposes concrete results in each grouped call", () => {
    const { container } = render(
      <InlineAgentExecution
        running
        toolRuns={[
          run,
          {
            ...run,
            result_summary: {
              returned_chars: 600,
              offset: 600,
              total_chars: 1800,
            },
          },
        ]}
        onOpenActivity={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: /2 个工具调用/ })).toBeTruthy();
    const group = container.querySelector(
      ".ai-tool-group",
    ) as HTMLDetailsElement;
    expect(group.open).toBe(false);
    fireEvent.click(group.querySelector("summary")!);
    const tools =
      container.querySelectorAll<HTMLDetailsElement>(".ai-tool-run");
    fireEvent.click(tools[0].querySelector("summary")!);
    expect(tools[0].textContent).toContain("600 字符");
    expect(tools[0].textContent).toContain("仍有正文可继续读取");
    expect(tools[1].textContent).not.toContain("仍有正文可继续读取");
    expect(
      tools[0].querySelector<HTMLDetailsElement>(".ai-tool-technical")?.open,
    ).toBe(false);
  });
  it("uses the same groups in Activity without losing pending text", () => {
    const trace: AIAgentTrace = {
      id: "stream-task",
      status: "running",
      preset_id: "general",
      steps: 1,
      tool_runs: [run, run],
      pending_text: "**正在核对**",
    };
    const { container } = render(
      <AIChatTracePanel
        open
        onClose={vi.fn()}
        preset={null}
        traces={[trace]}
        busy
        runningTaskId="task"
      />,
    );
    fireEvent.click(container.querySelector(".ai-trace-step > summary")!);
    expect(container.querySelectorAll(".ai-tool-group")).toHaveLength(1);
    expect(screen.queryByText("等待 Agent 返回")).toBeNull();
    expect(container.querySelectorAll(".ai-tool-run")).toHaveLength(2);
  });
  it("keeps rejected calls distinct and labels proposals as awaiting confirmation", () => {
    const { container } = render(
      <InlineAgentExecution
        running
        toolRuns={[
          {
            ...run,
            status: "rejected",
            result_summary: { error: "文件不存在" },
          },
          {
            ...run,
            tool_name: "propose_file_change",
            result_summary: {
              change_id: "c1",
              added: 2,
              removed: 0,
              approval: "pending_user",
            },
          },
        ]}
        onOpenActivity={vi.fn()}
      />,
    );
    expect(container.querySelector(".ai-tool-group")).toBeNull();
    expect(container.textContent).toContain("未执行：文件不存在");
    expect(container.textContent).toContain("等待你确认");
    expect(container.textContent).not.toContain("已写入");
  });
  it("uses a light color scheme for Aime and removes its preset when switching modes", () => {
    applyThemeMode("aime");
    expect(document.documentElement.dataset.themeMode).toBe("aime");
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(document.documentElement.style.colorScheme).toBe("light");
    applyThemeMode("dark");
    expect(document.documentElement.dataset.themeMode).toBe("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
  });
});
