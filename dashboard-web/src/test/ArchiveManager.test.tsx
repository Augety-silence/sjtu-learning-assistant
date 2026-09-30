// @vitest-environment jsdom

import { readFileSync } from "node:fs";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ArchiveManager } from "@/components/archive/ArchiveManager";
import { RestoreDialog } from "@/components/archive/RestoreDialog";
import {
  authorizeArchiveRoot,
  executeRestore,
  getArchiveDetail,
  getArchiveJobEvents,
  getArchiveJobs,
  getArchiveList,
  planRestore,
  retryArchive,
  startArchive,
} from "@/lib/api";
import type {
  ArchiveEntry,
  ArchiveJob,
  ArchiveListItem,
  ArchiveVersion,
  RestorePlan,
} from "@/lib/types";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@/test/render";

vi.mock("@/lib/api", () => ({
  authorizeArchiveRoot: vi.fn(),
  executeRestore: vi.fn(),
  getArchiveDetail: vi.fn(),
  getArchiveJobEvents: vi.fn(),
  getArchiveJobs: vi.fn(),
  getArchiveList: vi.fn(),
  planRestore: vi.fn(),
  retryArchive: vi.fn(),
  startArchive: vi.fn(),
}));

const version: ArchiveVersion = {
  id: "version-1",
  version_number: 1,
  size: 2048,
  file_type: "application/pdf",
  mtime_ns: 1_780_000_000_000_000_000,
  archived_at: "2026-09-27T08:00:00+08:00",
  sha256: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  cloud_remote_id: "remote-1",
  cloud_remote_path: ["SJTU Learning Assistant", "Archive", "week-1.pdf"],
  cloud_etag: null,
  status: "archived",
  last_error: null,
  retry_count: 0,
};

const entry: ArchiveListItem = {
  id: "entry-1",
  filename: "week-1.pdf",
  original_abs_path: "/Users/student/course/week-1.pdf",
  archive_root_snapshot: "/Users/student/course",
  relative_path: "week-1.pdf",
  restore_capability: "original_path",
  source_kind: "user_file",
  entry_status: "archived",
  status: "archived",
  version_status: "archived",
  size_bytes: 2048,
  cloud_path: ["SJTU Learning Assistant", "Archive", "week-1.pdf"],
  archived_at: "2026-09-27T08:00:00+08:00",
  sha256: version.sha256,
  current_version_id: version.id,
  current_version_number: version.version_number,
  last_error: null,
  retry_count: 0,
  created_at: "2026-09-27T08:00:00+08:00",
  updated_at: "2026-09-27T08:00:00+08:00",
};

const detailEntry: ArchiveEntry = { ...entry, versions: [version] };

const failedJob: ArchiveJob = {
  id: "job-failed",
  idempotency_key: "archive:1",
  kind: "archive",
  entry_id: entry.id,
  version_id: version.id,
  status: "failed",
  bytes_total: 2048,
  bytes_done: 1024,
  conflict_policy: null,
  last_error: "上传失败（TimeoutError）。",
  attempt_count: 1,
  created_at: "2026-09-27T08:00:00+08:00",
  started_at: "2026-09-27T08:00:00+08:00",
  finished_at: "2026-09-27T08:01:00+08:00",
  retryable: true,
};

const failedRestoreJob: ArchiveJob = {
  ...failedJob,
  id: "restore-failed",
  kind: "restore",
  status: "failed",
  retryable: true,
};

const downloadingJob: ArchiveJob = {
  ...failedJob,
  id: "job-downloading",
  kind: "restore",
  status: "downloading",
  bytes_done: 512,
  last_error: null,
  retryable: false,
};

