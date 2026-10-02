// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "@/App";
import {
  getCalendar,
  getCourseMedia,
  getGradebook,
  getGrading,
  getMediaCapabilities,
  getRoster,
  getSettings,
  getVideoPlayback,
  getVideoSubtitles,
  invoke,
} from "@/lib/api";
import type { AppCapabilities } from "@/lib/types";
import { cleanup, fireEvent, render, screen, waitFor } from "@/test/render";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    getCalendar: vi.fn(),
    getCourseMedia: vi.fn(),
    getGradebook: vi.fn(),
    getGrading: vi.fn(),
    getMediaCapabilities: vi.fn(),
    getRoster: vi.fn(),
    getVideoPlayback: vi.fn(),
    getVideoSubtitles: vi.fn(),
    getSettings: vi.fn(),
    invoke: vi.fn(),
  };
});

let role: "student" | "teacher" = "student";

function capabilities(): AppCapabilities {
  const staff = role === "teacher";
  return {
    courses: {
      items: [
        {
          course_id: "12",
          course_name: "文本分析",
          roles: [role],
          role_source: "canvas",
          can_view_calendar: true,
          can_view_members: true,
          can_view_submissions: staff,
          can_manage_grades: staff,
          can_comment_submissions: staff,
        },
        {
          course_id: "13",
          course_name: "大模型导论",
          roles: [role],
          role_source: "canvas",
          can_view_calendar: true,
          can_view_members: true,
          can_view_submissions: staff,
          can_manage_grades: staff,
          can_comment_submissions: staff,
        },
      ],
    },
    media: {},
    update: { check_only: true, automatic_install: false },
    mcp: { transport: "stdio", read_only: true },
  };
}

