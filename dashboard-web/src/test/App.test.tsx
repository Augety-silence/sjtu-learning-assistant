// @vitest-environment jsdom

import { Profiler } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "@/App";
import { getAICapabilities } from "@/lib/aiCapabilities";
import {
  getAssignmentDetail,
  getAssignments,
  getBackupStatus,
  getCalendar,
  getCourseMedia,
  getMediaCapabilities,
  getMessageDetail,
  getMessages,
  getSettings,
  getTimetableSchedule,
  getTimetableStatus,
  getTranscriptJobs,
  getVideoPlayback,
  invoke,
  startTranscriptBatch,
} from "@/lib/api";
import { applyThemeMode } from "@/lib/theme";
import type {
  AppCapabilities,
  MessageDetail,
  MessageItem,
  OverviewData,
  SyncStatus,
  ThemeMode,
  TranscriptJob,
} from "@/lib/types";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@/test/render";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    getAssignmentDetail: vi.fn(),
    getAssignments: vi.fn(),
    getBackupStatus: vi.fn(),
    getCalendar: vi.fn(),
    getCourseMedia: vi.fn(),
    getMediaCapabilities: vi.fn(),
    getMessageDetail: vi.fn(),
    getMessages: vi.fn(),
    getSettings: vi.fn(),
    getTimetableSchedule: vi.fn(),
    getTimetableStatus: vi.fn(),
    getTranscriptJobs: vi.fn(),
    getVideoPlayback: vi.fn(),
    invoke: vi.fn(),
    startTranscriptBatch: vi.fn(),
  };
});

vi.mock("@/lib/aiCapabilities", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/aiCapabilities")>()),
  getAICapabilities: vi.fn(),
}));

const targetMessage: MessageItem = {
  source_id: "mail-target",
  kind: "email",
  title: "无链接邮件",
  source_label: "老师",
  occurred_at: "2026-09-22T08:00:00+08:00",
  is_unread: true,
  url: null,
};

let syncTriggered = false;
let settingsTheme: ThemeMode = "system";

const overview: OverviewData = {
  courses: 1,
  upcoming_deadlines: 0,
  unread_emails: 1,
  deadlines: [],
  messages: [targetMessage],
};

const detail: MessageDetail = {
  title: targetMessage.title,
  source_label: targetMessage.source_label,
  occurred_at: targetMessage.occurred_at,
  body: "邮件正文",
  body_html: null,
  format: "text",
  pending_body_sync: false,
  attachments: [],
  resources: [],
  url: null,
  is_unread: true,
};

function learnerCapabilities(): AppCapabilities {
  return {
    courses: {
      items: [
        {
          course_id: "12",
          course_name: "课程 A",
          roles: ["student"],
          role_source: "canvas",
          can_view_calendar: true,
          can_view_members: true,
          can_view_submissions: false,
          can_manage_grades: false,
          can_comment_submissions: false,
        },
        {
          course_id: "13",
          course_name: "课程 B",
          roles: ["student"],
          role_source: "canvas",
          can_view_calendar: true,
          can_view_members: true,
          can_view_submissions: false,
          can_manage_grades: false,
          can_comment_submissions: false,
        },
      ],
    },
    media: {},
    update: { check_only: true, automatic_install: false },
    mcp: { transport: "stdio", read_only: true },
  };
}

function courseMedia(courseId: number, title: string) {
  return {
    items: [
      {
        source_id: `video-${courseId}`,
        name: title,
        media_kind: "video",
        source: "video_space",
        course_name: `课程 ${courseId === 12 ? "A" : "B"}`,
        downloadable: false,
        supports_subtitle: true,
        supports_slides_pdf: false,
        playback: {
          available: true,
          transport: "dashboard_action",
          action: "play_remote_video",
          source_id: `video-${courseId}`,
        },
      },
    ],
    subtitles: [],
    unmatched_subtitles: [],
    counts: { video: 1, audio: 0, subtitle: 0 },
  } as never;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((next) => {
    resolve = next;
  });
  return { promise, resolve };
}

