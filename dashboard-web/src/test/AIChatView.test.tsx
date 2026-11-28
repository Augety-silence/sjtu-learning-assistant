// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AIChatView } from "@/components/AIChatView";
import { type AIComposerDraft, AIComposerDraftContext } from "@/lib/aiDrafts";
import {
  createAiChatSession,
  getAiChatSession,
  getAiChatSessions,
  getAiWorkspaceStatus,
  getSettings,
  ingestAiAttachment,
  pickAiAttachment,
  pickAiWorkspace,
  resumeAiChatMessageTask,
  sendAiChatMessage,
  steerAiChatTask,
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
  resumeAiChatMessageTask: vi.fn(),
  steerAiChatTask: vi.fn(async (_session, taskId, content) => ({
    task_id: taskId,
    user_message: { id: "steering-ack", role: "user", content },
  })),
  getAiWorkspaceStatus: vi.fn(async () => ({
    configured: false,
    available: false,
    name: null,
    path: null,
    reason: null,
  })),
  getKnowledgeMCPConfig: vi.fn(async () => ({
    command: "/tmp/python",
    args: ["--mcp-stdio"],
  })),
  pickAiWorkspace: vi.fn(),
  clearAiWorkspace: vi.fn(),
  listAiAttachments: vi.fn(async () => ({ items: [] })),
  saveAiSkill: vi.fn(),
  deleteAiSkill: vi.fn(),
  getAiFileChange: vi.fn(),
  applyAiFileChange: vi.fn(),
  rejectAiFileChange: vi.fn(),
}));

