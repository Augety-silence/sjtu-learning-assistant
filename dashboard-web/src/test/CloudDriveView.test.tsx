// @vitest-environment jsdom

import { MotionConfig } from "motion/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CloudDriveView } from "@/components/CloudDriveView";
import {
  driveTransfers,
  resetDriveTransfersForTest,
} from "@/components/cloud-drive/driveTransfers";
import {
  BridgeInvocationError,
  cancelDriveTransfer,
  createDriveFolder,
  deleteDriveItems,
  downloadDriveItem,
  getBackupStatus,
  getDriveSpace,
  getDriveTransfers,
  listDrive,
  pickDriveUploadFiles,
  prepareDriveDownload,
  registerDriveDropFile,
  releaseDriveUploads,
  renameDriveItem,
  uploadDriveFile,
} from "@/lib/api";
import type { DriveItem, DrivePage } from "@/lib/types";
import {
  cleanup,
  fireEvent,
  render as renderWithProviders,
  screen,
  waitFor,
  within,
} from "@/test/render";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    BridgeInvocationError: actual.BridgeInvocationError,
    getBackupStatus: vi.fn(),
    startCloudBackup: vi.fn(),
    getDriveSpace: vi.fn(),
    listDrive: vi.fn(),
    createDriveFolder: vi.fn(),
    renameDriveItem: vi.fn(),
    moveDriveItems: vi.fn(),
    copyDriveItems: vi.fn(),
    deleteDriveItems: vi.fn(),
    pickDriveUploadFiles: vi.fn(),
    pickDriveUploadFolder: vi.fn(),
    registerDriveDropFile: vi.fn(),
    releaseDriveUploads: vi.fn(async () => ({ released: 0 })),
    uploadDriveFile: vi.fn(),
    prepareDriveDownload: vi.fn(async (_path, transferId) => ({
      transfer_id: transferId,
    })),
    downloadDriveItem: vi.fn(),
    revealDriveDownload: vi.fn(),
    getDriveTransfers: vi.fn(),
    cancelDriveTransfer: vi.fn(),
  };
});

function item(path: string[], extra: Partial<DriveItem> = {}): DriveItem {
  return {
    path,
    name: path[path.length - 1],
    is_directory: false,
    size: 2048,
    content_type: null,
    created_at: "2026-09-01T00:00:00Z",
    modified_at: "2026-10-20T08:30:00Z",
    ...extra,
  };
}

function page(
  path: string[],
  items: DriveItem[],
  extra: Partial<DrivePage> = {},
): DrivePage {
  return {
    path,
    page: 1,
    page_size: 20,
    total: items.length,
    pages: 1,
    folder_count: items.filter((entry) => entry.is_directory).length,
    file_count: items.filter((entry) => !entry.is_directory).length,
    truncated: false,
    query: "",
    sort: { by: "name", order: "asc" },
    items,
    ...extra,
  };
}

const rootItems = [
  item(["课程资料"], { is_directory: true, size: null }),
  item(["报告.pdf"], { size: 1024 * 1024 }),
];
const childItems = [item(["课程资料", "第一讲.pptx"])];

function render() {
  return renderWithProviders(
    <MotionConfig isStatic>
      <CloudDriveView />
    </MotionConfig>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  resetDriveTransfersForTest();
  window.location.hash = "/backup";
  vi.mocked(getDriveSpace).mockResolvedValue({
    capacity: 1024 ** 4,
    used: 233.01 * 1024 ** 3,
    available: 790.99 * 1024 ** 3,
    has_personal_space: true,
  });
  vi.mocked(listDrive).mockImplementation(async ({ path }) =>
    path.length === 0 ? page([], rootItems) : page(path, childItems),
  );
  vi.mocked(getDriveTransfers).mockResolvedValue({ items: [] });
  vi.mocked(getBackupStatus).mockResolvedValue({
    status: "idle",
    available: true,
    availability_message: null,
    counts: {
      canvas: 0,
      mail: 0,
      ready: 0,
      cloud_only: 0,
      missing_local: 0,
      total: 0,
    },
    progress: null,
    last_result: null,
  });
});

afterEach(() => {
  cleanup();
  resetDriveTransfersForTest();
});

