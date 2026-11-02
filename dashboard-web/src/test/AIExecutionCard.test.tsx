// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { AIChatMessageBubble } from "@/components/AIChatMessageBubble";
import {
  formatDuration,
  InlineAgentExecution,
  parseStartedAt,
  toolArgumentHint,
  toolResultLabel,
  turnSummaryText,
} from "@/components/AIExecutionCard";
import type { AIAgentTrace, AIToolRun } from "@/lib/types";
import { cleanup, fireEvent, render, screen } from "@/test/render";

const runs: AIToolRun[] = [
  {
    tool_name: "search_course_files",
    phase: "prefetch",
    status: "ok",
    arguments_summary: { query_length: 3, course_filtered: true, limit: 20 },
    result_summary: { status: "ok", count: 4 },
    duration_ms: 230,
  },
  {
    tool_name: "read_ai_attachment_text",
    phase: "model",
    status: "rejected",
    arguments_summary: {},
    result_summary: { status: "error", error: "附件不属于本轮消息。" },
  },
];

const trace: AIAgentTrace = {
  id: "trace-1",
  preset_id: "general",
  status: "completed",
  steps: 3,
  tool_runs: runs,
  duration_ms: 113_000,
};

afterEach(() => cleanup());

describe("per-turn execution details", () => {
  it("formats durations and summaries like the reference turn header", () => {
    expect(formatDuration(400)).toBe("不到 1 秒");
    expect(formatDuration(12_400)).toBe("12 秒");
    expect(formatDuration(113_000)).toBe("1 分 53 秒");
    expect(formatDuration(120_000)).toBe("2 分");
    expect(formatDuration(3_720_000)).toBe("1 小时 2 分");
    expect(formatDuration(null)).toBeNull();
    expect(
      turnSummaryText({
        running: false,
        failed: false,
        durationMs: 113_000,
        toolCount: 11,
        rejected: 0,
      }),
    ).toBe("执行了 1 分 53 秒 · 11 个工具调用");
    expect(
      turnSummaryText({
        running: true,
        failed: false,
        durationMs: 4_000,
        toolCount: 0,
        rejected: 0,
      }),
    ).toBe("正在执行 · 已用 4 秒");
    expect(
      turnSummaryText({
        running: false,
        failed: true,
        durationMs: null,
        toolCount: 2,
        rejected: 1,
      }),
    ).toBe("未完成 · 2 个工具调用 · 1 项未执行");
  });

  it("parses zoneless backend timestamps as UTC instead of local time", () => {
    const now = Date.parse("2026-11-02T12:46:10Z");
    // 北京时间 20:46 运行的任务，后端旧格式给出无时区 UTC 时间。
    expect(parseStartedAt("2026-11-02T12:46:00.284896", now)).toBe(
      Date.parse("2026-11-02T12:46:00.284Z"),
    );
    expect(parseStartedAt("2026-11-02 12:46:00", now)).toBe(
      Date.parse("2026-11-02T12:46:00Z"),
    );
    expect(parseStartedAt("2026-11-02T12:46:00+00:00", now)).toBe(
      Date.parse("2026-11-02T12:46:00Z"),
    );
    // 明显在未来的开始时间视为无效，不显示错误计时。
    expect(parseStartedAt("2026-11-02T20:46:00", now)).toBeNull();
    expect(parseStartedAt("not a date", now)).toBeNull();
  });

  it("describes reads by page range and never shows raw queries", () => {
    expect(
      toolResultLabel({
        ...runs[0],
        tool_name: "read_course_file_text",
        result_summary: {
          status: "ok",
          content_status: "ready",
          start_page: 3,
          end_page: 3,
          unit_count: 20,
          unit_label: "页",
        },
      }),
    ).toBe("已读第 3 页 / 共 20 页");
    expect(
      toolResultLabel({
        ...runs[0],
        tool_name: "read_course_file_text",
        result_summary: { status: "ok", content_status: "unavailable" },
      }),
    ).toBe("文件未下载到本机");
    expect(toolArgumentHint(runs[0])).toBe("按关键词 · 限定课程");
    expect(
      toolArgumentHint({
        ...runs[0],
        arguments_summary: { start_page: 5, hours: 168 },
      }),
    ).toBe("从第 5 页起 · 未来 7 天");
  });

  it("collapses a finished turn to one line and expands into ordered steps", () => {
    const openActivity = vi.fn();
    render(
      <InlineAgentExecution trace={trace} onOpenActivity={openActivity} />,
    );
    const toggle = screen.getByRole("button", {
      name: "执行了 1 分 53 秒 · 2 个工具调用 · 1 项未执行",
    });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByText("检索课程文件")).toBeNull();

    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    const detail = document.getElementById(
      toggle.getAttribute("aria-controls") ?? "",
    );
    expect(detail?.querySelectorAll("li")).toHaveLength(2);
    expect(detail?.textContent).toContain("检索课程文件按关键词 · 限定课程");
    expect(detail?.textContent).toContain("命中 4 项 · 0.2 秒");
    expect(detail?.textContent).toContain("未执行：附件不属于本轮消息。");
    expect(detail?.textContent).toContain("模型请求 3 次");
    fireEvent.click(
      screen.getByRole("button", { name: /在 Activity 查看完整记录/ }),
    );
    expect(openActivity).toHaveBeenCalledOnce();
  });

  it("starts expanded while running and lets the user collapse it", () => {
    render(
      <InlineAgentExecution
        running
        trace={{ ...trace, status: "running", duration_ms: null }}
        startedAt={Date.now() - 5_000}
        onOpenActivity={vi.fn()}
      />,
    );
    const toggle = screen.getByRole("button", {
      name: /正在执行 · 已用 5 秒 · 2 个工具调用/,
    });
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("正在规划下一步…")).toBeTruthy();
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByText("正在规划下一步…")).toBeNull();
  });
});

describe("user message bubble", () => {
  const baseProps = {
    agentName: "综合助理",
    onOpenActivity: vi.fn(),
    onRevealAttachment: vi.fn(),
    showCodeLineNumbers: false,
  };

  it("folds a long pasted message behind an accessible toggle", () => {
    const content = `${"这是一段很长的用户粘贴内容。".repeat(60)}\n结尾`;
    render(
      <AIChatMessageBubble
        {...baseProps}
        message={{ id: "u1", role: "user", content }}
      />,
    );
    const toggle = screen.getByRole("button", {
      name: `展开全文（共 ${content.length} 字）`,
    });
    const region = document.getElementById(
      toggle.getAttribute("aria-controls") ?? "",
    );
    expect(region?.className).toContain("is-collapsed");
    expect(region?.textContent).toContain("结尾");
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(region?.className).toContain("is-expanded");
    expect(screen.getByRole("button", { name: "收起" })).toBe(toggle);
  });

  it("keeps short or few-line messages unfolded", () => {
    render(
      <AIChatMessageBubble
        {...baseProps}
        message={{
          id: "u2",
          role: "user",
          content: "第一行\n第二行\n**重点**",
        }}
      />,
    );
    expect(screen.queryByRole("button", { name: /展开全文/ })).toBeNull();
    expect(screen.getByText("重点").tagName).toBe("STRONG");
  });
});
