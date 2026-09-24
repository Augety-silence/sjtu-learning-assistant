// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { AIChatView } from "@/components/AIChatView";
import {
  createAiChatSession,
  getAiChatSession,
  getAiChatSessions,
  getSettings,
  ingestAiAttachment,
  pickAiAttachment,
  sendAiChatMessage,
  updateSettings,
} from "@/lib/api";
import { cleanup, fireEvent, render, screen, waitFor } from "@/test/render";

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

const defaultSettings = {
  archive_root_ready: true,
  archive_root: "/tmp/archive",
  auto_download_current_term: true,
  organize_by_category: true,
  mail_account: "",
  canvas_token_saved: false,
  mail_password_saved: false,
  cloud_token_saved: false,
  credential_status_error: null,
  ai_enabled: true,
  ai_base_url: "https://example.test",
  ai_model: "deepseek-chat",
  ai_key_saved: true,
  ai_chat_send_shortcut: "enter" as const,
  ai_reply_language: "auto" as const,
  ai_attachment_context_budget: "balanced" as const,
  ai_auto_open_activity: true,
  ai_code_line_numbers: false,
  theme_mode: "system" as const,
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
  getSettings: vi.fn(async () => defaultSettings),
  updateSettings: vi.fn(async (change) => ({ ...defaultSettings, ...change })),
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
  pickAiAttachment: vi.fn(async () => ({
    cancelled: false,
    attachment: {
      id: 7,
      name: "复习提纲.txt",
      size: 128,
      sha256: "a".repeat(64),
      status: "local",
      cloud_ready: false,
      summary: "机器学习复习重点",
      tags: ["txt", "机器学习"],
      text_status: "ready",
    },
  })),
  ingestAiAttachment: vi.fn(async () => ({
    id: 8,
    name: "拖入笔记.md",
    size: 256,
    sha256: "b".repeat(64),
    status: "local",
    cloud_ready: false,
    summary: "拖入的课程笔记",
    tags: ["md"],
    text_status: "ready",
  })),
  revealAiAttachment: vi.fn(async () => ({ status: "revealed" })),
  deleteAiChatSession: vi.fn(async () => ({ deleted: true })),
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  window.localStorage.clear();
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
    expect(
      screen.getByRole("button", { name: "当前 Agent：学习数据 Agent" }),
    ).toBeTruthy();
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
    fireEvent.click(screen.getByRole("button", { name: "推理强度：标准" }));
    fireEvent.click(screen.getByRole("option", { name: /深度/ }));
    const input = screen.getByRole("textbox", { name: "输入问题" });
    fireEvent.change(input, { target: { value: "最近有什么消息？" } });
    fireEvent.keyDown(input, { key: "Enter" });

    expect(await screen.findByText("查看推理过程")).toBeTruthy();
    const animatedReply = document.querySelector(
      '[data-motion-entry="assistant"]',
    );
    expect(animatedReply?.textContent).toContain("优先事项");
    expect(document.querySelector('[data-motion-entry="user"]')).toBeNull();
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

  it("picks, removes and sends local attachment chips", async () => {
    render(<AIChatView onBack={vi.fn()} />);
    const input = await screen.findByRole("textbox", { name: "输入问题" });
    const attach = screen.getByRole("button", { name: "添加本地附件" });

    fireEvent.click(attach);
    expect(await screen.findByText("复习提纲.txt")).toBeTruthy();
    expect(pickAiAttachment).toHaveBeenCalledTimes(1);
    fireEvent.click(
      screen.getByRole("button", { name: "移除附件 复习提纲.txt" }),
    );
    expect(screen.queryByText("复习提纲.txt")).toBeNull();

    fireEvent.click(attach);
    await screen.findByText("复习提纲.txt");
    fireEvent.change(input, { target: { value: "总结附件" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(await screen.findByText("优先事项")).toBeTruthy();
    expect(sendAiChatMessage).toHaveBeenCalledWith(
      "session-1",
      "总结附件",
      "auto",
      "standard",
      "general",
      [7],
    );
    expect(screen.queryByLabelText("待发送附件")).toBeNull();
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
    await screen.findByRole("button", { name: "Agent：学习数据 Agent" });
    fireEvent.click(
      screen.getByRole("button", { name: "Agent：学习数据 Agent" }),
    );
    fireEvent.click(screen.getByRole("option", { name: /复习规划 Agent/ }));
    fireEvent.click(screen.getByRole("button", { name: "新对话" }));
    expect(createAiChatSession).toHaveBeenLastCalledWith(
      "auto",
      "standard",
      "review-planner",
    );
  });

  it("keeps the prompt and compact controls on separate semantic rows", async () => {
    const { container } = render(<AIChatView onBack={vi.fn()} />);
    const input = await screen.findByRole("textbox", { name: "输入问题" });
    const toolbar = screen.getByRole("toolbar", { name: "Agent 与输入工具" });
    const box = container.querySelector(".ai-composer-box");
    expect(box?.children[0]).toBe(input);
    expect(box?.children[1]).toBe(toolbar);
  });

  it("keeps the textarea visually delegated to the rounded composer focus ring", async () => {
    const { container } = render(<AIChatView onBack={vi.fn()} />);
    const input = await screen.findByRole("textbox", { name: "输入问题" });
    const box = container.querySelector(".ai-composer-box");
    expect(input.classList.contains("ai-composer-input")).toBe(true);
    expect(box?.contains(input)).toBe(true);
  });

  it("ingests dropped desktop files and sends a default attachment intent", async () => {
    render(<AIChatView onBack={vi.fn()} />);
    await screen.findByRole("textbox", { name: "输入问题" });
    const workspace = screen.getByRole("main");
    const file = new File(["# 复习"], "拖入笔记.md", { type: "text/markdown" });
    Object.defineProperty(file, "path", { value: "/tmp/拖入笔记.md" });
    const dataTransfer = {
      types: ["Files"],
      files: [file],
      dropEffect: "none",
    };

    fireEvent.dragEnter(workspace, { dataTransfer });
    expect(screen.getByText("将文件拖拽到这里")).toBeTruthy();
    expect(screen.getByText(/单文件最大 2GB/)).toBeTruthy();
    fireEvent.drop(workspace, { dataTransfer });

    expect(await screen.findByText("拖入笔记.md")).toBeTruthy();
    expect(ingestAiAttachment).toHaveBeenCalledWith("/tmp/拖入笔记.md");
    fireEvent.click(screen.getByRole("button", { name: "发送消息" }));
    await waitFor(() =>
      expect(sendAiChatMessage).toHaveBeenCalledWith(
        "session-1",
        "请总结并分析这些附件。",
        "auto",
        "standard",
        "general",
        [8],
      ),
    );
  });

  it("explains how to recover when a dropped file has no WebView path", async () => {
    render(<AIChatView onBack={vi.fn()} />);
    await screen.findByRole("textbox", { name: "输入问题" });
    const file = new File(["notes"], "notes.txt", { type: "text/plain" });
    fireEvent.drop(screen.getByRole("main"), {
      dataTransfer: { types: ["Files"], files: [file], dropEffect: "none" },
    });
    expect(await screen.findByText(/请使用回形针按钮选择文件/)).toBeTruthy();
    expect(ingestAiAttachment).not.toHaveBeenCalled();
  });

  it("persists personalization and applies the Cmd+Enter shortcut", async () => {
    render(<AIChatView onBack={vi.fn()} />);
    const input = await screen.findByRole("textbox", { name: "输入问题" });
    fireEvent.click(screen.getByRole("button", { name: "个性化设置" }));
    expect(
      screen.getByRole("dialog", { name: "AI Chat 个性化设置" }),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("radio", { name: "⌘ + Enter 发送" }));

    await screen.findByText("已保存");
    expect(updateSettings).toHaveBeenCalledWith({
      ai_chat_send_shortcut: "cmd_enter",
    });
    fireEvent.change(input, { target: { value: "保留换行" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(sendAiChatMessage).not.toHaveBeenCalled();
    fireEvent.keyDown(input, { key: "Enter", metaKey: true });
    await waitFor(() => expect(sendAiChatMessage).toHaveBeenCalledTimes(1));
    expect(
      screen
        .getByRole("button", { name: "切换 Activity 检索轨迹" })
        .getAttribute("aria-expanded"),
    ).toBe("true");
  });

  it("closes personalization with Escape and restores trigger focus", async () => {
    render(<AIChatView onBack={vi.fn()} />);
    const trigger = await screen.findByRole("button", { name: "个性化设置" });
    fireEvent.click(trigger);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(
      screen.queryByRole("dialog", { name: "AI Chat 个性化设置" }),
    ).toBeNull();
    await waitFor(() => expect(document.activeElement).toBe(trigger));
    expect(getSettings).toHaveBeenCalled();
  });

  it("keeps pane resizing within an accessible keyboard control", async () => {
    render(<AIChatView onBack={vi.fn()} />);
    await screen.findByRole("separator", { name: "调整历史会话栏宽度" });
    const leftResizer = screen.getByRole("separator", {
      name: "调整历史会话栏宽度",
    });
    expect(leftResizer.getAttribute("aria-valuenow")).toBe("240");
    fireEvent.keyDown(leftResizer, { key: "ArrowRight" });
    expect(leftResizer.getAttribute("aria-valuenow")).toBe("248");
  });
});