describe("CloudDriveView", () => {
  it("像网页端一样展示空间用量、文件列表与统计", async () => {
    render();
    expect(
      await screen.findByRole("button", { name: "课程资料" }),
    ).toBeTruthy();
    expect(screen.getByText(/已使用233\.01GB/)).toBeTruthy();
    expect(screen.getByText(/剩余可用790\.99GB/)).toBeTruthy();
    expect(screen.getByText("共 2 项")).toBeTruthy();
    const table = screen.getByRole("table", { name: "文件列表" });
    expect(within(table).getByText("报告.pdf")).toBeTruthy();
    expect(within(table).getByText("1.0 MB")).toBeTruthy();
    expect(screen.getByText("共有 2 个项目")).toBeTruthy();
    expect(getBackupStatus).not.toHaveBeenCalled();
  });

  it("单击文件夹名称进入子目录，面包屑可返回个人空间", async () => {
    render();
    fireEvent.click(await screen.findByRole("button", { name: "课程资料" }));
    expect(await screen.findByText("第一讲.pptx")).toBeTruthy();
    expect(listDrive).toHaveBeenLastCalledWith(
      expect.objectContaining({ path: ["课程资料"], page: 1 }),
    );
    const crumbs = screen.getByRole("navigation", { name: "当前位置" });
    expect(
      within(crumbs)
        .getByRole("button", { name: "课程资料" })
        .getAttribute("aria-current"),
    ).toBe("page");
    fireEvent.click(within(crumbs).getByRole("button", { name: "个人空间" }));
    expect(await screen.findByText("报告.pdf")).toBeTruthy();
  });

  it("点击表头切换排序并设置 aria-sort", async () => {
    render();
    await screen.findByText("报告.pdf");
    fireEvent.click(screen.getByRole("button", { name: /大小/ }));
    await waitFor(() =>
      expect(listDrive).toHaveBeenLastCalledWith(
        expect.objectContaining({ sortBy: "size", order: "desc" }),
      ),
    );
    expect(
      screen
        .getByRole("columnheader", { name: /大小/ })
        .getAttribute("aria-sort"),
    ).toBe("descending");
  });

  it("搜索框防抖后按关键字查询当前文件夹", async () => {
    render();
    await screen.findByText("报告.pdf");
    fireEvent.change(
      screen.getByRole("searchbox", { name: "搜索当前文件夹" }),
      {
        target: { value: "报告" },
      },
    );
    await waitFor(() =>
      expect(listDrive).toHaveBeenLastCalledWith(
        expect.objectContaining({ query: "报告" }),
      ),
    );
  });

  it("选中后出现批量操作栏，删除需确认并移入回收站", async () => {
    vi.mocked(deleteDriveItems).mockResolvedValue({
      done: [["报告.pdf"]],
      failed: [],
    });
    render();
    fireEvent.click(
      await screen.findByRole("checkbox", { name: "选择“报告.pdf”" }),
    );
    const bar = screen.getByRole("region", { name: "批量操作" });
    expect(within(bar).getByText("已选择 1 项")).toBeTruthy();
    fireEvent.click(within(bar).getByRole("button", { name: "删除" }));
    const dialog = await screen.findByRole("dialog", {
      name: "删除“报告.pdf”？",
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "移入回收站" }));
    await waitFor(() =>
      expect(deleteDriveItems).toHaveBeenCalledWith([
        { path: ["报告.pdf"], is_directory: false },
      ]),
    );
    expect(await screen.findByText("已移入回收站 1 项")).toBeTruthy();
  });

  it("新建文件夹会校验名称并刷新列表", async () => {
    vi.mocked(createDriveFolder).mockResolvedValue(
      item(["新资料"], { is_directory: true }),
    );
    render();
    await screen.findByText("报告.pdf");
    fireEvent.click(screen.getByRole("button", { name: "新建文件夹" }));
    const dialog = await screen.findByRole("dialog", { name: "新建文件夹" });
    const input = within(dialog).getByRole("textbox", { name: "文件夹名称" });
    fireEvent.change(input, { target: { value: "a/b" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "创建" }));
    expect(await within(dialog).findByRole("alert")).toBeTruthy();
    expect(createDriveFolder).not.toHaveBeenCalled();
    fireEvent.change(input, { target: { value: "新资料" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "创建" }));
    await waitFor(() =>
      expect(createDriveFolder).toHaveBeenCalledWith([], "新资料"),
    );
    await waitFor(() =>
      expect(listDrive).toHaveBeenLastCalledWith(
        expect.objectContaining({ refresh: true }),
      ),
    );
  });

  it("右键菜单提供重命名，F2 同样可用", async () => {
    vi.mocked(renameDriveItem).mockResolvedValue(item(["总结.pdf"]));
    render();
    const row = (await screen.findByText("报告.pdf")).closest(
      "tr",
    ) as HTMLElement;
    fireEvent.contextMenu(row, { clientX: 100, clientY: 100 });
    const menu = screen.getByRole("menu", { name: "文件操作" });
    fireEvent.click(within(menu).getByRole("menuitem", { name: /重命名/ }));
    const dialog = await screen.findByRole("dialog", { name: "重命名" });
    const input = within(dialog).getByRole("textbox", {
      name: "文件名",
    }) as HTMLInputElement;
    expect(input.selectionEnd).toBe(2);
    fireEvent.change(input, { target: { value: "总结.pdf" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "确定" }));
    await waitFor(() =>
      expect(renameDriveItem).toHaveBeenCalledWith(
        ["报告.pdf"],
        "总结.pdf",
        false,
      ),
    );
  });

  it("双击文件发起下载并在传输列表中显示完成", async () => {
    vi.mocked(downloadDriveItem).mockResolvedValue({
      status: "downloaded",
      transfer_id: "d1",
      name: "报告.pdf",
      size: 1024 * 1024,
    });
    render();
    const row = (await screen.findByText("报告.pdf")).closest(
      "tr",
    ) as HTMLElement;
    fireEvent.doubleClick(row);
    await waitFor(() =>
      expect(downloadDriveItem).toHaveBeenCalledWith(
        ["报告.pdf"],
        expect.any(String),
      ),
    );
    const tray = await screen.findByRole("region", { name: "传输列表" });
    expect(await within(tray).findByText("全部传输已完成")).toBeTruthy();
    expect(
      within(tray).getByRole("button", { name: "在文件夹中显示“报告.pdf”" }),
    ).toBeTruthy();
  });

  it("可取消仍在等待保存位置的排队下载", async () => {
    let finishFirst!: (value: {
      status: "downloaded";
      transfer_id: string;
      name: string;
      size: number;
    }) => void;
    vi.mocked(downloadDriveItem).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finishFirst = resolve;
        }),
    );
    const first = driveTransfers.download(item(["第一份.pdf"]));
    const second = driveTransfers.download(item(["第二份.pdf"]));
    render();
    const cancel = await screen.findByRole("button", {
      name: "取消下载“第二份.pdf”",
    });
    fireEvent.click(cancel);
    finishFirst({
      status: "downloaded",
      transfer_id: "download-first",
      name: "第一份.pdf",
      size: 2048,
    });
    await first;
    await second;
    expect(downloadDriveItem).toHaveBeenCalledTimes(1);
    expect(
      screen
        .queryByText("第二份.pdf")
        ?.closest("li")
        ?.getAttribute("data-status"),
    ).toBe("cancelled");
  });

  it("登记期间取消下载后先通知后端再完成清理", async () => {
    let finishPrepare!: (result: { transfer_id: string }) => void;
    vi.mocked(prepareDriveDownload).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finishPrepare = resolve;
        }),
    );
    vi.mocked(cancelDriveTransfer).mockResolvedValue({
      transfer_id: "d",
      cancelled: true,
    });
    vi.mocked(downloadDriveItem).mockResolvedValue({ status: "cancelled" });
    const download = driveTransfers.download(item(["文件.pdf"]));
    await waitFor(() => expect(prepareDriveDownload).toHaveBeenCalledOnce());
    const id = driveTransfers.getState().tasks[0].id;
    await driveTransfers.cancel(id);
    expect(cancelDriveTransfer).not.toHaveBeenCalled();
    finishPrepare({ transfer_id: id });
    expect((await download).status).toBe("cancelled");
    expect(cancelDriveTransfer).toHaveBeenCalledWith(id);
    expect(downloadDriveItem).toHaveBeenCalledWith(["文件.pdf"], id);
    expect(
      vi.mocked(cancelDriveTransfer).mock.invocationCallOrder[0],
    ).toBeLessThan(vi.mocked(downloadDriveItem).mock.invocationCallOrder[0]);
  });

  it("保存对话框已启动时将取消发送给后端", async () => {
    let finishDownload!: (result: { status: "cancelled" }) => void;
    vi.mocked(downloadDriveItem).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finishDownload = resolve;
        }),
    );
    vi.mocked(cancelDriveTransfer).mockResolvedValue({
      transfer_id: "d",
      cancelled: true,
    });
    const download = driveTransfers.download(item(["文件.pdf"]));
    await waitFor(() => expect(downloadDriveItem).toHaveBeenCalledOnce());
    const id = driveTransfers.getState().tasks[0].id;
    await driveTransfers.cancel(id);
    expect(cancelDriveTransfer).toHaveBeenCalledWith(id);
    expect(driveTransfers.getState().tasks[0].message).toBe("正在取消…");
    finishDownload({ status: "cancelled" });
    expect((await download).status).toBe("cancelled");
  });

  it("丢弃超出记录上限的失败上传时释放凭证", async () => {
    vi.mocked(uploadDriveFile).mockRejectedValue(new Error("网络错误"));
    driveTransfers.enqueueUploads(
      Array.from({ length: 65 }, (_, index) => ({
        path: `token-${index}`,
        name: `file-${index}`,
        size: 1,
        too_large: false,
        relative_dir: [],
      })),
      [],
    );
    await waitFor(() =>
      expect(driveTransfers.getState().completion.seq).toBe(1),
    );
    expect(driveTransfers.getState().tasks).toHaveLength(60);
    expect(releaseDriveUploads).toHaveBeenCalledWith([
      "token-0",
      "token-1",
      "token-2",
      "token-3",
      "token-4",
    ]);
  });

  it("拖放后切换目录仍上传到发生拖放的目录", async () => {
    vi.mocked(registerDriveDropFile).mockResolvedValue({
      path: "drop-capability",
      name: "拖入.txt",
      size: 1,
      too_large: false,
      relative_dir: [],
    });
    vi.mocked(uploadDriveFile).mockResolvedValue({
      transfer_id: "upload",
      item: item(["拖入.txt"]),
    });
    render();
    const area = await screen.findByRole("region", {
      name: "“个人空间”中的内容",
    });
    const dataTransfer = { types: ["Files"], dropEffect: "none" };
    fireEvent.dragEnter(area, { dataTransfer });
    fireEvent.drop(area, { dataTransfer });
    fireEvent.click(screen.getByRole("button", { name: "课程资料" }));
    await screen.findByText("第一讲.pptx");
    window.dispatchEvent(
      new CustomEvent("sjtu-native-file-drop", {
        detail: {
          files: [{ path: "/tmp/拖入.txt", drop_token: "native-token" }],
        },
      }),
    );
    await waitFor(() =>
      expect(uploadDriveFile).toHaveBeenCalledWith(
        expect.objectContaining({ path: "drop-capability", parent: [] }),
      ),
    );
  });

  it("清理完成记录时保留正在等待处理的同名冲突", async () => {
    vi.mocked(uploadDriveFile).mockRejectedValue(
      new BridgeInvocationError("drive_upload", {
        code: "conflict",
        message: "同名",
      }),
    );
    driveTransfers.enqueueUploads(
      [
        {
          path: "conflict-token",
          name: "重复文件",
          size: 1,
          too_large: false,
          relative_dir: [],
        },
      ],
      [],
    );
    await waitFor(() =>
      expect(driveTransfers.getState().conflict).not.toBeNull(),
    );
    vi.mocked(downloadDriveItem).mockResolvedValue({
      status: "downloaded",
      transfer_id: "download",
      name: "文件.pdf",
      size: 1,
    });
    await Promise.all(
      Array.from({ length: 61 }, (_, index) =>
        driveTransfers.download(item([`文件-${index}.pdf`])),
      ),
    );
    expect(driveTransfers.getState().tasks).toHaveLength(61);
    expect(driveTransfers.getState().tasks[0].status).toBe("conflict");
    expect(releaseDriveUploads).not.toHaveBeenCalledWith(["conflict-token"]);
    driveTransfers.resolveConflict("skip");
    await waitFor(() => expect(driveTransfers.getState().conflict).toBeNull());
  });

  it("上传遇到同名冲突时询问处理方式，选择保留两者后重新上传", async () => {
    vi.mocked(pickDriveUploadFiles).mockResolvedValue({
      cancelled: false,
      files: [
        {
          path: "token-a",
          name: "报告.pdf",
          size: 10,
          too_large: false,
          relative_dir: [],
        },
      ],
    });
    let rejectFirst!: (reason: unknown) => void;
    vi.mocked(uploadDriveFile)
      .mockImplementationOnce(
        () =>
          new Promise((_, reject) => {
            rejectFirst = reject;
          }),
      )
      .mockResolvedValueOnce({
        transfer_id: "u1",
        item: item(["报告 (1).pdf"]),
      });
    render();
    await screen.findByText("报告.pdf");
    fireEvent.click(screen.getByRole("button", { name: "上传" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "上传文件" }));
    await waitFor(() => expect(uploadDriveFile).toHaveBeenCalledOnce());
    const syncTab = screen.getByRole("tab", { name: "本地同步" });
    fireEvent.mouseDown(syncTab, { button: 0 });
    fireEvent.click(syncTab);
    rejectFirst(
      new BridgeInvocationError("drive_upload", {
        code: "conflict",
        message: "同名",
      }),
    );
    const dialog = await screen.findByRole("dialog", { name: "存在同名文件" });
    fireEvent.click(within(dialog).getByRole("button", { name: "保留两者" }));
    await waitFor(() =>
      expect(uploadDriveFile).toHaveBeenLastCalledWith(
        expect.objectContaining({ conflict: "rename", parent: [] }),
      ),
    );
    await waitFor(() =>
      expect(listDrive).toHaveBeenLastCalledWith(
        expect.objectContaining({ refresh: true }),
      ),
    );
  });

  it("未登录时给出前往设置的引导而不是空白列表", async () => {
    vi.mocked(listDrive).mockRejectedValue(
      new BridgeInvocationError("drive_list", {
        code: "auth_required",
        message: "需要登录",
      }),
    );
    render();
    expect(await screen.findByText("尚未登录交大云盘")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "前往设置" }));
    expect(window.location.hash).toBe("#/settings");
  });

  it("空文件夹提供上传与新建入口", async () => {
    vi.mocked(listDrive).mockResolvedValue(page([], []));
    render();
    expect(await screen.findByText("此文件夹为空")).toBeTruthy();
    expect(screen.getByRole("button", { name: "上传文件" })).toBeTruthy();
  });

  it("本地同步作为子页签保留原备份工作流", async () => {
    render();
    await screen.findByText("报告.pdf");
    const tab = screen.getByRole("tab", { name: "本地同步" });
    fireEvent.mouseDown(tab, { button: 0 });
    fireEvent.click(tab);
    await waitFor(() => expect(getBackupStatus).toHaveBeenCalledOnce());
  });

  it("方向键移动选中项，Enter 打开文件夹", async () => {
    render();
    const folderRow = (
      await screen.findByText("课程资料", { selector: "button" })
    ).closest("tr") as HTMLElement;
    folderRow.focus();
    fireEvent.keyDown(folderRow, { key: "ArrowDown" });
    const table = screen.getByRole("table", { name: "文件列表" });
    const fileRow = within(table)
      .getByText("报告.pdf")
      .closest("tr") as HTMLElement;
    await waitFor(() =>
      expect(fileRow.getAttribute("aria-selected")).toBe("true"),
    );
    fireEvent.keyDown(fileRow, { key: "ArrowUp" });
    await waitFor(() =>
      expect(folderRow.getAttribute("aria-selected")).toBe("true"),
    );
    fireEvent.keyDown(folderRow, { key: "Enter" });
    expect(await screen.findByText("第一讲.pptx")).toBeTruthy();
  });
});