function controlIntervals() {
  const intervals: Array<{
    id: number;
    delay: number;
    active: boolean;
    tick: () => void;
  }> = [];
  let nextId = 0;
  vi.spyOn(window, "setInterval").mockImplementation(((
    handler: TimerHandler,
    delay = 0,
  ) => {
    const id = ++nextId;
    intervals.push({
      id,
      delay: Number(delay),
      active: true,
      tick: () => {
        if (typeof handler === "function") handler();
      },
    });
    return id;
  }) as typeof window.setInterval);
  vi.spyOn(window, "clearInterval").mockImplementation((id) => {
    const interval = intervals.find((item) => item.id === Number(id));
    if (interval) interval.active = false;
  });
  return {
    intervals,
    latest(delay: number) {
      return [...intervals]
        .reverse()
        .find((item) => item.delay === delay && item.active);
    },
  };
}

function statusCallCount() {
  return vi
    .mocked(invoke)
    .mock.calls.filter(([action]) => action === "sync_status").length;
}

beforeEach(() => {
  window.location.hash = "#/overview";
  vi.clearAllMocks();
  vi.mocked(getAICapabilities).mockResolvedValue({
    schema_version: 1,
    tasks: [],
    preflight: {
      enabled: true,
      configured: true,
      ready_for_request: true,
      connection_verified: false,
      model: "deepseek-chat",
      reasons: [],
      streaming_implemented: false,
    },
  });
  syncTriggered = false;
  settingsTheme = "system";
  vi.stubGlobal(
    "matchMedia",
    vi.fn(() => ({
      matches: false,
      media: "(prefers-color-scheme: dark)",
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  );
  vi.mocked(invoke).mockImplementation(async (action) => {
    if (action === "capabilities") return new Promise<never>(() => undefined);
    if (action === "overview") return overview as never;
    if (action === "settings_status") {
      return { theme_mode: settingsTheme } as never;
    }
    if (action === "sync_status") {
      return {
        status: syncTriggered ? "syncing" : "idle",
        last_success_at: null,
        last_run_status: null,
        last_run_at: null,
      } as never;
    }
    if (action === "sync_trigger") {
      syncTriggered = true;
      return { status: "accepted" } as never;
    }
    throw new Error(`Unexpected action: ${action}`);
  });
  vi.mocked(getSettings).mockImplementation(
    async () => ({ theme_mode: settingsTheme }) as never,
  );
  vi.mocked(getCourseMedia).mockResolvedValue(courseMedia(12, "A 专属录像"));
  vi.mocked(getMediaCapabilities).mockResolvedValue({
    video_screenshot_pdf: { available: true, reason: null },
  });
  vi.mocked(getTranscriptJobs).mockResolvedValue({ items: [] });
  vi.mocked(getVideoPlayback).mockImplementation(async (sourceId) => ({
    available: true,
    transport: "remote_url",
    action: "play_remote_video",
    source_id: sourceId,
    url: `https://example.test/${sourceId}.mp4`,
  }));
  vi.mocked(startTranscriptBatch).mockResolvedValue({ jobs: [] } as never);
  vi.mocked(getMessages).mockResolvedValue({ items: [] });
  vi.mocked(getMessageDetail).mockResolvedValue(detail);
  vi.mocked(getBackupStatus).mockResolvedValue({
    status: "idle",
    available: true,
    availability_message: null,
    counts: {
      canvas: 2,
      mail: 1,
      ready: 3,
      cloud_only: 0,
      missing_local: 0,
      total: 3,
    },
    progress: null,
    last_result: null,
  });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  document.documentElement.removeAttribute("data-theme");
  document.documentElement.removeAttribute("data-theme-mode");
  document.documentElement.style.removeProperty("color-scheme");
  window.location.hash = "";
});

describe("课程视频首帧导航", () => {
  it("capabilities 未返回时已经显示课程视频入口", () => {
    render(<App />);

    expect(screen.getByRole("button", { name: "课程视频" })).toBeTruthy();
  });

  it("保留恢复的 videos 导航意图并呈现稳定工作台，而非权限拒绝", () => {
    window.location.hash = "#/videos";
    const { container } = render(<App />);

    expect(window.location.hash).toBe("#/videos");
    expect(
      screen.getByRole("heading", { level: 1, name: "课程视频" }),
    ).toBeTruthy();
    expect(screen.getByLabelText("课程视频学习工作台")).toBeTruthy();
    expect(
      container.querySelector('.video-workbench[data-state="loading"]'),
    ).toBeTruthy();
    expect(screen.queryByText("需要视频访问权限")).toBeNull();
  });

  it("capabilities 成功后同步决议 learnerCourseId，全程不渲染权限不足", async () => {
    window.location.hash = "#/videos";
    const capabilityRequest = deferred<AppCapabilities>();
    vi.mocked(invoke).mockImplementation(async (action) => {
      if (action === "capabilities") return capabilityRequest.promise as never;
      if (action === "sync_status") {
        return {
          status: "idle",
          last_success_at: null,
          last_run_status: null,
          last_run_at: null,
        } as never;
      }
      throw new Error(`Unexpected action: ${action}`);
    });
    const { container } = render(<App />);
    let announcedPermissionDenied = false;
    const observer = new MutationObserver(() => {
      announcedPermissionDenied ||=
        container.textContent?.includes("需要视频访问权限") ?? false;
    });
    observer.observe(container, {
      childList: true,
      subtree: true,
      characterData: true,
    });

    capabilityRequest.resolve(learnerCapabilities());
    expect(await screen.findByText("A 专属录像")).toBeTruthy();
    observer.disconnect();

    expect(announcedPermissionDenied).toBe(false);
    expect(screen.queryByText("需要视频访问权限")).toBeNull();
    expect(getCourseMedia).toHaveBeenCalledWith(12);
  });

  it("A 请求晚返回不会覆盖已切换到 B 的录像", async () => {
    window.location.hash = "#/videos";
    const courseARequest = deferred<ReturnType<typeof courseMedia>>();
    vi.mocked(invoke).mockImplementation(async (action) => {
      if (action === "capabilities") return learnerCapabilities() as never;
      if (action === "sync_status") {
        return {
          status: "idle",
          last_success_at: null,
          last_run_status: null,
          last_run_at: null,
        } as never;
      }
      throw new Error(`Unexpected action: ${action}`);
    });
    vi.mocked(getCourseMedia).mockImplementation(async (courseId) =>
      courseId === 12 ? courseARequest.promise : courseMedia(13, "B 专属录像"),
    );
    render(<App />);

    await waitFor(() => expect(getCourseMedia).toHaveBeenCalledWith(12));
    fireEvent.click(screen.getByRole("combobox", { name: "选择课程" }));
    fireEvent.click(screen.getByRole("option", { name: "课程 B" }));
    expect(await screen.findByText("B 专属录像")).toBeTruthy();

    courseARequest.resolve(courseMedia(12, "A 迟到录像"));
    await waitFor(() => expect(screen.queryByText("A 迟到录像")).toBeNull());
    expect(screen.getByText("B 专属录像")).toBeTruthy();
  });

  it("A→B 切换立即隔离选择，并只用 B courseId 与 B videoId 创建任务", async () => {
    window.location.hash = "#/videos";
    vi.mocked(invoke).mockImplementation(async (action) => {
      if (action === "capabilities") return learnerCapabilities() as never;
      if (action === "sync_status") {
        return {
          status: "idle",
          last_success_at: null,
          last_run_status: null,
          last_run_at: null,
        } as never;
      }
      throw new Error(`Unexpected action: ${action}`);
    });
    vi.mocked(getCourseMedia).mockImplementation(async (courseId) =>
      courseMedia(courseId, `${courseId === 12 ? "A" : "B"} 专属录像`),
    );
    render(<App />);

    await screen.findByText("A 专属录像");
    fireEvent.click(screen.getByRole("button", { name: "批量选择" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "选择 A 专属录像" }));
    expect(screen.getByLabelText("批量操作")).toBeTruthy();

    fireEvent.click(screen.getByRole("combobox", { name: "选择课程" }));
    fireEvent.click(screen.getByRole("option", { name: "课程 B" }));
    expect(screen.queryByText("A 专属录像")).toBeNull();
    expect(screen.queryByLabelText("批量操作")).toBeNull();
    expect(screen.getByLabelText("正在加载录像列表")).toBeTruthy();

    await screen.findByText("B 专属录像");
    fireEvent.click(screen.getByRole("button", { name: "批量选择" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "选择 B 专属录像" }));
    fireEvent.click(screen.getByRole("button", { name: "整理所选学习材料" }));
    await waitFor(() =>
      expect(startTranscriptBatch).toHaveBeenCalledWith(13, ["video-13"]),
    );
    expect(startTranscriptBatch).not.toHaveBeenCalledWith(13, ["video-12"]);
  });
});

describe("后台轮询状态回归", () => {
  const idleStatus: SyncStatus = {
    status: "idle",
    last_success_at: "2026-10-10T10:00:00+08:00",
    last_canvas_success_at: "2026-10-10T10:00:00+08:00",
    last_run_status: "success",
    last_run_at: "2026-10-10T10:00:00+08:00",
  };

  it("sync_status 请求未完成时，定时轮询不会启动第二个请求", async () => {
    const timers = controlIntervals();
    const pendingStatus = deferred<SyncStatus>();
    let syncCalls = 0;
    vi.mocked(invoke).mockImplementation(async (action) => {
      if (action === "capabilities") return new Promise<never>(() => undefined);
      if (action === "overview") return overview as never;
      if (action === "sync_status") {
        syncCalls += 1;
        return pendingStatus.promise as never;
      }
      throw new Error(`Unexpected action: ${action}`);
    });

    render(<App />);
    await waitFor(() => expect(syncCalls).toBe(1));
    const timer = timers.latest(5000);
    expect(timer).toBeTruthy();
    await act(async () => {
      timer?.tick();
      await Promise.resolve();
    });

    expect(syncCalls).toBe(1);
    pendingStatus.resolve(idleStatus);
  });

  it("页面隐藏时暂停轮询，重新可见时立即刷新", async () => {
    const timers = controlIntervals();
    let hidden = true;
    vi.spyOn(document, "hidden", "get").mockImplementation(() => hidden);
    vi.mocked(invoke).mockImplementation(async (action) => {
      if (action === "capabilities") return new Promise<never>(() => undefined);
      if (action === "overview") return overview as never;
      if (action === "sync_status") return idleStatus as never;
      throw new Error(`Unexpected action: ${action}`);
    });

    render(<App />);
    await act(async () => {
      timers.latest(5000)?.tick();
      await Promise.resolve();
    });
    expect(statusCallCount()).toBe(0);

    hidden = false;
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
      await Promise.resolve();
      await Promise.resolve();
    });
    await waitFor(() => expect(statusCallCount()).toBeGreaterThan(0));
    const callsWhileVisible = statusCallCount();

    hidden = true;
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
      timers.latest(5000)?.tick();
      await Promise.resolve();
    });
    expect(statusCallCount()).toBe(callsWhileVisible);

    hidden = false;
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
      await Promise.resolve();
    });
    await waitFor(() =>
      expect(statusCallCount()).toBeGreaterThan(callsWhileVisible),
    );
  });

  it("相同 sync 状态不会提交新的 App 页面 render", async () => {
    const timers = controlIntervals();
    vi.mocked(invoke).mockImplementation(async (action) => {
      if (action === "capabilities") return new Promise<never>(() => undefined);
      if (action === "overview") return overview as never;
      if (action === "sync_status") return { ...idleStatus } as never;
      throw new Error(`Unexpected action: ${action}`);
    });
    const onRender = vi.fn();

    render(
      <Profiler id="app" onRender={onRender}>
        <App />
      </Profiler>,
    );
    await screen.findByRole("heading", { level: 1, name: "概览" });
    await screen.findByRole("heading", { level: 2, name: "最新消息" });
    await waitFor(() => expect(statusCallCount()).toBeGreaterThanOrEqual(2));
    const commitsBeforeRefresh = onRender.mock.calls.length;

    await act(async () => {
      timers.latest(5000)?.tick();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(onRender).toHaveBeenCalledTimes(commitsBeforeRefresh);
  });
});

