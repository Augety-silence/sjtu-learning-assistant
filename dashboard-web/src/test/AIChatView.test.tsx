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
  credential_storage_name: "Windows Credential Manager",
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
  AI_CHAT_STREAM_EVENT: "sjtu-ai-chat-stream",
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
    expect(screen.getByRole("button", { name: "模型：自动" }).textContent).toBe(
      "模型：自动",
    );
    expect(
      screen.getByRole("button", { name: "推理强度：标准" }).textContent,
    ).toBe("推理：标准");
    expect(
      screen.getByText("简洁时间线展示检索与整理进度", { exact: false }),
    ).toBeTruthy();
  });

  it("opens a recent session selected from the workbench sidebar", async () => {
    window.localStorage.setItem("ai-chat-pending-session", "session-target");
    vi.mocked(getAiChatSessions).mockResolvedValueOnce({
      items: [
        {
          id: "session-recent",
          title: "最近对话",
          model: "auto",
          thinking_depth: "standard",
          preset_id: "general",
        },
      ],
    });
    vi.mocked(getAiChatSession).mockResolvedValueOnce({
      id: "session-target",
      title: "指定对话",
      model: "auto",
      thinking_depth: "standard",
      preset_id: "general",
      messages: [],
      traces: [],
    });

    render(<AIChatView onBack={vi.fn()} />);

    await waitFor(() =>
      expect(getAiChatSession).toHaveBeenCalledWith("session-target"),
    );
    expect(window.localStorage.getItem("ai-chat-pending-session")).toBeNull();
  });

  it("keeps the last clicked session when earlier loads resolve later", async () => {
    const session = (id: string, title: string) => ({
      id,
      title,
      model: "auto",
      thinking_depth: "standard" as const,
      preset_id: "general",
      messages: [],
      traces: [],
    });
    vi.mocked(getAiChatSessions).mockResolvedValueOnce({
      items: [
        {
          id: "session-a",
          title: "会话 A",
          model: "auto",
          thinking_depth: "standard",
          preset_id: "general",
        },
        {
          id: "session-b",
          title: "会话 B",
          model: "auto",
          thinking_depth: "standard",
          preset_id: "general",
        },
      ],
    });
    vi.mocked(getAiChatSession).mockResolvedValueOnce(
      session("session-a", "会话 A"),
    );
    let resolveA!: (value: ReturnType<typeof session>) => void;
    let resolveB!: (value: ReturnType<typeof session>) => void;
    vi.mocked(getAiChatSession)
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveB = resolve;
          }),
      )
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveA = resolve;
          }),
      );
    render(<AIChatView onBack={vi.fn()} />);
    await screen.findByRole("button", { name: "会话 A" });
    fireEvent.click(screen.getByRole("button", { name: "会话 B" }));
    fireEvent.click(screen.getByRole("button", { name: "会话 A" }));
    resolveA(session("session-a", "会话 A"));
    await waitFor(() =>
      expect(
        document.querySelector(".ai-history-item.active button")?.textContent,
      ).toBe("会话 A"),
    );
    resolveB(session("session-b", "会话 B"));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(
      screen
        .getByRole("button", { name: "会话 A" })
        .getAttribute("aria-current"),
    ).toBe("true");
  });

  it("does not auto-send a pending prompt into a fallback session", async () => {
    window.localStorage.setItem("ai-chat-pending-session", "missing-session");
    window.localStorage.setItem("ai-chat-pending-prompt", "继续刚才的对话");
    window.localStorage.setItem("ai-chat-auto-submit", "true");
    vi.mocked(getAiChatSessions).mockResolvedValueOnce({
      items: [
        {
          id: "session-fallback",
          title: "最近对话",
          model: "auto",
          thinking_depth: "standard",
          preset_id: "general",
        },
      ],
    });
    vi.mocked(getAiChatSession)
      .mockRejectedValueOnce(new Error("会话不存在"))
      .mockResolvedValueOnce({
        id: "session-fallback",
        title: "最近对话",
        model: "auto",
        thinking_depth: "standard",
        preset_id: "general",
        messages: [],
        traces: [],
      });

    render(<AIChatView onBack={vi.fn()} />);
    expect(
      await screen.findByText(
        "指定的对话无法读取，已打开最近对话；请确认后再发送消息。",
        { exact: false },
      ),
    ).toBeTruthy();
    expect(screen.getByRole("textbox", { name: "输入问题" })).toHaveProperty(
      "value",
      "继续刚才的对话",
    );
    expect(sendAiChatMessage).not.toHaveBeenCalled();
  });

  it("creates a fresh session when requested from the sidebar", async () => {
    window.localStorage.setItem("ai-chat-new-session", "true");
    vi.mocked(getAiChatSessions).mockResolvedValueOnce({
      items: [
        {
          id: "session-recent",
          title: "最近对话",
          model: "auto",
          thinking_depth: "standard",
          preset_id: "general",
        },
      ],
    });

    render(<AIChatView onBack={vi.fn()} />);

    await waitFor(() =>
      expect(createAiChatSession).toHaveBeenCalledWith(
        "auto",
        "standard",
        "general",
      ),
    );
    expect(window.localStorage.getItem("ai-chat-new-session")).toBeNull();
  });

  it("automatically sends a prompt submitted from the workbench sidebar", async () => {
    window.localStorage.setItem("ai-chat-pending-prompt", "帮我梳理本周作业");
    window.localStorage.setItem("ai-chat-auto-submit", "true");

    render(<AIChatView onBack={vi.fn()} />);

    await waitFor(() =>
      expect(sendAiChatMessage).toHaveBeenCalledWith(
        "session-1",
        "帮我梳理本周作业",
        "auto",
        "standard",
        "general",
        undefined,
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      ),
    );
    expect(window.localStorage.getItem("ai-chat-auto-submit")).toBeNull();
  });

  it("requests soft cancellation from the composer while a task is running", async () => {
    let signal: AbortSignal | undefined;
    vi.mocked(sendAiChatMessage).mockImplementationOnce(
      (_session, _content, _model, _depth, _preset, _attachments, options) => {
        signal = options?.signal;
        return new Promise(() => undefined);
      },
    );
    render(<AIChatView onBack={vi.fn()} />);
    const input = await screen.findByRole("textbox", { name: "输入问题" });
    fireEvent.change(input, { target: { value: "检查本周任务" } });
    fireEvent.keyDown(input, { key: "Enter" });
    const stop = await screen.findByRole("button", { name: "停止本次任务" });
    expect(signal?.aborted).toBe(false);
    fireEvent.click(stop);
    expect(signal?.aborted).toBe(true);
    expect(
      screen.getByRole("button", { name: "正在停止本次任务" }),
    ).toHaveProperty("disabled", true);
    expect(screen.getByText("正在停止")).toBeTruthy();
  });

  it("shows tool activity as soon as a streaming tool event arrives", async () => {
    vi.mocked(sendAiChatMessage).mockImplementationOnce(
      () => new Promise(() => undefined),
    );
    render(<AIChatView onBack={vi.fn()} />);
    const input = await screen.findByRole("textbox", { name: "输入问题" });
    fireEvent.change(input, { target: { value: "查课程截止日期" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await screen.findByRole("button", { name: "停止本次任务" });
    window.dispatchEvent(
      new CustomEvent("sjtu-ai-chat-stream", {
        detail: {
          type: "tool",
          session_id: "session-1",
          task_id: "task-live",
          run: {
            tool_name: "get_deadlines",
            phase: "prefetch",
            status: "ok",
            arguments_summary: { hours: 168 },
            result_summary: { count: 2 },
          },
        },
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: /Activity/ }));
    expect(await screen.findByText(/调用 1 次/)).toBeTruthy();
  });

  it("supports arrow navigation, Escape, and focus return in compact choices", async () => {
    render(<AIChatView onBack={vi.fn()} />);
    const trigger = await screen.findByRole("button", { name: "模型：自动" });

    fireEvent.keyDown(trigger, { key: "ArrowDown" });
    const selected = await screen.findByRole("option", { name: /^自动/ });
    await waitFor(() => expect(document.activeElement).toBe(selected));

    fireEvent.keyDown(selected, { key: "ArrowDown" });
    const next = screen.getByRole("option", { name: /^DeepSeek Flash/ });
    expect(document.activeElement).toBe(next);

    fireEvent.keyDown(next, { key: "Escape" });
    await waitFor(() => {
      expect(screen.queryByRole("listbox", { name: "模型" })).toBeNull();
      expect(document.activeElement).toBe(trigger);
    });
  });

  it("sends model, depth and preset and renders the returned trace", async () => {
    render(<AIChatView onBack={vi.fn()} />);
    await screen.findByRole("textbox", { name: "输入问题" });
    fireEvent.click(screen.getByRole("button", { name: "推理强度：标准" }));
    fireEvent.click(screen.getByRole("option", { name: /深度/ }));
    const input = screen.getByRole("textbox", { name: "输入问题" });
    fireEvent.change(input, { target: { value: "最近有什么消息？" } });
    fireEvent.keyDown(input, { key: "Enter" });

    expect(await screen.findByText("查看过程")).toBeTruthy();
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
      undefined,
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(screen.getAllByText("检索截止日期").length).toBeGreaterThan(0);
    expect(screen.getAllByText("命中 3 项").length).toBeGreaterThan(0);
    expect(screen.getByText(/已完成 · 1 项操作 · 命中 3 项/)).toBeTruthy();
    expect(screen.getByRole("article", { name: "你的消息" })).toBeTruthy();
    expect(
      document.querySelector(".ai-message-user .ai-message-avatar"),
    ).toBeNull();
    expect(screen.queryByText("你", { exact: true })).toBeNull();
    expect(screen.getAllByText("预检索").length).toBeGreaterThan(0);
    expect(screen.queryByText("查看推理过程")).toBeNull();
    expect(screen.getAllByRole("button", { name: "复制消息" })).toHaveLength(2);
  });

  it("keeps generation feedback directly in the conversation flow", async () => {
    let resolveMessage:
      | ((value: Awaited<ReturnType<typeof sendAiChatMessage>>) => void)
      | undefined;
    vi.mocked(sendAiChatMessage).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveMessage = resolve;
        }),
    );

    const { container } = render(<AIChatView onBack={vi.fn()} />);
    const input = await screen.findByRole("textbox", { name: "输入问题" });
    fireEvent.change(input, { target: { value: "检索本学期课程" } });
    fireEvent.keyDown(input, { key: "Enter" });

    expect(
      await screen.findByText("请求已发送，正在等待 Agent 返回"),
    ).toBeTruthy();
    expect(
      container.querySelector(".ai-chat-messages > .ai-inline-execution"),
    ).toBeTruthy();
    expect(screen.queryByText("正在规划检索步骤")).toBeNull();

    resolveMessage?.({
      session: {
        id: "session-1",
        title: "最近一周待办",
        model: "auto",
        thinking_depth: "standard",
        preset_id: "general",
      },
      trace,
      user_message: { id: "1", role: "user", content: "检索本学期课程" },
      assistant_message: { id: "2", role: "assistant", content: "检索完成" },
    });
    expect(await screen.findByText("检索完成")).toBeTruthy();
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
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
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

    const { container } = render(<AIChatView onBack={vi.fn()} />);
    expect(await screen.findByText("历史回答")).toBeTruthy();
    const inlineExecution = container.querySelector(
      ".ai-message-assistant .ai-execution-card > summary",
    );
    expect(inlineExecution).toBeTruthy();
    expect(
      (inlineExecution?.parentElement as HTMLDetailsElement | null)?.open,
    ).toBe(false);
    fireEvent.click(inlineExecution as HTMLElement);
    expect(
      (inlineExecution?.parentElement as HTMLDetailsElement | null)?.open,
    ).toBe(true);
    const inlineTool = container.querySelector(
      ".ai-message-assistant .ai-execution-detail .ai-tool-run > summary",
    );
    expect(inlineTool).toBeTruthy();
    expect((inlineTool?.parentElement as HTMLDetailsElement | null)?.open).toBe(
      false,
    );
    fireEvent.click(inlineTool as HTMLElement);
    expect((inlineTool?.parentElement as HTMLDetailsElement | null)?.open).toBe(
      true,
    );
    expect(inlineTool?.parentElement?.textContent).toContain("安全参数");
    expect(inlineTool?.parentElement?.textContent).toContain("结果摘要");
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

  it("grows and caps the composer, then returns to one line when cleared", async () => {
    render(<AIChatView onBack={vi.fn()} />);
    const input = (await screen.findByRole("textbox", {
      name: "输入问题",
    })) as HTMLTextAreaElement;
    let measuredHeight = 42;
    Object.defineProperty(input, "scrollHeight", {
      configurable: true,
      get: () => measuredHeight,
    });

    expect(input.style.height).toBe("42px");
    expect(input.style.overflowY).toBe("hidden");

    fireEvent.change(input, { target: { value: "单行问题" } });
    expect(input.style.height).toBe("42px");
    expect(input.style.overflowY).toBe("hidden");

    measuredHeight = 92;
    fireEvent.change(input, { target: { value: "第一行\n第二行\n第三行" } });
    expect(input.style.height).toBe("92px");
    expect(input.style.overflowY).toBe("hidden");

    measuredHeight = 240;
    fireEvent.change(input, { target: { value: "很多行\n".repeat(20) } });
    expect(input.style.height).toBe("160px");
    expect(input.style.overflowY).toBe("auto");

    fireEvent.change(input, { target: { value: "" } });
    expect(input.style.height).toBe("42px");
    expect(input.style.overflowY).toBe("hidden");
  });

  it("uses 32vh as the textarea cap in a short viewport", async () => {
    const originalInnerHeight = window.innerHeight;
    Object.defineProperty(window, "innerHeight", {
      configurable: true,
      value: 400,
    });

    try {
      render(<AIChatView onBack={vi.fn()} />);
      const input = (await screen.findByRole("textbox", {
        name: "输入问题",
      })) as HTMLTextAreaElement;
      Object.defineProperty(input, "scrollHeight", {
        configurable: true,
        value: 240,
      });

      fireEvent.change(input, {
        target: { value: "短窗口中的多行内容\n".repeat(20) },
      });
      expect(input.style.height).toBe("128px");
      expect(input.style.overflowY).toBe("auto");
    } finally {
      Object.defineProperty(window, "innerHeight", {
        configurable: true,
        value: originalInnerHeight,
      });
    }
  });

  it("keeps the user message compact without a detached avatar", async () => {
    render(<AIChatView onBack={vi.fn()} />);
    const input = await screen.findByRole("textbox", { name: "输入问题" });
    fireEvent.change(input, { target: { value: "检查消息布局" } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() => {
      expect((input as HTMLTextAreaElement).value).toBe("");
      expect((input as HTMLTextAreaElement).style.height).toBe("42px");
    });
    const article = await screen.findByRole("article", { name: "你的消息" });
    expect(article.querySelector(".ai-message-avatar")).toBeNull();
    expect(article.children[0].classList.contains("ai-message-body")).toBe(
      true,
    );
    expect(
      article.querySelector(
        ".ai-message-body > footer button[aria-label='复制消息']",
      ),
    ).toBeTruthy();
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
    expect(screen.getByText(/2GB 是摄取上限/)).toBeTruthy();
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
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      ),
    );
  });

  it("ingests native WebView drop paths without opening the file picker", async () => {
    render(<AIChatView onBack={vi.fn()} />);
    await screen.findByRole("textbox", { name: "输入问题" });
    const file = new File(["notes"], "notes.txt", { type: "text/plain" });
    fireEvent.drop(screen.getByRole("main"), {
      dataTransfer: { types: ["Files"], files: [file], dropEffect: "none" },
    });
    window.dispatchEvent(
      new CustomEvent("sjtu-native-file-drop", {
        detail: { paths: ["/tmp/拖入笔记.md"] },
      }),
    );
    expect(await screen.findByText("拖入笔记.md")).toBeTruthy();
    expect(ingestAiAttachment).toHaveBeenCalledWith("/tmp/拖入笔记.md");
    expect(pickAiAttachment).not.toHaveBeenCalled();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("explains how to add files when the native drop path event is missing", async () => {
    render(<AIChatView onBack={vi.fn()} />);
    await screen.findByRole("textbox", { name: "输入问题" });
    const file = new File(["notes"], "notes.txt", { type: "text/plain" });
    fireEvent.drop(screen.getByRole("main"), {
      dataTransfer: { types: ["Files"], files: [file], dropEffect: "none" },
    });
    expect(
      await screen.findByText(/桌面文件路径尚未就绪/, {}, { timeout: 1800 }),
    ).toBeTruthy();
  });

  it("persists personalization and applies the Cmd+Enter shortcut", async () => {
    render(<AIChatView onBack={vi.fn()} />);
    const input = await screen.findByRole("textbox", { name: "输入问题" });
    fireEvent.click(screen.getByRole("button", { name: "个性化设置" }));
    expect(
      screen.getByRole("dialog", { name: "AI 助手个性化设置" }),
    ).toBeTruthy();
    fireEvent.click(
      screen.getByRole("radio", { name: /(?:⌘|Ctrl) \+ Enter 发送/ }),
    );

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
    ).toBe("false");
  });

  it("keeps Activity non-modal and preserves composer focus and draft", async () => {
    const { container } = render(<AIChatView onBack={vi.fn()} />);
    const input = await screen.findByRole("textbox", { name: "输入问题" });
    fireEvent.change(input, { target: { value: "保持这段输入" } });
    input.focus();
    const activityToggle = screen.getByRole("button", {
      name: "切换 Activity 检索轨迹",
    });
    expect(activityToggle.getAttribute("aria-expanded")).toBe("false");
    fireEvent.pointerDown(activityToggle);
    fireEvent.click(activityToggle);

    await waitFor(() => expect(document.activeElement).toBe(input));
    expect((input as HTMLTextAreaElement).value).toBe("保持这段输入");
    expect(container.querySelector(".ai-activity-backdrop")).toBeNull();
    expect(
      screen.getByRole("complementary", { name: "Activity 检索轨迹" }),
    ).toBeTruthy();
    expect(input.hasAttribute("disabled")).toBe(false);

    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => {
      expect(activityToggle.getAttribute("aria-expanded")).toBe("false");
      expect(document.activeElement).toBe(activityToggle);
    });
  });

  it("queues a follow-up while Agent is running and sends it next", async () => {
    let resolveFirst:
      | ((value: Awaited<ReturnType<typeof sendAiChatMessage>>) => void)
      | undefined;
    vi.mocked(sendAiChatMessage)
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveFirst = resolve;
          }),
      )
      .mockResolvedValueOnce({
        session: {
          id: "session-1",
          title: "补充要求",
          model: "auto",
          thinking_depth: "standard",
          preset_id: "general",
        },
        trace: { ...trace, id: "trace-2" },
        user_message: { id: "3", role: "user", content: "只看安泰的课程" },
        assistant_message: {
          id: "4",
          role: "assistant",
          content: "已按补充要求处理",
        },
      });

    render(<AIChatView onBack={vi.fn()} />);
    const input = await screen.findByRole("textbox", { name: "输入问题" });
    fireEvent.change(input, { target: { value: "检索本学期课程" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await screen.findByText("请求已发送，正在等待 Agent 返回");

    fireEvent.change(input, { target: { value: "只看安泰的课程" } });
    expect(
      screen.getByRole("button", { name: "发送消息" }).hasAttribute("disabled"),
    ).toBe(false);
    expect(
      screen
        .getByRole("button", { name: "添加本地附件" })
        .hasAttribute("disabled"),
    ).toBe(false);
    expect(
      screen
        .getByRole("button", { name: "Agent：学习数据 Agent" })
        .hasAttribute("disabled"),
    ).toBe(false);
    fireEvent.keyDown(input, { key: "Enter" });

    expect(await screen.findByText("将在当前检索完成后执行")).toBeTruthy();
    expect(sendAiChatMessage).toHaveBeenCalledTimes(1);

    resolveFirst?.({
      session: {
        id: "session-1",
        title: "课程检索",
        model: "auto",
        thinking_depth: "standard",
        preset_id: "general",
      },
      trace,
      user_message: { id: "1", role: "user", content: "检索本学期课程" },
      assistant_message: { id: "2", role: "assistant", content: "第一轮完成" },
    });

    await waitFor(() => expect(sendAiChatMessage).toHaveBeenCalledTimes(2));
    expect(sendAiChatMessage).toHaveBeenLastCalledWith(
      "session-1",
      "只看安泰的课程",
      "auto",
      "standard",
      "general",
      undefined,
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(await screen.findByText("已按补充要求处理")).toBeTruthy();
  });

  it("restores the failed request and offers a real retry", async () => {
    const failure = new Error("AI 服务暂时不可用") as Error & {
      retryable?: boolean;
    };
    failure.retryable = true;
    vi.mocked(sendAiChatMessage).mockRejectedValueOnce(failure);

    render(<AIChatView onBack={vi.fn()} />);
    const input = await screen.findByRole("textbox", { name: "输入问题" });
    fireEvent.change(input, { target: { value: "重新检查本周任务" } });
    fireEvent.keyDown(input, { key: "Enter" });

    expect(await screen.findByText("AI 服务暂时不可用")).toBeTruthy();
    expect((input as HTMLTextAreaElement).value).toBe("重新检查本周任务");
    const retry = screen.getByRole("button", { name: "重试本次" });
    fireEvent.click(retry);

    await waitFor(() => expect(sendAiChatMessage).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("优先事项")).toBeTruthy();
    expect((input as HTMLTextAreaElement).value).toBe("");
  });

  it("closes personalization with Escape and restores trigger focus", async () => {
    render(<AIChatView onBack={vi.fn()} />);
    const trigger = await screen.findByRole("button", { name: "个性化设置" });
    fireEvent.click(trigger);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(
      screen.queryByRole("dialog", { name: "AI 助手个性化设置" }),
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