const restorePlan: RestorePlan = {
  job: {
    ...downloadingJob,
    id: "restore-plan",
    status: "planned",
    bytes_done: 0,
  },
  target: "/Users/student/Downloads/week-1.pdf",
  missing_directories: ["/Users/student/Downloads/course"],
  existing: {
    kind: "file",
    size: 1024,
    mtime_ns: 1_700_000_000_000_000_000,
    sha256: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  },
  comparison: {
    size_matches: false,
    mtime_matches: false,
    hash_matches: false,
  },
  expected: { size: 2048, mtime_ns: version.mtime_ns, sha256: version.sha256 },
  requires_directory_confirmation: true,
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getArchiveList).mockResolvedValue({
    items: [entry],
    limit: 20,
    offset: 0,
    total: 1,
    next_cursor: null,
  });
  vi.mocked(getArchiveDetail).mockResolvedValue(detailEntry);
  vi.mocked(getArchiveJobs).mockResolvedValue({
    items: [failedJob, failedRestoreJob, downloadingJob],
  });
  vi.mocked(getArchiveJobEvents).mockResolvedValue({
    items: [
      {
        id: "event-1",
        job_id: failedJob.id,
        event_type: "failed",
        status: "failed",
        message: "上传失败（TimeoutError）。",
        details: {},
        bytes_done: 1024,
        bytes_total: 2048,
        created_at: "2026-09-27T08:01:00+08:00",
      },
    ],
  });
  vi.mocked(startArchive).mockResolvedValue({
    ...failedJob,
    status: "completed",
  });
  vi.mocked(retryArchive).mockResolvedValue({
    ...failedJob,
    status: "completed",
  });
  vi.mocked(authorizeArchiveRoot).mockResolvedValue({
    id: "root-1",
    path: "/Users/student/Downloads",
    source: "native_picker",
  });
  vi.mocked(planRestore).mockResolvedValue(restorePlan);
  vi.mocked(executeRestore).mockResolvedValue({
    ...restorePlan.job,
    status: "completed",
    bytes_done: 2048,
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("ArchiveManager", () => {
  it("支持搜索、状态筛选、详情、真实进度、事件与失败重试", async () => {
    render(<ArchiveManager />);

    expect(await screen.findByText("week-1.pdf")).toBeTruthy();
    expect(getArchiveList).toHaveBeenCalledWith({
      limit: 20,
      query: undefined,
      status: undefined,
      sort: "updated_desc",
      cursor: undefined,
    });
    expect(getArchiveDetail).not.toHaveBeenCalled();
    expect(
      screen.getByText("SJTU Learning Assistant/Archive/week-1.pdf"),
    ).toBeTruthy();
    expect(
      screen
        .getByRole("progressbar", { name: "恢复进度" })
        .getAttribute("aria-valuenow"),
    ).toBe("25");

    fireEvent.change(screen.getByPlaceholderText("搜索全部文件名或相对路径"), {
      target: { value: "week" },
    });
    await waitFor(() =>
      expect(getArchiveList).toHaveBeenLastCalledWith(
        expect.objectContaining({ query: "week", cursor: undefined }),
      ),
    );
    fireEvent.change(screen.getByLabelText("状态"), {
      target: { value: "archived" },
    });
    await waitFor(() =>
      expect(getArchiveList).toHaveBeenLastCalledWith(
        expect.objectContaining({ status: "archived", cursor: undefined }),
      ),
    );

    const fileButton = screen.getByRole("button", { name: /week-1.pdf/ });
    fileButton.focus();
    fireEvent.click(fileButton);
    const drawer = await screen.findByRole("dialog", { name: "week-1.pdf" });
    expect(getArchiveDetail).toHaveBeenCalledOnce();
    expect(drawer.textContent).toContain("SHA-256");
    expect(drawer.textContent).toContain(
      "SJTU Learning Assistant/Archive/week-1.pdf",
    );
    await waitFor(() =>
      expect(document.activeElement).toBe(
        screen.getByRole("button", { name: "关闭档案详情" }),
      ),
    );
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(document.activeElement).toBe(fileButton));

    fireEvent.click(screen.getAllByRole("button", { name: /任务/ })[0]);
    expect(await screen.findByText("failed")).toBeTruthy();
    const jobsPanel = screen.getByRole("region", { name: "任务与事件" });
    const retryButtons = within(jobsPanel).getAllByRole("button", {
      name: /重试/,
    });
    expect(retryButtons).toHaveLength(2);
    fireEvent.click(retryButtons[0]);
    await waitFor(() =>
      expect(retryArchive).toHaveBeenCalledWith("job-failed"),
    );
    fireEvent.click(retryButtons[1]);
    await waitFor(() =>
      expect(retryArchive).toHaveBeenCalledWith("restore-failed"),
    );
  });

  it("使用后端分页并支持批量选择后逐项恢复", async () => {
    const many = Array.from({ length: 20 }, (_, index) => ({
      ...entry,
      id: "entry-" + index,
      filename: "file-" + index + ".pdf",
      current_version_id: "version-" + index,
    }));
    vi.mocked(getArchiveList)
      .mockResolvedValueOnce({
        items: many,
        limit: 20,
        offset: 0,
        total: 21,
        next_cursor: "cursor-20",
      })
      .mockResolvedValueOnce({
        items: [],
        limit: 20,
        offset: 20,
        total: 21,
        next_cursor: null,
      });
    render(<ArchiveManager />);

    await screen.findByText("file-0.pdf");
    fireEvent.click(screen.getByRole("button", { name: "下一页" }));
    await waitFor(() =>
      expect(getArchiveList).toHaveBeenLastCalledWith(
        expect.objectContaining({ cursor: "cursor-20" }),
      ),
    );
    expect(screen.getByText(/第 2 页.*共 21 条/)).toBeTruthy();

    fireEvent.change(screen.getByLabelText("排序"), {
      target: { value: "name_asc" },
    });
    await waitFor(() =>
      expect(getArchiveList).toHaveBeenLastCalledWith(
        expect.objectContaining({ sort: "name_asc", cursor: undefined }),
      ),
    );
    expect(screen.getByText(/第 1 页/)).toBeTruthy();

    cleanup();
    vi.mocked(getArchiveList).mockResolvedValue({
      items: [entry],
      limit: 20,
      offset: 0,
      total: 1,
      next_cursor: null,
    });
    vi.mocked(getArchiveDetail).mockResolvedValue(detailEntry);
    render(<ArchiveManager />);
    await screen.findByText("week-1.pdf");
    fireEvent.click(screen.getByRole("checkbox", { name: "选择 week-1.pdf" }));
    fireEvent.click(screen.getByRole("button", { name: "逐项恢复所选" }));
    expect(
      await screen.findByRole("dialog", { name: /恢复“week-1.pdf”/ }),
    ).toBeTruthy();
  });

  it("呈现 Bridge 离线错误并允许重试", async () => {
    vi.mocked(getArchiveList).mockRejectedValue(
      new Error("桌面 Bridge 不可用。"),
    );
    render(<ArchiveManager />);
    expect(await screen.findByText("桌面 Bridge 当前离线")).toBeTruthy();
    expect(
      screen.getAllByRole("button", { name: "重试" }).length,
    ).toBeGreaterThan(0);
  });
});

describe("RestoreDialog", () => {
  it("旧记录禁用原路径，目录只能经原生 picker 授权", async () => {
    const legacy: ArchiveEntry = {
      ...detailEntry,
      original_abs_path: null,
      restore_capability: "choose_location",
    };
    render(
      <RestoreDialog
        entry={legacy}
        version={version}
        onClose={vi.fn()}
        onComplete={vi.fn()}
      />,
    );

    expect(
      screen
        .getByRole("radio", { name: /恢复到原路径/ })
        .hasAttribute("disabled"),
    ).toBe(true);
    expect(screen.queryByRole("textbox")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /选择目录并检查/ }));
    await waitFor(() => expect(authorizeArchiveRoot).toHaveBeenCalledOnce());
    expect(planRestore).toHaveBeenCalledWith(legacy.id, {
      versionId: version.id,
      mode: "choose_location",
      authorizedRootId: "root-1",
    });
  });

  it.each(["skip", "save_as", "overwrite", "compare"] as const)(
    "提交冲突策略 %s 与目录确认",
    async (policy) => {
      const onComplete = vi.fn();
      render(
        <RestoreDialog
          entry={detailEntry}
          version={version}
          onClose={vi.fn()}
          onComplete={onComplete}
        />,
      );
      fireEvent.click(screen.getByRole("button", { name: "检查原路径" }));
      await screen.findByText("目标冲突处理");
      fireEvent.click(
        screen.getByRole("radio", {
          name: new RegExp(
            policy === "save_as"
              ? "另存为"
              : policy === "overwrite"
                ? "覆盖"
                : policy === "compare"
                  ? "仅对照"
                  : "跳过",
          ),
        }),
      );
      fireEvent.click(screen.getByRole("checkbox", { name: /确认创建上述/ }));
      fireEvent.click(
        screen.getByRole("button", {
          name: policy === "compare" ? "执行对照" : "开始恢复",
        }),
      );
      await waitFor(() =>
        expect(executeRestore).toHaveBeenCalledWith(
          "restore-plan",
          policy,
          true,
        ),
      );
      expect(onComplete).toHaveBeenCalled();
    },
  );

  it("Escape 关闭并恢复焦点，样式禁用 reduced-motion 位移", async () => {
    const trigger = document.createElement("button");
    document.body.append(trigger);
    trigger.focus();
    const onClose = vi.fn();
    render(
      <RestoreDialog
        entry={entry}
        version={version}
        onClose={onClose}
        onComplete={vi.fn()}
      />,
    );
    await waitFor(() =>
      expect(document.activeElement).toBe(
        screen.getByRole("button", { name: "关闭恢复对话框" }),
      ),
    );
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledOnce();
    cleanup();
    await waitFor(() => expect(document.activeElement).toBe(trigger));

    const css = readFileSync(
      process.cwd() + "/src/components/archive/archive.css",
      "utf8",
    );
    expect(css).toContain("@media (prefers-reduced-motion: reduce)");
    expect(css).toMatch(/archive-job-toggle[\s\S]+transition: none/);
  });
});