describe("课程切换时字幕任务轮询隔离", () => {
  it("单课程慢请求不重叠，切课程后迟到响应不会覆盖当前课程", async () => {
    window.location.hash = "#/videos";
    const timers = controlIntervals();
    const courseARequest = deferred<{ items: TranscriptJob[] }>();
    const courseBRequest = deferred<{ items: TranscriptJob[] }>();
    vi.mocked(invoke).mockImplementation(async (action) => {
      if (action === "capabilities") return learnerCapabilities() as never;
      if (action === "sync_status") {
        return {
          status: "idle",
          last_success_at: null,
          last_run_status: null,
          last_run_at: null,
        } as never;
      }
      throw new Error(`Unexpected action: ${action}`);
    });
    vi.mocked(getTranscriptJobs).mockImplementation((courseId) =>
      courseId === 12 ? courseARequest.promise : courseBRequest.promise,
    );
    vi.mocked(getCourseMedia).mockImplementation(async (courseId) =>
      courseMedia(courseId, `${courseId === 12 ? "A" : "B"} 专属录像`),
    );

    render(<App />);
    await waitFor(() => expect(getTranscriptJobs).toHaveBeenCalledWith(12));
    await act(async () => {
      timers.latest(2500)?.tick();
      await Promise.resolve();
    });
    expect(getTranscriptJobs).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("combobox", { name: "选择课程" }));
    fireEvent.click(screen.getByRole("option", { name: "课程 B" }));
    expect(await screen.findByText("B 专属录像")).toBeTruthy();
    await waitFor(() => expect(getTranscriptJobs).toHaveBeenCalledWith(13));
    await act(async () => {
      timers.latest(2500)?.tick();
      await Promise.resolve();
    });
    expect(getTranscriptJobs).toHaveBeenCalledTimes(2);

    const courseBJob: TranscriptJob = {
      id: "job-b",
      batch_id: "batch-b",
      source_id: "video-13",
      title: "B 专属录像",
      status: "completed",
      stage: "completed",
      progress: 100,
      attempts: 1,
    };
    await act(async () => {
      courseBRequest.resolve({ items: [courseBJob] });
      await Promise.resolve();
    });
    expect(
      await screen.findByRole("button", { name: "已整理 1" }),
    ).toBeTruthy();

    const courseAJob: TranscriptJob = {
      ...courseBJob,
      id: "job-a",
      source_id: "video-12",
      title: "A 迟到任务",
    };
    await act(async () => {
      courseARequest.resolve({ items: [courseAJob] });
      await Promise.resolve();
    });
    expect(screen.getByRole("button", { name: "已整理 1" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "已整理 0" })).toBeNull();
  });
});

