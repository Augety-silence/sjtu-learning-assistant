// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BackupView } from "@/components/BackupView";
import { getBackupStatus, startCloudBackup } from "@/lib/api";
import type { BackupStatus } from "@/lib/types";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@/test/render";

vi.mock("@/lib/api", () => ({
  getBackupStatus: vi.fn(),
  startCloudBackup: vi.fn(),
}));

const idleStatus: BackupStatus = {
  status: "idle",
  available: true,
  availability_message: null,
  counts: { canvas: 12, mail: 5, ready: 14, missing_local: 3, total: 17 },
  progress: null,
  last_result: null,
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getBackupStatus).mockResolvedValue(idleStatus);
  vi.mocked(startCloudBackup).mockResolvedValue({ status: "started" });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("BackupView", () => {
  it("显示 idle 候选数、缺失统计与可用的备份操作", async () => {
    render(<BackupView />);

    expect(
      await screen.findByRole("heading", { name: "等待备份" }),
    ).toBeTruthy();
    expect(screen.getByText("Canvas 与邮件资料云端副本")).toBeTruthy();
    expect(screen.getByText("全部候选").parentElement?.textContent).toContain(
      "17",
    );
    expect(
      screen.getByText("Canvas 文件").parentElement?.textContent,
    ).toContain("12");
    expect(screen.getByText("邮件附件").parentElement?.textContent).toContain(
      "5",
    );
    expect(screen.getByText("可备份").parentElement?.textContent).toContain(
      "14",
    );
    expect(screen.getByText("本地缺失").parentElement?.textContent).toContain(
      "3",
    );
    expect(
      screen.getByRole("button", { name: "立即备份" }).hasAttribute("disabled"),
    ).toBe(false);
    expect(screen.queryByRole("progressbar")).toBeNull();
  });

  it("运行时约每秒轮询，并在 finished 后展示完整结果", async () => {
    vi.useFakeTimers();
    const running: BackupStatus = {
      ...idleStatus,
      status: "running",
      progress: { done: 4, total: 14, current_name: "week-4.pdf" },
    };
    const finished: BackupStatus = {
      ...idleStatus,
      status: "finished",
      progress: { done: 14, total: 14, current_name: null },
      last_result: {
        started_at: "2026-09-23T10:00:00+08:00",
        finished_at: "2026-09-23T10:01:00+08:00",
        uploaded: 9,
        skipped_existing: 4,
        skipped_missing_local: 1,
        failed: 0,
        failures: [],
      },
    };
    vi.mocked(getBackupStatus)
      .mockResolvedValueOnce(running)
      .mockResolvedValueOnce(finished);

    render(<BackupView />);
    await act(async () => Promise.resolve());
    const progress = screen.getByRole("progressbar", { name: "云盘备份进度" });
    expect(progress.getAttribute("aria-valuenow")).toBe("4");
    expect(progress.getAttribute("aria-valuemax")).toBe("14");
    expect(screen.getByText("week-4.pdf")).toBeTruthy();

    await act(async () => {
      vi.advanceTimersByTime(1000);
      await Promise.resolve();
    });

    expect(screen.getByRole("heading", { name: "备份已完成" })).toBeTruthy();
    expect(screen.getByText("已上传").parentElement?.textContent).toContain(
      "9",
    );
    expect(screen.getByText("云端已存在").parentElement?.textContent).toContain(
      "4",
    );
    expect(
      screen.getByText("本地缺失跳过").parentElement?.textContent,
    ).toContain("1");
    expect(screen.getByText("失败").parentElement?.textContent).toContain("0");
    expect(getBackupStatus).toHaveBeenCalledTimes(2);
  });

  it("already_running 后防重复并继续跟踪状态", async () => {
    const running: BackupStatus = {
      ...idleStatus,
      status: "running",
      progress: { done: 0, total: 14, current_name: null },
    };
    vi.mocked(getBackupStatus)
      .mockResolvedValueOnce(idleStatus)
      .mockResolvedValueOnce(running);
    vi.mocked(startCloudBackup).mockResolvedValue({
      status: "already_running",
    });
    render(<BackupView />);

    const start = await screen.findByRole("button", { name: "立即备份" });
    fireEvent.click(start);

    expect(
      await screen.findByText("已有云盘备份任务正在运行，已继续跟踪进度。"),
    ).toBeTruthy();
    expect(startCloudBackup).toHaveBeenCalledOnce();
    expect(
      screen
        .getByRole("button", { name: "备份进行中" })
        .hasAttribute("disabled"),
    ).toBe(true);
    expect(screen.getByRole("progressbar")).toBeTruthy();
  });

  it("Pan 不可用时解释原因、禁用开始但仍允许刷新", async () => {
    vi.mocked(getBackupStatus).mockResolvedValue({
      ...idleStatus,
      available: false,
      availability_message: "请先登录 SJTU Pan。",
    });
    render(<BackupView />);

    expect(await screen.findByText("请先登录 SJTU Pan。")).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "立即备份" }).hasAttribute("disabled"),
    ).toBe(true);
    const refresh = screen.getByRole("button", { name: "刷新备份状态" });
    expect(refresh.hasAttribute("disabled")).toBe(false);
    fireEvent.click(refresh);
    await waitFor(() => expect(getBackupStatus).toHaveBeenCalledTimes(2));
    expect(startCloudBackup).not.toHaveBeenCalled();
  });

  it("失败项仅展示安全来源、名称、云端路径和原因", async () => {
    vi.mocked(getBackupStatus).mockResolvedValue({
      ...idleStatus,
      status: "finished",
      last_result: {
        uploaded: 1,
        skipped_existing: 0,
        skipped_missing_local: 0,
        failed: 1,
        failures: [
          {
            source: "mail",
            name: "附件.zip",
            remote_path: "学习资料/邮件/附件.zip",
            error: "读取 /Users/student/private.zip 失败 token=super-secret",
          },
        ],
      },
    });
    render(<BackupView />);

    expect(await screen.findByRole("heading", { name: "失败项" })).toBeTruthy();
    expect(screen.getByText("附件.zip")).toBeTruthy();
    expect(screen.getByText("mail")).toBeTruthy();
    expect(screen.getByText("学习资料/邮件/附件.zip")).toBeTruthy();
    expect(document.body.textContent).not.toContain("/Users/");
    expect(document.body.textContent).not.toContain("super-secret");
    expect(document.body.textContent).not.toContain("token=");
  });
});