beforeEach(() => {
  document.documentElement.dataset.uiRelease = "classic";
});

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

  it("moves focus to the visible composer after deleting the active session", async () => {
    const originalConfirm = window.confirm;
    window.confirm = vi.fn(() => true);
    const first = {
      id: "session-a",
      title: "会话 A",
      model: "auto",
      thinking_depth: "standard" as const,
      preset_id: "general",
      messages: [],
      traces: [],
    };
    const second = { ...first, id: "session-b", title: "会话 B" };
    vi.mocked(getAiChatSessions)
      .mockResolvedValueOnce({ items: [first, second] })
      .mockResolvedValueOnce({ items: [second] });
    vi.mocked(getAiChatSession)
      .mockResolvedValueOnce(first)
      .mockResolvedValueOnce(second);

    try {
      render(<AIChatView onBack={vi.fn()} />);
      const input = await screen.findByRole("textbox", { name: "输入问题" });
      fireEvent.click(
        await screen.findByRole("button", { name: "删除对话：会话 A" }),
      );

      await waitFor(() => expect(document.activeElement).toBe(input));
    } finally {
      window.confirm = originalConfirm;
    }
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

  it("detaches on unmount and resumes the same task before allowing another send", async () => {
    vi.mocked(sendAiChatMessage).mockImplementationOnce(
      (_session, _content, _model, _depth, _preset, _attachments, options) => {
        options?.onTaskStart?.("task-reconnect");
        return new Promise((_resolve, reject) => {
          options?.pollSignal?.addEventListener(
            "abort",
            () =>
              reject(
                Object.assign(new Error("detached"), {
                  category: "detached",
                  retryable: false,
                }),
              ),
            { once: true },
          );
        });
      },
    );
    vi.mocked(resumeAiChatMessageTask).mockImplementationOnce(
      () => new Promise(() => undefined),
    );

    const first = render(<AIChatView onBack={vi.fn()} />);
    const firstInput = await screen.findByRole("textbox", { name: "输入问题" });
    fireEvent.change(firstInput, { target: { value: "继续后台检索" } });
    fireEvent.keyDown(firstInput, { key: "Enter" });
    await waitFor(() =>
      expect(
        window.localStorage.getItem("ai-chat-running-task:session-1"),
      ).toContain("task-reconnect"),
    );
    first.unmount();

    render(<AIChatView onBack={vi.fn()} />);
    await waitFor(() =>
      expect(resumeAiChatMessageTask).toHaveBeenCalledWith(
        "session-1",
        "task-reconnect",
        expect.objectContaining({
          signal: expect.any(AbortSignal),
          pollSignal: expect.any(AbortSignal),
        }),
      ),
    );
    expect(
      await screen.findByRole("button", { name: "停止本次任务" }),
    ).toBeTruthy();
    expect(sendAiChatMessage).toHaveBeenCalledTimes(1);
  });

  it("keeps a task resumable when cancellation cannot be confirmed", async () => {
    vi.mocked(sendAiChatMessage).mockImplementationOnce(
      (_session, _content, _model, _depth, _preset, _attachments, options) => {
        options?.onTaskStart?.("task-cancel-unconfirmed");
        return Promise.reject(
          Object.assign(new Error("停止请求超时"), {
            category: "cancel_unconfirmed",
            retryable: true,
          }),
        );
      },
    );
    vi.mocked(resumeAiChatMessageTask).mockImplementationOnce(
      () => new Promise(() => undefined),
    );

    render(<AIChatView onBack={vi.fn()} />);
    const input = await screen.findByRole("textbox", { name: "输入问题" });
    fireEvent.change(input, { target: { value: "保留仍在运行的任务" } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() =>
      expect(
        window.localStorage.getItem("ai-chat-running-task:session-1"),
      ).toContain("task-cancel-unconfirmed"),
    );
    expect(screen.getByText("保留仍在运行的任务")).toBeTruthy();
    expect(screen.queryByText("停止请求超时")).toBeNull();
    await waitFor(() =>
      expect(resumeAiChatMessageTask).toHaveBeenCalledWith(
        "session-1",
        "task-cancel-unconfirmed",
        expect.objectContaining({
          signal: expect.any(AbortSignal),
          pollSignal: expect.any(AbortSignal),
        }),
      ),
    );
    expect(
      window.localStorage.getItem("ai-chat-running-task:session-1"),
    ).toContain("task-cancel-unconfirmed");
  });

  it("keeps a steering request in the same task while cancellation remains unconfirmed", async () => {
    let rejectFirst: ((reason: Error) => void) | undefined;
    vi.mocked(sendAiChatMessage).mockImplementationOnce(
      (_session, _content, _model, _depth, _preset, _attachments, options) => {
        options?.onTaskStart?.("task-first");
        return new Promise((_resolve, reject) => {
          rejectFirst = reject;
        });
      },
    );
    vi.mocked(resumeAiChatMessageTask).mockImplementationOnce(
      (_session, _task, options) => {
        void options?.signal;
        return new Promise(() => undefined);
      },
    );

    render(<AIChatView onBack={vi.fn()} />);
    const input = await screen.findByRole("textbox", { name: "输入问题" });
    fireEvent.change(input, { target: { value: "第一项" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await screen.findByText("请求已发送，正在等待 Agent 返回");

    fireEvent.change(input, { target: { value: "第二项" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(steerAiChatTask).toHaveBeenCalledTimes(1));

    rejectFirst?.(
      Object.assign(new Error("停止请求超时"), {
        category: "cancel_unconfirmed",
        retryable: true,
      }),
    );

    await waitFor(() =>
      expect(resumeAiChatMessageTask).toHaveBeenCalledWith(
        "session-1",
        "task-first",
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      ),
    );
    expect(sendAiChatMessage).toHaveBeenCalledTimes(1);
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
          type: "start",
          session_id: "session-1",
          task_id: "task-live",
        },
      }),
    );
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

  it("keeps live per-turn execution details in the thread while the answer streams", async () => {
    vi.mocked(sendAiChatMessage).mockImplementationOnce(
      () => new Promise(() => undefined),
    );
    const { container } = render(<AIChatView onBack={vi.fn()} />);
    const input = await screen.findByRole("textbox", { name: "输入问题" });
    fireEvent.change(input, { target: { value: "读一下第 4 页课件" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await screen.findByRole("button", { name: "停止本次任务" });
    const emit = (detail: Record<string, unknown>) =>
      window.dispatchEvent(
        new CustomEvent("sjtu-ai-chat-stream", {
          detail: { session_id: "session-1", task_id: "task-turn", ...detail },
        }),
      );
    emit({
      type: "start",
      started_at: new Date(Date.now() - 65_000).toISOString(),
    });
    emit({
      type: "tool",
      run: {
        tool_name: "read_course_file_text",
        phase: "model",
        status: "ok",
        arguments_summary: { start_page: 1, max_chars: 6000 },
        result_summary: {
          status: "ok",
          count: 1,
          content_status: "ready",
          start_page: 1,
          end_page: 20,
          unit_count: 20,
          unit_label: "页",
        },
        duration_ms: 420,
      },
    });
    // 运行中默认展开，步骤直接出现在对话流里，而不只在 Activity 面板。
    const running = await waitFor(() => {
      const node = container.querySelector(
        ".ai-chat-messages > .ai-inline-execution .ai-turn-trace.is-running",
      );
      expect(node?.textContent).toContain("读取课件正文");
      return node as HTMLElement;
    });
    expect(running.textContent).toContain("已读第 1–20 页 / 共 20 页");
    expect(running.textContent).toContain("0.4 秒");
    expect(running.textContent).toMatch(
      /正在执行 · 已用 1 分 \d+ 秒 · 1 个工具调用/,
    );

    emit({ type: "content_delta", delta: "第 4 页介绍了开课概况。" });
    // 流式文字按节奏逐步渐显（被拆入渐显元素），最终完整显示在回复气泡中。
    await waitFor(() =>
      expect(
        container.querySelector(
          ".ai-message-assistant .ai-message-body > .ai-markdown",
        )?.textContent,
      ).toBe("第 4 页介绍了开课概况。"),
    );
    // 正文开始流式输出后，执行详情跟随到回复气泡顶部，不会消失。
    await waitFor(() =>
      expect(
        container.querySelector(
          ".ai-message-assistant .ai-turn-trace.is-running",
        )?.textContent,
      ).toContain("正在生成回答…"),
    );
    // 独立的运行卡片在退场动画结束后移除，不会与气泡内的详情重复出现。
    await waitFor(() =>
      expect(
        container.querySelector(".ai-chat-messages > .ai-inline-execution"),
      ).toBeNull(),
    );
  });

  it("keeps execution notes between tools and only the final result in the answer", async () => {
    vi.mocked(sendAiChatMessage).mockImplementationOnce(
      () => new Promise(() => undefined),
    );
    const { container } = render(<AIChatView onBack={vi.fn()} />);
    const input = await screen.findByRole("textbox", { name: "输入问题" });
    fireEvent.change(input, { target: { value: "查询课程" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await screen.findByRole("button", { name: "停止本次任务" });
    const emit = (detail: Record<string, unknown>) =>
      window.dispatchEvent(
        new CustomEvent("sjtu-ai-chat-stream", {
          detail: { session_id: "session-1", task_id: "task-notes", ...detail },
        }),
      );
    emit({ type: "start" });
    emit({ type: "progress_delta", delta: "正在查课程。" });
    await waitFor(() =>
      expect(container.querySelector(".ai-execution-note")?.textContent).toBe(
        "正在查课程。",
      ),
    );
    expect(
      container.querySelector(
        ".ai-message-assistant .ai-message-body > .ai-markdown",
      )?.textContent ?? "",
    ).not.toContain("正在查课程。");
    emit({
      type: "tool",
      run: {
        tool_name: "list_courses",
        phase: "model",
        status: "ok",
        progress_text: "正在查课程。",
        arguments_summary: {},
        result_summary: { count: 1 },
      },
    });
    emit({ type: "progress_delta", delta: "继续核对文件。" });
    await waitFor(() =>
      expect(container.querySelectorAll(".ai-execution-note").length).toBe(2),
    );
    emit({
      type: "tool",
      run: {
        tool_name: "list_course_files",
        phase: "model",
        status: "ok",
        progress_text: "继续核对文件。",
        arguments_summary: {},
        result_summary: { count: 1 },
      },
    });
    emit({ type: "progress_delta", delta: "临时候选答案" });
    emit({ type: "answer_start" });
    emit({ type: "content_delta", delta: "已找到一份课件。" });
    await waitFor(() =>
      expect(
        container.querySelector(
          ".ai-message-assistant .ai-message-body > .ai-markdown",
        )?.textContent,
      ).toBe("已找到一份课件。"),
    );
    expect(container.textContent).not.toContain("临时候选答案");
    await waitFor(() =>
      expect(
        container.querySelector(".ai-chat-messages > .ai-inline-execution"),
      ).toBeNull(),
    );
    const notes = container.querySelectorAll(
      ".ai-message-assistant .ai-turn-trace .ai-execution-note",
    );
    expect(Array.from(notes).map((note) => note.textContent)).toEqual([
      "正在查课程。",
      "继续核对文件。",
    ]);
  });

  it("removes a failed task's temporary running trace before the retry", async () => {
    let rejectFirst: ((reason: Error) => void) | undefined;
    vi.mocked(sendAiChatMessage)
      .mockImplementationOnce(
        () =>
          new Promise((_resolve, reject) => {
            rejectFirst = reject;
          }),
      )
      .mockImplementationOnce(() => new Promise(() => undefined));

    render(<AIChatView onBack={vi.fn()} />);
    const input = await screen.findByRole("textbox", { name: "输入问题" });
    fireEvent.change(input, { target: { value: "先检索再失败" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await screen.findByRole("button", { name: "停止本次任务" });
    window.dispatchEvent(
      new CustomEvent("sjtu-ai-chat-stream", {
        detail: {
          type: "start",
          session_id: "session-1",
          task_id: "task-failed",
        },
      }),
    );
    window.dispatchEvent(
      new CustomEvent("sjtu-ai-chat-stream", {
        detail: {
          type: "tool",
          session_id: "session-1",
          task_id: "task-failed",
          run: {
            tool_name: "get_deadlines",
            phase: "prefetch",
            status: "ok",
            arguments_summary: {},
            result_summary: { count: 1 },
          },
        },
      }),
    );
    rejectFirst?.(new Error("检索失败"));
    expect(await screen.findByText("检索失败")).toBeTruthy();
    expect(screen.queryByText(/进行中 · 调用 1 次/)).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "重试本次" }));
    fireEvent.click(screen.getByRole("button", { name: /Activity/ }));
    expect(await screen.findByText("等待 Agent 返回")).toBeTruthy();
    expect(screen.queryByText(/调用 1 次/)).toBeNull();
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

    // 完成后折叠为一行本轮摘要（耗时 · 工具调用数），点击可展开逐步核对。
    expect(await screen.findByText("已完成 · 1 个工具调用")).toBeTruthy();
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
    expect(screen.getByText("已完成 · 1 个工具调用")).toBeTruthy();
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
    expect(document.activeElement).toBe(input);

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
      ".ai-message-assistant .ai-turn-trace-toggle",
    );
    expect(inlineExecution).toBeTruthy();
    expect(inlineExecution?.getAttribute("aria-expanded")).toBe("false");
    expect(
      container.querySelector(".ai-message-assistant .ai-turn-trace-detail"),
    ).toBeNull();
    fireEvent.click(inlineExecution as HTMLElement);
    expect(inlineExecution?.getAttribute("aria-expanded")).toBe("true");
    const inlineTool = container.querySelector(
      ".ai-message-assistant .ai-turn-trace-detail .ai-tool-run > summary",
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

  it("accepts a native path that arrives before the DOM drop event", async () => {
    render(<AIChatView onBack={vi.fn()} />);
    await screen.findByRole("textbox", { name: "输入问题" });
    const workspace = screen.getByRole("main");
    const file = new File(["notes"], "notes.txt", { type: "text/plain" });
    const dataTransfer = {
      types: ["Files"],
      files: [file],
      dropEffect: "none",
    };

    fireEvent.dragEnter(workspace, { dataTransfer });
    window.dispatchEvent(
      new CustomEvent("sjtu-native-file-drop", {
        detail: { paths: ["/tmp/提前到达.md"] },
      }),
    );
    fireEvent.drop(workspace, { dataTransfer });

    expect(await screen.findByText("拖入笔记.md")).toBeTruthy();
    expect(ingestAiAttachment).toHaveBeenCalledWith("/tmp/提前到达.md");
    await new Promise((resolve) => setTimeout(resolve, 1_000));
    expect(screen.queryByText(/桌面文件路径尚未就绪/)).toBeNull();
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

  it("立即将补充输入提交给执行中的任务，完成后不开第二轮", async () => {
    let resolveFirst:
      | ((value: Awaited<ReturnType<typeof sendAiChatMessage>>) => void)
      | undefined;
    vi.mocked(sendAiChatMessage).mockImplementationOnce(
      (_session, _content, _model, _depth, _preset, _attachments, options) => {
        options?.onTaskStart?.("task-first");
        return new Promise((resolve) => {
          resolveFirst = resolve;
        });
      },
    );
    render(<AIChatView onBack={vi.fn()} />);
    const input = await screen.findByRole("textbox", { name: "输入问题" });
    fireEvent.change(input, { target: { value: "检索本学期课程" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await screen.findByText("请求已发送，正在等待 Agent 返回");
    fireEvent.change(input, { target: { value: "只看安泰的课程" } });
    expect(
      screen.getByRole("button", { name: "补充指令" }).hasAttribute("disabled"),
    ).toBe(false);
    expect(
      screen
        .getByRole("button", { name: "添加本地附件" })
        .hasAttribute("disabled"),
    ).toBe(false);
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() =>
      expect(steerAiChatTask).toHaveBeenCalledWith(
        "session-1",
        "task-first",
        "只看安泰的课程",
        expect.any(String),
        [],
      ),
    );
    expect(screen.queryByText("将在当前检索完成后执行")).toBeNull();
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
      assistant_message: {
        id: "2",
        role: "assistant",
        content: "已按补充要求处理",
      },
    });
    expect(await screen.findByText("已按补充要求处理")).toBeTruthy();
    expect(sendAiChatMessage).toHaveBeenCalledTimes(1);
    expect(screen.getByText("只看安泰的课程")).toBeTruthy();
  });

  it("任务标识返回后立即提交此前输入的补充指令", async () => {
    let onStart: ((taskId: string) => void) | undefined;
    vi.mocked(sendAiChatMessage).mockImplementationOnce(
      (_session, _content, _model, _depth, _preset, _attachments, options) => {
        onStart = options?.onTaskStart;
        return new Promise(() => undefined);
      },
    );
    render(<AIChatView onBack={vi.fn()} />);
    const input = await screen.findByRole("textbox", { name: "输入问题" });
    fireEvent.change(input, { target: { value: "读取课件" } });
    fireEvent.keyDown(input, { key: "Enter" });
    fireEvent.change(input, { target: { value: "只解释第 4 页" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(steerAiChatTask).not.toHaveBeenCalled();
    expect(
      window.localStorage.getItem("ai-chat-pending-steering:session-1"),
    ).toContain("只解释第 4 页");
    onStart?.("task-delayed");
    await waitFor(() =>
      expect(steerAiChatTask).toHaveBeenCalledWith(
        "session-1",
        "task-delayed",
        "只解释第 4 页",
        expect.any(String),
        [],
      ),
    );
    await waitFor(() =>
      expect(
        window.localStorage.getItem("ai-chat-pending-steering:session-1"),
      ).toBeNull(),
    );
    expect(sendAiChatMessage).toHaveBeenCalledTimes(1);
  });

  it("执行中的会话可新建并启动另一个任务，后台完成不会清除当前停止状态", async () => {
    const first = {
      id: "session-1",
      title: "会话 A",
      model: "auto",
      thinking_depth: "standard" as const,
      preset_id: "general",
      messages: [],
      traces: [],
    };
    const second = { ...first, id: "session-2", title: "会话 B" };
    vi.mocked(createAiChatSession)
      .mockResolvedValueOnce(first)
      .mockResolvedValueOnce(second);
    let resolveFirst:
      | ((value: Awaited<ReturnType<typeof sendAiChatMessage>>) => void)
      | undefined;
    let signalA: AbortSignal | undefined;
    let signalB: AbortSignal | undefined;
    vi.mocked(sendAiChatMessage)
      .mockImplementationOnce(
        (
          _session,
          _content,
          _model,
          _depth,
          _preset,
          _attachments,
          options,
        ) => {
          options?.onTaskStart?.("task-a");
          signalA = options?.signal;
          return new Promise((resolve) => {
            resolveFirst = resolve;
          });
        },
      )
      .mockImplementationOnce(
        (
          _session,
          _content,
          _model,
          _depth,
          _preset,
          _attachments,
          options,
        ) => {
          options?.onTaskStart?.("task-b");
          signalB = options?.signal;
          return new Promise(() => undefined);
        },
      );
    const { container } = render(<AIChatView onBack={vi.fn()} />);
    const input = await screen.findByRole("textbox", { name: "输入问题" });
    fireEvent.change(input, { target: { value: "任务 A" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await screen.findByRole("button", { name: "停止本次任务" });
    const create = screen.getByRole("button", { name: "新对话" });
    expect(create.hasAttribute("disabled")).toBe(false);
    fireEvent.click(create);
    await waitFor(() =>
      expect(
        container.querySelector(".ai-conversation-heading strong")?.textContent,
      ).toBe("会话 B"),
    );
    const inputB = screen.getByRole("textbox", { name: "输入问题" });
    fireEvent.change(inputB, { target: { value: "任务 B" } });
    fireEvent.keyDown(inputB, { key: "Enter" });
    await waitFor(() => expect(sendAiChatMessage).toHaveBeenCalledTimes(2));
    expect(sendAiChatMessage).toHaveBeenLastCalledWith(
      "session-2",
      "任务 B",
      "auto",
      "standard",
      "general",
      undefined,
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    window.dispatchEvent(
      new CustomEvent("sjtu-ai-chat-stream", {
        detail: {
          type: "content_delta",
          session_id: "session-1",
          task_id: "task-a",
          delta: "A 的旧消息",
        },
      }),
    );
    window.dispatchEvent(
      new CustomEvent("sjtu-ai-chat-stream", {
        detail: {
          type: "content_delta",
          session_id: "session-2",
          task_id: "old-b",
          delta: "旧 B 消息",
        },
      }),
    );
    expect(container.textContent).not.toContain("A 的旧消息");
    expect(container.textContent).not.toContain("旧 B 消息");
    fireEvent.click(screen.getByRole("button", { name: "停止本次任务" }));
    expect(signalB?.aborted).toBe(true);
    expect(signalA?.aborted).toBe(false);
    resolveFirst?.({
      session: first,
      trace,
      user_message: { id: "a-user", role: "user", content: "任务 A" },
      assistant_message: {
        id: "a-answer",
        role: "assistant",
        content: "A 完成",
      },
    });
    await waitFor(() =>
      expect(
        window.localStorage.getItem("ai-chat-running-task:session-1"),
      ).toBeNull(),
    );
    expect(
      container.querySelector(".ai-conversation-heading strong")?.textContent,
    ).toBe("会话 B");
    expect(
      screen
        .getByRole("button", { name: "正在停止本次任务" })
        .hasAttribute("disabled"),
    ).toBe(true);
    expect(container.textContent).not.toContain("A 完成");
  });

  it("终态拒绝的补充指令恢复草稿，不自动启动第二轮", async () => {
    vi.mocked(sendAiChatMessage).mockImplementationOnce(
      (_session, _content, _model, _depth, _preset, _attachments, options) => {
        options?.onTaskStart?.("task-ended");
        return new Promise(() => undefined);
      },
    );
    vi.mocked(steerAiChatTask).mockRejectedValueOnce(
      new Error("原任务已经结束，请重新确认补充内容"),
    );
    const { container } = render(<AIChatView onBack={vi.fn()} />);
    const input = await screen.findByRole("textbox", { name: "输入问题" });
    fireEvent.change(input, { target: { value: "读取课件" } });
    fireEvent.keyDown(input, { key: "Enter" });
    fireEvent.change(input, { target: { value: "补充条件" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(
      await screen.findByText("原任务已经结束，请重新确认补充内容"),
    ).toBeTruthy();
    expect((input as HTMLTextAreaElement).value).toBe("补充条件");
    expect(
      container.querySelector(".ai-chat-messages")?.textContent,
    ).not.toContain("补充条件");
    expect(sendAiChatMessage).toHaveBeenCalledTimes(1);
    expect(
      window.localStorage.getItem("ai-chat-pending-steering:session-1"),
    ).toBeNull();
  });

  it("补充指令 ACK 不明时重载仍使用同一请求标识", async () => {
    vi.mocked(sendAiChatMessage).mockImplementationOnce(
      (_session, _content, _model, _depth, _preset, _attachments, options) => {
        options?.onTaskStart?.("task-uncertain");
        return new Promise(() => undefined);
      },
    );
    vi.mocked(steerAiChatTask).mockRejectedValueOnce(
      Object.assign(new Error("桥接暂不可用"), {
        category: "steering_unconfirmed",
      }),
    );
    const view = render(<AIChatView onBack={vi.fn()} />);
    const input = await screen.findByRole("textbox", { name: "输入问题" });
    fireEvent.change(input, { target: { value: "慢任务" } });
    fireEvent.keyDown(input, { key: "Enter" });
    fireEvent.change(input, { target: { value: "即时补充" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await screen.findByText(
      "补充指令暂未确认，已保留；重新连接后将按原标识重试。",
    );
    const requestId = vi.mocked(steerAiChatTask).mock.calls[0][3];
    expect(
      window.localStorage.getItem("ai-chat-pending-steering:session-1"),
    ).toContain(requestId);
    fireEvent.click(screen.getByRole("button", { name: "停止本次任务" }));
    expect(
      window.localStorage.getItem("ai-chat-pending-steering:session-1"),
    ).toContain(requestId);
    view.unmount();
    vi.mocked(resumeAiChatMessageTask).mockImplementationOnce(
      () => new Promise(() => undefined),
    );
    render(<AIChatView onBack={vi.fn()} />);
    await waitFor(() => expect(steerAiChatTask).toHaveBeenCalledTimes(2));
    expect(steerAiChatTask).toHaveBeenLastCalledWith(
      "session-1",
      "task-uncertain",
      "即时补充",
      requestId,
      [],
    );
    expect(sendAiChatMessage).toHaveBeenCalledTimes(1);
  });

  it("已落库的补充 ACK 重试保持消息顺序", async () => {
    const request = {
      sessionId: "session-1",
      text: "即时补充",
      optimisticId: "request-accepted",
      attachments: [],
      model: "auto",
      depth: "standard",
      presetId: "general",
      steeringTaskId: "task-completed",
    };
    window.localStorage.setItem(
      "ai-chat-pending-steering:session-1",
      JSON.stringify([request]),
    );
    vi.mocked(createAiChatSession).mockResolvedValueOnce({
      id: "session-1",
      title: "已完成",
      model: "auto",
      thinking_depth: "standard",
      preset_id: "general",
      messages: [
        { id: "original", role: "user", content: "原始要求" },
        { id: "steering-ack", role: "user", content: "即时补充" },
        { id: "completed", role: "assistant", content: "完成答案" },
      ],
      traces: [],
    });
    const { container } = render(<AIChatView onBack={vi.fn()} />);
    await waitFor(() =>
      expect(steerAiChatTask).toHaveBeenCalledWith(
        "session-1",
        "task-completed",
        "即时补充",
        "request-accepted",
        [],
      ),
    );
    await waitFor(() =>
      expect(
        window.localStorage.getItem("ai-chat-pending-steering:session-1"),
      ).toBeNull(),
    );
    expect(
      Array.from(
        container.querySelectorAll(".ai-chat-messages .ai-message-body"),
      ).map((node) => node.textContent),
    ).toEqual(["原始要求", "即时补充", "学习数据 Agent完成答案"]);
    expect(sendAiChatMessage).not.toHaveBeenCalled();
  });

  it("切换执行中的会话保留各自的草稿", async () => {
    const first = {
      id: "session-a",
      title: "会话 A",
      model: "auto",
      thinking_depth: "standard" as const,
      preset_id: "general",
      messages: [],
      traces: [],
    };
    const second = { ...first, id: "session-b", title: "会话 B" };
    vi.mocked(getAiChatSessions).mockResolvedValueOnce({
      items: [first, second],
    });
    vi.mocked(getAiChatSession)
      .mockResolvedValueOnce(first)
      .mockResolvedValueOnce(second)
      .mockResolvedValueOnce(first);
    vi.mocked(sendAiChatMessage).mockImplementationOnce(
      (_session, _content, _model, _depth, _preset, _attachments, options) => {
        options?.onTaskStart?.("task-a-draft");
        return new Promise(() => undefined);
      },
    );
    render(<AIChatView onBack={vi.fn()} />);
    const input = await screen.findByRole("textbox", { name: "输入问题" });
    fireEvent.change(input, { target: { value: "任务 A" } });
    fireEvent.keyDown(input, { key: "Enter" });
    fireEvent.change(input, { target: { value: "A 的待发草稿" } });
    fireEvent.click(screen.getByRole("button", { name: "会话 B" }));
    await waitFor(() =>
      expect(
        (
          screen.getByRole("textbox", {
            name: "输入问题",
          }) as HTMLTextAreaElement
        ).value,
      ).toBe(""),
    );
    fireEvent.change(screen.getByRole("textbox", { name: "输入问题" }), {
      target: { value: "B 的待发草稿" },
    });
    fireEvent.click(screen.getByRole("button", { name: "会话 A" }));
    await waitFor(() =>
      expect(
        (
          screen.getByRole("textbox", {
            name: "输入问题",
          }) as HTMLTextAreaElement
        ).value,
      ).toBe("A 的待发草稿"),
    );
    expect(screen.getByRole("button", { name: "停止本次任务" })).toBeTruthy();
    expect(sendAiChatMessage).toHaveBeenCalledTimes(1);
  });

  it.each(["steering_applied", "response_reset"])(
    "%s 清除旧流正文并保留工具结果",
    async (resetEvent) => {
      vi.mocked(sendAiChatMessage).mockImplementationOnce(
        (
          _session,
          _content,
          _model,
          _depth,
          _preset,
          _attachments,
          options,
        ) => {
          options?.onTaskStart?.("task-steer-stream");
          return new Promise(() => undefined);
        },
      );
      const { container } = render(<AIChatView onBack={vi.fn()} />);
      const input = await screen.findByRole("textbox", { name: "输入问题" });
      fireEvent.change(input, { target: { value: "读取课件" } });
      fireEvent.keyDown(input, { key: "Enter" });
      await screen.findByRole("button", { name: "停止本次任务" });
      const emit = (detail: Record<string, unknown>) =>
        window.dispatchEvent(
          new CustomEvent("sjtu-ai-chat-stream", {
            detail: {
              ...detail,
              session_id: "session-1",
              task_id: "task-steer-stream",
            },
          }),
        );
      emit({
        type: "tool",
        run: {
          tool_name: "list_courses",
          phase: "model",
          status: "ok",
          arguments_summary: {},
          result_summary: { count: 2 },
        },
      });
      emit({ type: "content_delta", delta: "旧答案" });
      await waitFor(() =>
        expect(
          container.querySelector(".ai-message-assistant .ai-markdown")
            ?.textContent,
        ).toBe("旧答案"),
      );
      emit({ type: resetEvent, request_ids: ["supplement-1"] });
      await waitFor(() =>
        expect(
          container.querySelector(".ai-message-assistant .ai-markdown"),
        ).toBeNull(),
      );
      emit({ type: "content_delta", delta: "补充后的答案" });
      await waitFor(() =>
        expect(
          container.querySelector(".ai-message-assistant .ai-markdown")
            ?.textContent,
        ).toBe("补充后的答案"),
      );
      expect(
        container.querySelector(".ai-message-assistant .ai-turn-trace")
          ?.textContent,
      ).toContain("读取课程列表");
    },
  );

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

describe("2.0 workspace integration", () => {
  beforeEach(() => {
    document.documentElement.dataset.uiRelease = "2";
  });

  it("opens configuration centrally and preserves the conversation draft", async () => {
    render(<AIChatView onBack={vi.fn()} />);
    await screen.findByText("今天想一起完成什么？");
    const input = screen.getByRole("textbox", { name: "输入问题" });
    fireEvent.change(input, { target: { value: "未发送的复习安排" } });
    fireEvent.click(screen.getByRole("button", { name: "技能配置" }));
    expect(
      await screen.findByRole("region", { name: "技能配置" }),
    ).toBeTruthy();
    expect(screen.queryByRole("textbox", { name: "输入问题" })).toBeNull();
    expect((input as HTMLTextAreaElement).value).toBe("未发送的复习安排");
    expect(screen.queryByRole("button", { name: "导入 ZIP" })).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: "外部技能" }));
    expect(screen.getByRole("button", { name: "导入 ZIP" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "全部任务" }));
    const search = await screen.findByRole("searchbox", {
      name: "搜索全部任务",
    });
    expect(search.closest("aside")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "返回对话" }));
    expect(
      (screen.getByRole("textbox", { name: "输入问题" }) as HTMLTextAreaElement)
        .value,
    ).toBe("未发送的复习安排");
    expect(sendAiChatMessage).not.toHaveBeenCalled();
  });

  it("uses a portal for the agent menu and keeps drafts when switching interface", async () => {
    const { container } = render(<AIChatView onBack={vi.fn()} />);
    await screen.findByText("今天想一起完成什么？");
    fireEvent.click(
      screen.getByRole("button", { name: "当前 Agent：学习数据 Agent" }),
    );
    const list = screen.getByRole("listbox", { name: "切换 Agent" });
    expect(list.closest("[data-ai-floating-menu]")?.parentElement).toBe(
      document.body,
    );
    expect(container.querySelector('[role="listbox"]')).toBeNull();
    fireEvent.click(screen.getByRole("option", { name: "学习数据 Agent" }));
    expect(screen.queryByRole("listbox", { name: "切换 Agent" })).toBeNull();
    fireEvent.change(screen.getByRole("textbox", { name: "输入问题" }), {
      target: { value: "界面切换草稿" },
    });
    fireEvent.click(screen.getByRole("button", { name: "切换界面版本" }));
    expect(await screen.findByText("今天想从学习数据里查什么？")).toBeTruthy();
    expect(
      (screen.getByRole("textbox", { name: "输入问题" }) as HTMLTextAreaElement)
        .value,
    ).toBe("界面切换草稿");
  });

  it("selects the workspace through the original bridge callback", async () => {
    vi.mocked(pickAiWorkspace).mockResolvedValueOnce({
      configured: true,
      available: true,
      name: "练习",
      path: "/tmp/练习",
      reason: null,
    });
    render(<AIChatView onBack={vi.fn()} />);
    await screen.findByText("今天想一起完成什么？");
    await waitFor(() => expect(getAiWorkspaceStatus).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: "工作区" }));
    fireEvent.click(await screen.findByRole("button", { name: "选择文件夹" }));
    expect(await screen.findByText("/tmp/练习")).toBeTruthy();
    expect(pickAiWorkspace).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "取消授权" })).toBeTruthy();
  });

  it("restores draft text and attachments after returning from the workbench", async () => {
    const drafts = new Map<string, AIComposerDraft>();
    const view = () => (
      <AIComposerDraftContext.Provider value={drafts}>
        <AIChatView onBack={vi.fn()} />
      </AIComposerDraftContext.Provider>
    );
    const first = render(view());
    await screen.findByText("今天想一起完成什么？");
    fireEvent.change(screen.getByRole("textbox", { name: "输入问题" }), {
      target: { value: "先保留这个计划" },
    });
    fireEvent.click(screen.getByRole("button", { name: "唤起技能与工具" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "添加本地文件" }));
    await screen.findByText("复习提纲.txt");
    first.unmount();
    render(view());
    await screen.findByText("今天想一起完成什么？");
    expect(
      (screen.getByRole("textbox", { name: "输入问题" }) as HTMLTextAreaElement)
        .value,
    ).toBe("先保留这个计划");
    expect(await screen.findByText("复习提纲.txt")).toBeTruthy();
    expect(sendAiChatMessage).not.toHaveBeenCalled();
  });
});