describe("application theme", () => {
  it("applies the saved dark theme to the document root", async () => {
    settingsTheme = "dark";
    const { container } = render(<App />);

    expect(container.querySelector(".view-transition")).toBeTruthy();
    await waitFor(() => {
      expect(document.documentElement.dataset.theme).toBe("dark");
      expect(document.documentElement.dataset.themeMode).toBe("dark");
      expect(document.documentElement.style.colorScheme).toBe("dark");
    });
  });

  it("tracks system appearance while system mode is active", () => {
    let listener: (() => void) | undefined;
    const mediaQuery = {
      matches: true,
      media: "(prefers-color-scheme: dark)",
      onchange: null,
      addEventListener: vi.fn((_event: string, next: () => void) => {
        listener = next;
      }),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    };
    vi.stubGlobal(
      "matchMedia",
      vi.fn(() => mediaQuery),
    );

    const cleanupTheme = applyThemeMode("system");
    expect(document.documentElement.dataset.theme).toBe("dark");
    mediaQuery.matches = false;
    listener?.();
    expect(document.documentElement.dataset.theme).toBe("light");
    cleanupTheme();
    expect(mediaQuery.removeEventListener).toHaveBeenCalledWith(
      "change",
      listener,
    );
  });
});

describe("overview message detail", () => {
  it("在概览页原地打开同款详情弹窗，不切换消息视图", async () => {
    render(<App />);

    fireEvent.click(
      await screen.findByRole("button", { name: "打开消息详情：无链接邮件" }),
    );

    await waitFor(() => {
      expect(window.location.hash).toBe("#/overview");
      expect(
        screen
          .getByRole("button", { name: "概览", hidden: true })
          .getAttribute("aria-current"),
      ).toBe("page");
      expect(screen.queryByText("消息收件箱")).toBeNull();
      expect(getMessageDetail).toHaveBeenCalledWith("email", "mail-target");
    });
    expect((await screen.findByRole("dialog")).textContent).toContain(
      "邮件正文",
    );
  });

  it("概览 DTO 不依赖消息列表或 URL，关闭后仍停留在概览", async () => {
    render(<App />);
    const trigger = await screen.findByRole("button", {
      name: "打开消息详情：无链接邮件",
    });
    fireEvent.click(trigger);
    await screen.findByRole("dialog");
    fireEvent.click(screen.getByRole("button", { name: "关闭消息详情" }));

    expect(screen.queryByRole("dialog")).toBeNull();
    await waitFor(() => expect(document.activeElement).toBe(trigger));
    expect(window.location.hash).toBe("#/overview");
    expect(getMessages).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: /在 Canvas 打开/ })).toBeNull();
  });
});

