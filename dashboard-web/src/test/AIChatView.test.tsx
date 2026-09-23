// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { AIChatView } from "@/components/AIChatView";
import {
  createAiChatSession,
  getAiChatSession,
  getAiChatSessions,
  sendAiChatMessage,
} from "@/lib/api";
import { cleanup, fireEvent, render, screen } from "@/test/render";

const preset = {
  id: "general",
  name: "学习数据 Agent",
  description: "检索本地同步的课程与学习资料",
  allowed_tools: [
    "list_courses",
    "search_course_files",
    "list_course_files",
    "get_deadlines",
    "search_messages",
    "get_message_detail",
    "get_material_tree",
  ],
  is_default: true,
};

const trace = {
  id: "trace-1",
  preset_id: "general",
  status: "completed",
  steps: 2,
  created_at: "2026-09-23T14:20:00+08:00",
  tool_runs: [
    {
      tool_name: "get_deadlines",
      phase: "prefetch" as const,
      status: "ok" as const,
      arguments_summary: { hours: 168, limit: 30 },
      result_summary: { count: 3 },
    },
  ],
};

vi.mock("@/lib/api", () => ({
  getAiPresets: vi.fn(async () => ({
    default_preset_id: "general",
    items: [preset],
  })),
  getAiChatSessions: vi.fn(async () => ({ items: [] })),
  getAiChatSession: vi.fn(),
  createAiChatSession: vi.fn(async () => ({
    id: "session-1",
    title: "新对话",
    model: "auto",
    thinking_depth: "standard",
    preset_id: "general",
    messages: [],
    traces: [],
  })),
  sendAiChatMessage: vi.fn(async () => ({
    session: {
      id: "session-1",
      title: "最近一周待办",
      model: "auto",
      thinking_depth: "deep",
      preset_id: "general",
    },
    trace,
    user_message: {
      id: "1",
      role: "user",
      content: "最近有什么消息？",
      trace_id: "trace-1",
      tool_runs: trace.tool_runs,
    },
    assistant_message: {
      id: "2",
      role: "assistant",
      content: "## 优先事项\n\n- 完成明天截止的作业",
      reasoning_content: "先比较截止时间。",
      model: "deepseek-reasoner",
      trace_id: "trace-1",
      tool_runs: trace.tool_runs,
    },
  })),
  deleteAiChatSession: vi.fn(async () => ({ deleted: true })),
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("AIChatView", () => {
  it("loads presets and presents the workspace as a learning-data agent", async () => {
    render(<AIChatView onBack={vi.fn()} />);

    expect(
      await screen.findByRole("heading", {
        name: "今天想从学习数据里查什么？",
      }),
    ).toBeTruthy();
    expect(createAiChatSession).toHaveBeenCalledWith(
      "auto",
      "standard",
      "general",
    );
    expect(screen.getByRole("radiogroup", { name: "Agent 预设" })).toBeTruthy();
    for (const capability of [
      "课程",
      "课程文件",
      "截止日期",
      "消息",
      "资料树",
    ]) {
      expect(screen.getByText(capability)).toBeTruthy();
    }
    expect(
      screen.getByText("工具调用会显示在这里", { exact: false }),
    ).toBeTruthy();
  });

  it("sends model, depth and preset and renders the returned trace", async () => {
    render(<AIChatView onBack={vi.fn()} />);
    await screen.findByRole("textbox", { name: "输入问题" });
    fireEvent.change(screen.getByRole("combobox", { name: "思考深度" }), {
      target: { value: "deep" },
    });
    const input = screen.getByRole("textbox", { name: "输入问题" });
    fireEvent.change(input, { target: { value: "最近有什么消息？" } });
    fireEvent.keyDown(input, { key: "Enter" });

    expect(await screen.findByText("查看推理过程")).toBeTruthy();
    expect(sendAiChatMessage).toHaveBeenCalledWith(
      "session-1",
      "最近有什么消息？",
      "auto",
      "deep",
      "general",
    );
    expect(screen.getByText("查询截止日期")).toBeTruthy();
    expect(screen.getByText("命中 3 项")).toBeTruthy();
    expect(screen.getByText("预检索")).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "复制消息" })).toHaveLength(2);
  });

  it("expands a tool call to show safe arguments and result summaries", async () => {
    vi.mocked(getAiChatSessions).mockResolvedValueOnce({
      items: [
        {
          id: "existing",
          title: "复习计划",
          model: "deepseek-chat",
          thinking_depth: "standard",
          preset_id: "general",
        },
      ],
    });
    vi.mocked(getAiChatSession).mockResolvedValueOnce({
      id: "existing",
      title: "复习计划",
      model: "deepseek-chat",
      thinking_depth: "standard",
      preset_id: "general",
      messages: [
        {
          id: "3",
          role: "assistant",
          content: "历史回答",
          trace_id: "trace-1",
          tool_runs: trace.tool_runs,
        },
      ],
      traces: [trace],
    });

    render(<AIChatView onBack={vi.fn()} />);
    expect(await screen.findByText("历史回答")).toBeTruthy();
    fireEvent.click(screen.getByText("查询截止日期"));
    expect(screen.getByText("安全参数")).toBeTruthy();
    expect(screen.getByText("结果摘要")).toBeTruthy();
  });

  it("switches the active preset before creating a new conversation", async () => {
    const reviewPreset = {
      ...preset,
      id: "review-planner",
      name: "复习规划 Agent",
      is_default: false,
    };
    const { getAiPresets } = await import("@/lib/api");
    vi.mocked(getAiPresets).mockResolvedValueOnce({
      default_preset_id: "general",
      items: [preset, reviewPreset],
    });

    render(<AIChatView onBack={vi.fn()} />);
    await screen.findByRole("radio", { name: /复习规划 Agent/ });
    fireEvent.click(screen.getByRole("radio", { name: /复习规划 Agent/ }));
    fireEvent.click(screen.getByRole("button", { name: "新对话" }));
    expect(createAiChatSession).toHaveBeenLastCalledWith(
      "auto",
      "standard",
      "review-planner",
    );
  });
});
