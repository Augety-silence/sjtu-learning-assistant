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
  counts: {
    canvas: 12,
    mail: 5,
    ready: 14,
    cloud_only: 2,
    missing_local: 3,
    total: 17,
  },
  progress: null,
  last_result: null,
};

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  vi.mocked(getBackupStatus).mockResolvedValue(idleStatus);
  vi.mocked(startCloudBackup).mockResolvedValue({ status: "started" });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("BackupView", () => {
  it("显示 idle 候选数、缺失统计与可用的备份操作", async () => {
    render(<BackupView />);

    expect(
      await screen.findByRole("heading", { name: "等待归档" }),
    ).toBeTruthy();
    expect(screen.getByText("Canvas、邮件与 AI 附件云端归档")).toBeTruthy();
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
    expect(screen.getByText("仅云端").parentElement?.textContent).toContain(
      "2",
    );
    expect(screen.getByText("本地缺失").parentElement?.textContent).toContain(
      "3",
    );
    expect(
      screen
        .getByRole("button", { name: "归档至云端" })
        .hasAttribute("disabled"),
    ).toBe(false);
    expect(screen.queryByRole("progressbar")).toBeNull();
  });

  it("默认保留本地副本并显式传递安全选项", async () => {
    render(<BackupView />);

    const start = await screen.findByRole("button", { name: "归档至云端" });
    fireEvent.click(start);

    await waitFor(() => expect(startCloudBackup).toHaveBeenCalledWith(false));
    expect(
      await screen.findByText("归档请求已接受；本地副本将继续保留。"),
    ).toBeTruthy();
  });

  it("释放本地空间必须勾选并通过二次确认", async () => {
    const confirm = vi
      .spyOn(window, "confirm")
      .mockReturnValueOnce(false)
      .mockReturnValueOnce(true);
    render(<BackupView />);

    const option = await screen.findByRole("checkbox", {
      name: /归档后释放本地空间/,
    });
    fireEvent.click(option);
    const start = screen.getByRole("button", { name: "归档至云端" });
    fireEvent.click(start);
    expect(confirm).toHaveBeenCalledOnce();
    expect(startCloudBackup).not.toHaveBeenCalled();

    fireEvent.click(start);
    await waitFor(() => expect(startCloudBackup).toHaveBeenCalledWith(true));
    expect(confirm).toHaveBeenCalledTimes(2);
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
        local_removed: 13,
        failed: 0,
        failures: [],
      },
    };
    vi.mocked(getBackupStatus)
      .mockResolvedValueOnce(running)
      .mockResolvedValueOnce(finished);

    render(<BackupView />);
    await act(async () => Promise.resolve());
    const progress = screen.getByRole("progressbar", { name: "云端归档进度" });
    expect(progress.getAttribute("aria-valuenow")).toBe("4");
    expect(progress.getAttribute("aria-valuemax")).toBe("14");
    expect(screen.getByText("week-4.pdf")).toBeTruthy();

    await act(async () => {
      vi.advanceTimersByTime(1000);
      await Promise.resolve();
    });

    expect(screen.getByRole("heading", { name: "归档已完成" })).toBeTruthy();
    expect(screen.getByText("已上传").parentElement?.textContent).toContain(
      "9",
    );
    expect(screen.getByText("云端已存在").parentElement?.textContent).toContain(
      "4",
    );
    expect(
      screen.getByText("已安全移除本地文件").parentElement?.textContent,
    ).toContain("13");
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

    const start = await screen.findByRole("button", { name: "归档至云端" });
    fireEvent.click(start);

    expect(
      await screen.findByText("已有云端归档任务正在运行，已继续跟踪进度。"),
    ).toBeTruthy();
    expect(startCloudBackup).toHaveBeenCalledOnce();
    expect(startCloudBackup).toHaveBeenCalledWith(false);
    expect(
      screen
        .getByRole("button", { name: "归档进行中" })
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
      screen.getByText(/已统一移至“系统设置 → 交大云盘”管理/),
    ).toBeTruthy();
    expect(
      screen
        .getByRole("button", { name: "归档至云端" })
        .hasAttribute("disabled"),
    ).toBe(true);
    const refresh = screen.getByRole("button", { name: "刷新归档状态" });
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