beforeEach(async () => {
  role = "student";
  window.location.hash = "#/overview";
  vi.clearAllMocks();
  vi.stubGlobal(
    "matchMedia",
    vi.fn(() => ({
      matches: false,
      media: "(prefers-color-scheme: dark)",
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  );
  vi.mocked(getSettings).mockResolvedValue({ theme_mode: "system" } as never);
  vi.mocked(getCalendar).mockResolvedValue({
    month_start: "2026-09-01T00:00:00+08:00",
    month_end: "2026-10-01T00:00:00+08:00",
    month_events: [],
    upcoming_start: "2026-09-29T00:00:00+08:00",
    upcoming_end: "2026-10-06T00:00:00+08:00",
    upcoming_events: [],
  });
  vi.mocked(getCourseMedia).mockResolvedValue({
    items: [],
    subtitles: [],
    unmatched_subtitles: [],
    counts: { video: 0, audio: 0, subtitle: 0 },
  });
  vi.mocked(getGradebook).mockResolvedValue({
    course_id: "12",
    assignments: [{ id: 21, name: "项目", points_possible: 100 }],
    students: [],
    submissions: [],
    rows: [],
    statistics: {},
  });
  vi.mocked(getGrading).mockResolvedValue({ items: [] });
  vi.mocked(getMediaCapabilities).mockResolvedValue({
    video_screenshot_pdf: {
      available: true,
      reason: null,
    },
  });
  vi.mocked(getRoster).mockResolvedValue({ items: [] });
  vi.mocked(getVideoSubtitles).mockResolvedValue({
    status: "ready",
    message: "字幕已加载。",
    content_type: "text/vtt; charset=utf-8",
    vtt: "WEBVTT\n",
    cue_count: 0,
  });
  vi.mocked(getVideoPlayback).mockResolvedValue({
    available: true,
    transport: "remote_url",
    action: "play_remote_video",
    source_id: "sjtu-video:12:99",
    url: "https://videos.sjtu.edu.cn/v/99.mp4",
  });
  vi.mocked(invoke).mockImplementation(async (action) => {
    if (action === "capabilities") return capabilities() as never;
    if (action === "deadlines") return { items: [] } as never;
    if (action === "overview") {
      return {
        courses: 1,
        upcoming_deadlines: 0,
        unread_emails: 0,
        deadlines: [],
        messages: [],
      } as never;
    }
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
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  window.location.hash = "";
});

describe("Canvas Helper 页面集成", () => {
  it("学生只看到合并后的日程与 AI 助手入口", async () => {
    render(<App />);
    expect(await screen.findByRole("button", { name: "日程" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "课程视频" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "AI 助手" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "截止事项" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Canvas Agent" })).toBeNull();
    expect(screen.queryByRole("button", { name: "成绩" })).toBeNull();
    expect(screen.queryByRole("button", { name: "花名册" })).toBeNull();
    expect(screen.queryByRole("button", { name: "作业批改" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "日程" }));
    expect(
      await screen.findByRole("heading", { level: 1, name: "日程" }),
    ).toBeTruthy();
    expect(screen.getByRole("tab", { name: "月历" })).toBeTruthy();
    fireEvent.mouseDown(screen.getByRole("tab", { name: "待处理" }), {
      button: 0,
      ctrlKey: false,
    });
    await vi.waitFor(() => {
      expect(invoke).toHaveBeenCalledWith("deadlines", { window: "7d" });
    });
    expect(getCalendar).toHaveBeenCalled();
  });

  it("远端录像通过后端签发 URL 进入现有播放器", async () => {
    vi.mocked(getCourseMedia).mockResolvedValue({
      items: [
        {
          source_id: "sjtu-video:12:99",
          name: "远端第一讲",
          media_kind: "video",
          source: "video_space",
          course_name: "文本分析",
          downloadable: false,
          supports_slides_pdf: false,
          playback: {
            available: true,
            transport: "dashboard_action",
            action: "play_remote_video",
            source_id: "sjtu-video:12:99",
          },
        },
      ],
      subtitles: [],
      unmatched_subtitles: [],
      counts: { video: 1, audio: 0, subtitle: 0 },
    });
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "课程视频" }));
    expect(await screen.findByText("远端第一讲")).toBeTruthy();
    fireEvent.click(screen.getByRole("combobox", { name: "选择课程" }));
    expect(screen.getByRole("option", { name: "文本分析" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "大模型导论" })).toBeTruthy();
    fireEvent.click(screen.getByRole("option", { name: "文本分析" }));
    fireEvent.click(screen.getByRole("button", { name: /^播放 / }));

    await waitFor(() =>
      expect(getVideoPlayback).toHaveBeenCalledWith("sjtu-video:12:99"),
    );
    expect(document.querySelector("video source")?.getAttribute("src")).toBe(
      "https://videos.sjtu.edu.cn/v/99.mp4",
    );
    expect(screen.queryByRole("button", { name: "下载" })).toBeNull();
  });

  it("教师可进入课程管理页面并共享同一个 AI 助手入口", async () => {
    role = "teacher";
    render(<App />);
    await screen.findByRole("button", { name: "成绩" });

    fireEvent.click(screen.getByRole("button", { name: "成绩" }));
    expect(await screen.findByRole("heading", { name: "评分册" })).toBeTruthy();
    expect(getGradebook).toHaveBeenCalledWith(12);

    fireEvent.click(screen.getByRole("button", { name: "花名册" }));
    expect(
      await screen.findByRole("heading", { name: "课程花名册" }),
    ).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "作业批改" }));
    expect(
      await screen.findByRole("heading", { name: "作业批改" }),
    ).toBeTruthy();
    await waitFor(() => expect(getGrading).toHaveBeenCalledWith(12, 21));

    fireEvent.click(screen.getByRole("button", { name: "课程视频" }));
    expect(
      await screen.findByRole("heading", { name: "课程视频" }),
    ).toBeTruthy();
    expect(getCourseMedia).toHaveBeenCalledWith(12);

    expect(screen.getByRole("button", { name: "AI 助手" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Canvas Agent" })).toBeNull();
  });
});