describe("日程到作业提交工作台", () => {
  it("点击 Canvas 作业时留在 App 内并自动定位真实作业", async () => {
    window.location.hash = "#/calendar";
    const assignment = {
      course_id: "1",
      course_name: "人机交互",
      id: "2",
      name: "项目报告",
      description: "完成报告",
      due_at: "2026-10-19T21:59:00+08:00",
      unlock_at: null,
      lock_at: null,
      points_possible: 100,
      html_url: "https://oc.sjtu.edu.cn/courses/1/assignments/2",
      submission_types: ["online_text_entry"],
      submission: null,
      can_submit: true,
      requires_external_submission: false,
      categories: ["upcoming", "unsubmitted"],
    };
    vi.mocked(invoke).mockImplementation(async (action) => {
      if (action === "capabilities") return new Promise<never>(() => undefined);
      if (action === "sync_status") {
        return {
          status: "idle",
          last_success_at: null,
          last_run_status: null,
          last_run_at: null,
        } as never;
      }
      throw new Error(`Unexpected action: ${action}`);
    });
    const event = {
      id: 40,
      title: assignment.name,
      start_at: assignment.due_at,
      context_code: "course_1",
      context_name: assignment.course_name,
      assignment: { id: 2, name: assignment.name },
    };
    vi.mocked(getCalendar).mockResolvedValue({
      month_start: "2026-10-01T00:00:00+08:00",
      month_end: "2026-11-01T00:00:00+08:00",
      month_events: [event],
      upcoming_start: "2026-10-09T00:00:00+08:00",
      upcoming_end: "2026-11-09T00:00:00+08:00",
      upcoming_events: [event],
    });
    vi.mocked(getTimetableStatus).mockResolvedValue({
      state: "local",
      provider: "上海交通大学",
      lastSyncedAt: null,
      message: null,
      supportsOAuth: false,
      hasLocalData: false,
    });
    vi.mocked(getTimetableSchedule).mockResolvedValue({ events: [] });
    vi.mocked(getAssignments).mockResolvedValue({
      category: "upcoming",
      items: [assignment],
    } as never);
    vi.mocked(getAssignmentDetail).mockResolvedValue(assignment as never);

    render(<App />);
    fireEvent.click(
      await screen.findByRole("button", {
        name: "在 App 内完成作业：项目报告",
      }),
    );

    expect(
      await screen.findByRole("heading", { name: "在 App 内提交" }),
    ).toBeTruthy();
    expect(window.location.hash).toBe("#/assignments");
    expect(getAssignmentDetail).toHaveBeenCalledWith(1, 2);
  });
});

