// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "@/App";
import {
  getBackupStatus,
  getCourseMedia,
  getMediaCapabilities,
  getMessageDetail,
  getMessages,
  getSettings,
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
  ThemeMode,
} from "@/lib/types";
import { cleanup, fireEvent, render, screen, waitFor } from "@/test/render";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    getBackupStatus: vi.fn(),
    getCourseMedia: vi.fn(),
    getMediaCapabilities: vi.fn(),
    getMessageDetail: vi.fn(),
    getMessages: vi.fn(),
    getSettings: vi.fn(),
    getTranscriptJobs: vi.fn(),
    getVideoPlayback: vi.fn(),
    invoke: vi.fn(),
    startTranscriptBatch: vi.fn(),
  };
});

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

beforeEach(() => {
  window.location.hash = "#/overview";
  vi.clearAllMocks();
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