describe("页面标题焦点", () => {
  it("hash 导航后聚焦新页面标题，但首次加载不抢焦点", async () => {
    render(<App />);
    const initialHeading = screen.getByRole("heading", {
      level: 1,
      name: "概览",
    });
    expect(document.activeElement).not.toBe(initialHeading);

    window.location.hash = "#/messages";
    window.dispatchEvent(new HashChangeEvent("hashchange"));
    const heading = await screen.findByRole("heading", {
      level: 1,
      name: "消息",
    });
    await waitFor(() => expect(document.activeElement).toBe(heading));
  });
});

describe("云盘备份页面导航", () => {
  it("通过独立入口更新 hash 并渲染 BackupView", async () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "云盘备份" }));

    await waitFor(() => expect(window.location.hash).toBe("#/backup"));
    expect(
      screen.getByRole("heading", { level: 1, name: "云盘备份" }),
    ).toBeTruthy();
    expect(
      await screen.findByRole("heading", {
        level: 2,
        name: "Canvas、邮件与 AI 附件云端归档",
      }),
    ).toBeTruthy();
    expect(getBackupStatus).toHaveBeenCalledOnce();
  });
});

describe("global operation toast", () => {
  it("同步操作显示在全局右上角 Toast，页面内静态通知不再出现", async () => {
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "立即同步" }));

    const viewport = document.querySelector(".toast-viewport");
    expect(viewport?.getAttribute("aria-label")).toBe("操作通知");
    expect(
      await screen.findByText("同步请求已接受，正在后台执行。"),
    ).toBeTruthy();
    expect(document.querySelector(".global-notice")).toBeNull();
    expect(document.querySelector(".content > .notice")).toBeNull();
  });
});

describe("bounded course and video reads", () => {
  function capabilitiesFrom(request: Promise<AppCapabilities>) {
    const original = vi.mocked(invoke).getMockImplementation();
    vi.mocked(invoke).mockImplementation((action, payload) =>
      action === "capabilities"
        ? (request as never)
        : (original?.(action, payload) as never),
    );
  }
  function capabilityCalls() {
    return vi
      .mocked(invoke)
      .mock.calls.filter(([action]) => action === "capabilities").length;
  }
  async function flush() {
    await act(async () => {
      await Promise.resolve();
    });
  }

  it("stops the course skeleton after 30 seconds, ignores late success and permits a fresh retry", async () => {
    vi.useFakeTimers();
    window.location.hash = "#/videos";
    const request = deferred<AppCapabilities>();
    capabilitiesFrom(request.promise);
    const { container } = render(<App />);
    await flush();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(
      screen.getAllByText("课程权限确认超时，请重新检查。")[0],
    ).toBeTruthy();
    expect(
      container.querySelector('.video-workbench[data-state="loading"]'),
    ).toBeNull();
    await act(async () => {
      request.resolve(learnerCapabilities());
    });
    expect(getCourseMedia).not.toHaveBeenCalled();
    expect(screen.queryByText("A 专属录像")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "重新加载" }));
    await flush();
    expect(capabilityCalls()).toBe(2);
    expect(screen.getByText("A 专属录像")).toBeTruthy();
  });

  it("allows one stale course read replacement and bounds repeated retries", async () => {
    vi.useFakeTimers();
    window.location.hash = "#/videos";
    const request = deferred<AppCapabilities>();
    capabilitiesFrom(request.promise);
    const { unmount } = render(<App />);
    await flush();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    fireEvent.click(screen.getByRole("button", { name: "重新加载" }));
    await flush();
    expect(capabilityCalls()).toBe(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(
      screen.getAllByText("课程权限确认超时，请重新检查。")[0],
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "重新加载" }));
    await flush();
    expect(
      screen.getAllByText(
        "同一资源的两次读取均未响应，请重新打开应用后重试。",
      )[0],
    ).toBeTruthy();
    unmount();
    await act(async () => {
      request.resolve(learnerCapabilities());
    });
    expect(capabilityCalls()).toBe(2);
    expect(getCourseMedia).not.toHaveBeenCalled();
  });

  it("shows the first media failure instead of a permanent skeleton and retries successfully", async () => {
    window.location.hash = "#/videos";
    capabilitiesFrom(Promise.resolve(learnerCapabilities()));
    vi.mocked(getCourseMedia).mockRejectedValueOnce(
      new Error("录像服务暂不可用"),
    );
    const { container } = render(<App />);
    expect(await screen.findAllByText("录像服务暂不可用")).toBeTruthy();
    expect(
      container.querySelector('.video-workbench[data-state="loading"]'),
    ).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "重新加载" }));
    expect(await screen.findByText("A 专属录像")).toBeTruthy();
    expect(getCourseMedia).toHaveBeenCalledTimes(2);
  });

  it("times out media, ignores late results and starts a new read after the old read settles", async () => {
    vi.useFakeTimers();
    window.location.hash = "#/videos";
    capabilitiesFrom(Promise.resolve(learnerCapabilities()));
    const request = deferred<ReturnType<typeof courseMedia>>();
    vi.mocked(getCourseMedia).mockReturnValueOnce(request.promise);
    render(<App />);
    await flush();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(
      screen.getAllByText("课程录像读取超时，请检查连接后重新加载。")[0],
    ).toBeTruthy();
    await act(async () => {
      request.resolve(courseMedia(12, "迟到录像"));
    });
    expect(screen.queryByText("迟到录像")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "重新加载" }));
    await flush();
    expect(getCourseMedia).toHaveBeenCalledTimes(2);
    expect(screen.getByText("A 专属录像")).toBeTruthy();
  });

  it("shares the fresh media replacement when leaving and reentering videos", async () => {
    vi.useFakeTimers();
    window.location.hash = "#/videos";
    capabilitiesFrom(Promise.resolve(learnerCapabilities()));
    const request = deferred<ReturnType<typeof courseMedia>>();
    const replacement = deferred<ReturnType<typeof courseMedia>>();
    vi.mocked(getCourseMedia)
      .mockReturnValueOnce(request.promise)
      .mockReturnValueOnce(replacement.promise);
    render(<App />);
    await flush();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    fireEvent.click(screen.getByRole("button", { name: "重新加载" }));
    await flush();
    expect(getCourseMedia).toHaveBeenCalledTimes(2);
    fireEvent.click(screen.getByRole("button", { name: "概览" }));
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "课程视频" }));
    await flush();
    expect(getCourseMedia).toHaveBeenCalledTimes(2);
    await act(async () => {
      request.resolve(courseMedia(12, "旧录像不能覆盖"));
    });
    expect(screen.queryByText("旧录像不能覆盖")).toBeNull();
    await act(async () => {
      replacement.resolve(courseMedia(12, "恢复后的录像"));
    });
    expect(screen.getByText("恢复后的录像")).toBeTruthy();
  });

  it("shows loaded videos immediately when optional media capabilities never return", async () => {
    vi.useFakeTimers();
    window.location.hash = "#/videos";
    capabilitiesFrom(Promise.resolve(learnerCapabilities()));
    vi.mocked(getMediaCapabilities).mockReturnValueOnce(
      new Promise(() => undefined),
    );
    const { container, unmount } = render(<App />);
    await flush();
    expect(screen.getByText("A 专属录像")).toBeTruthy();
    expect(
      container.querySelector('.video-workbench[data-state="loading"]'),
    ).toBeNull();
    unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(getMediaCapabilities).toHaveBeenCalledTimes(1);
  });
});

describe("视频AI配置与材料状态反馈", () => {
  it("配置失败明确显示原因并保留录像，同时能打开设置", async () => {
    vi.mocked(getAICapabilities).mockRejectedValue(new Error("AI配置读取失败"));
    window.location.hash = "#/videos";
    const original = vi.mocked(invoke).getMockImplementation()!;
    vi.mocked(invoke).mockImplementation((action, payload) =>
      action === "capabilities"
        ? Promise.resolve(learnerCapabilities() as never)
        : original(action, payload),
    );
    const { container } = render(<App />);
    expect(await screen.findByText(/AI配置读取失败/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "配置 AI" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "配置 AI" }));
    await waitFor(() => expect(window.location.hash).toBe("#/settings"));
    expect(container.querySelector(".workspace-scene")).toBeTruthy();
  });

  it("材料状态读取失败可见且不会阻止视频列表加载", async () => {
    window.location.hash = "#/videos";
    vi.mocked(invoke).mockImplementation(async (action) => {
      if (action === "capabilities") return learnerCapabilities() as never;
      if (action === "sync_status") return { status: "idle" } as never;
      if (action === "overview") return overview as never;
      throw new Error(`Unexpected action: ${action}`);
    });
    vi.mocked(getTranscriptJobs).mockRejectedValue(
      new Error("材料状态读取失败"),
    );
    render(<App />);
    expect(await screen.findByText("A 专属录像")).toBeTruthy();
    expect(await screen.findByText("材料状态读取失败")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "重新检查" }));
    await waitFor(() => expect(getTranscriptJobs).toHaveBeenCalledTimes(2));
  });
});
