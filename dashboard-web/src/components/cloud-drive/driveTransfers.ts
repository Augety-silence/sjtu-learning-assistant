/**
 * 交大云盘传输队列（模块级存储）。
 *
 * 上传/下载在切换页面后仍需继续，因此队列放在组件外部，通过 useSyncExternalStore 订阅。
 * 上传串行执行，便于逐个处理同名冲突；下载串行弹出保存对话框，避免多个系统对话框叠加。
 */
import { useSyncExternalStore } from "react";
import {
  cancelDriveTransfer,
  downloadDriveItem,
  getDriveTransfers,
  prepareDriveDownload,
  releaseDriveUploads,
  uploadDriveFile,
} from "@/lib/api";
import type {
  DriveConflictStrategy,
  DriveItem,
  DriveLocalFile,
  DriveTransferStatus,
} from "@/lib/types";
import { errorCode, errorMessage, newTransferId, pathKey } from "./driveUtils";

export type LocalTransferStatus =
  | DriveTransferStatus
  | "queued"
  | "preparing"
  | "skipped";

export interface LocalTransfer {
  id: string;
  kind: "upload" | "download";
  name: string;
  total: number | null;
  done: number;
  status: LocalTransferStatus;
  message: string | null;
  /** 上传目标（用户所在的文件夹），用于完成后刷新列表。 */
  parent: string[];
  relativeDir: string[];
  localPath?: string;
  remotePath?: string[];
  batch: string;
  revealable?: boolean;
}

export type ConflictChoice = "overwrite" | "rename" | "skip" | "cancel";

export interface PendingConflict {
  id: string;
  name: string;
  remaining: number;
}

export interface DriveCompletion {
  seq: number;
  parents: string[];
  uploaded: number;
  failed: number;
  skipped: number;
}

interface StoreState {
  tasks: LocalTransfer[];
  conflict: PendingConflict | null;
  completion: DriveCompletion;
}

const ACTIVE: LocalTransferStatus[] = ["queued", "preparing", "running"];
const POLL_MS = 800;
const MAX_FINISHED = 60;

let state: StoreState = {
  tasks: [],
  conflict: null,
  completion: { seq: 0, parents: [], uploaded: 0, failed: 0, skipped: 0 },
};
const listeners = new Set<() => void>();
let uploadQueue: string[] = [];
let uploadRunning = false;
let downloadChain: Promise<unknown> = Promise.resolve();
let pollTimer: ReturnType<typeof setInterval> | null = null;
let conflictResolver:
  | ((choice: ConflictChoice, applyAll: boolean) => void)
  | null = null;
const batchPolicy = new Map<string, Exclude<ConflictChoice, "cancel">>();
const cancelledBatches = new Set<string>();
const cancelledDownloads = new Set<string>();
const startedDownloads = new Set<string>();
const registeredDownloads = new Set<string>();

function releaseCapabilities(items: LocalTransfer[]) {
  const tokens = [
    ...new Set(
      items
        .filter((item) => item.kind === "upload" && item.localPath)
        .map((item) => item.localPath as string),
    ),
  ];
  // The bridge bounds each release request; a long-running queue can retain
  // more finished records than that limit before it is trimmed.
  for (let offset = 0; offset < tokens.length; offset += 200) {
    void releaseDriveUploads(tokens.slice(offset, offset + 200)).catch(
      () => undefined,
    );
  }
}

function emit(next: Partial<StoreState>) {
  state = { ...state, ...next };
  for (const listener of listeners) listener();
  syncPolling();
}

function patchTask(id: string, patch: Partial<LocalTransfer>) {
  emit({
    tasks: state.tasks.map((task) =>
      task.id === id ? { ...task, ...patch } : task,
    ),
  });
}

function task(id: string) {
  return state.tasks.find((item) => item.id === id);
}

function trimFinished(tasks: LocalTransfer[]) {
  const finished = tasks.filter((item) => !isActiveTransfer(item));
  if (finished.length <= MAX_FINISHED) return tasks;
  const dropped = finished.slice(0, finished.length - MAX_FINISHED);
  releaseCapabilities(dropped);
  const drop = new Set(dropped.map((item) => item.id));
  return tasks.filter((item) => !drop.has(item.id));
}

function syncPolling() {
  const needsPoll = state.tasks.some(
    (item) => item.status === "running" || item.status === "preparing",
  );
  if (needsPoll && pollTimer === null) {
    pollTimer = setInterval(() => void poll(), POLL_MS);
  } else if (!needsPoll && pollTimer !== null) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

async function poll() {
  let items: Awaited<ReturnType<typeof getDriveTransfers>>["items"];
  try {
    ({ items } = await getDriveTransfers());
  } catch {
    return;
  }
  if (!Array.isArray(items)) return;
  const byId = new Map(items.map((item) => [item.id, item]));
  let changed = false;
  const tasks = state.tasks.map((local) => {
    const remote = byId.get(local.id);
    if (!remote) return local;
    if (local.status !== "running" && local.status !== "preparing")
      return local;
    if (
      remote.done === local.done &&
      remote.total === local.total &&
      local.status === "running"
    )
      return local;
    changed = true;
    // 最终状态以 invoke 返回值为准，轮询只更新进度，避免竞态把结果覆盖回 running。
    return {
      ...local,
      done: remote.done,
      total: remote.total ?? local.total,
      status: "running" as const,
      message: null,
    };
  });
  if (changed) emit({ tasks });
}

function askConflict(id: string, name: string, remaining: number) {
  return new Promise<{ choice: ConflictChoice; applyAll: boolean }>(
    (resolve) => {
      conflictResolver = (choice, applyAll) => {
        conflictResolver = null;
        emit({ conflict: null });
        resolve({ choice, applyAll });
      };
      emit({ conflict: { id, name, remaining } });
    },
  );
}

function remainingInBatch(batch: string) {
  return uploadQueue.filter((id) => task(id)?.batch === batch).length;
}

async function runUpload(id: string, summary: BatchSummary) {
  const job = task(id);
  if (!job || job.status !== "queued" || !job.localPath) return;
  let strategy: DriveConflictStrategy = batchPolicyStrategy(job.batch);
  for (;;) {
    patchTask(id, { status: "running", done: 0, message: null });
    try {
      await uploadDriveFile({
        path: job.localPath,
        parent: job.parent,
        conflict: strategy,
        transferId: id,
        relativeDir: job.relativeDir,
      });
      patchTask(id, {
        status: "done",
        done: job.total ?? task(id)?.done ?? 0,
        message: null,
      });
      summary.uploaded += 1;
      summary.parents.add(pathKey(job.parent));
      return;
    } catch (reason) {
      const code = errorCode(reason);
      if (code === "conflict" && strategy === "ask") {
        patchTask(id, { status: "conflict", message: "等待选择同名处理方式" });
        const { choice, applyAll } = await askConflict(
          id,
          job.name,
          remainingInBatch(job.batch),
        );
        if (choice === "cancel") {
          cancelledBatches.add(job.batch);
          releaseCapabilities([job]);
          patchTask(id, {
            status: "skipped",
            message: "已取消",
            localPath: undefined,
          });
          summary.skipped += 1;
          return;
        }
        if (applyAll) batchPolicy.set(job.batch, choice);
        if (choice === "skip") {
          releaseCapabilities([job]);
          patchTask(id, {
            status: "skipped",
            message: "已跳过同名文件",
            localPath: undefined,
          });
          summary.skipped += 1;
          return;
        }
        strategy = choice;
        continue;
      }
      if (code === "cancelled") {
        patchTask(id, { status: "cancelled", message: "已取消" });
        summary.skipped += 1;
        return;
      }
      patchTask(id, {
        status: "failed",
        message: errorMessage(reason, "上传失败"),
      });
      summary.failed += 1;
      return;
    }
  }
}

function batchPolicyStrategy(batch: string): DriveConflictStrategy {
  const policy = batchPolicy.get(batch);
  return policy === "overwrite" || policy === "rename" ? policy : "ask";
}

interface BatchSummary {
  uploaded: number;
  failed: number;
  skipped: number;
  parents: Set<string>;
}

async function drainUploads() {
  if (uploadRunning) return;
  uploadRunning = true;
  const summary: BatchSummary = {
    uploaded: 0,
    failed: 0,
    skipped: 0,
    parents: new Set(),
  };
  try {
    while (uploadQueue.length > 0) {
      const id = uploadQueue.shift() as string;
      const job = task(id);
      if (!job) continue;
      if (cancelledBatches.has(job.batch)) {
        releaseCapabilities([job]);
        patchTask(id, {
          status: "skipped",
          message: "已取消",
          localPath: undefined,
        });
        summary.skipped += 1;
        continue;
      }
      if (batchPolicy.get(job.batch) === "skip") {
        // “跳过并应用到全部”只影响冲突文件：先尝试上传，冲突时直接跳过。
        await runUploadSkippingConflicts(id, summary);
        continue;
      }
      await runUpload(id, summary);
    }
  } finally {
    uploadRunning = false;
    emit({
      tasks: trimFinished(state.tasks),
      completion: {
        seq: state.completion.seq + 1,
        parents: [...summary.parents],
        uploaded: summary.uploaded,
        failed: summary.failed,
        skipped: summary.skipped,
      },
    });
  }
}

async function runUploadSkippingConflicts(id: string, summary: BatchSummary) {
  const job = task(id);
  if (!job?.localPath) return;
  patchTask(id, { status: "running", done: 0, message: null });
  try {
    await uploadDriveFile({
      path: job.localPath,
      parent: job.parent,
      conflict: "ask",
      transferId: id,
      relativeDir: job.relativeDir,
    });
    patchTask(id, { status: "done", done: job.total ?? 0 });
    summary.uploaded += 1;
    summary.parents.add(pathKey(job.parent));
  } catch (reason) {
    const code = errorCode(reason);
    if (code === "conflict") {
      releaseCapabilities([job]);
      patchTask(id, {
        status: "skipped",
        message: "已跳过同名文件",
        localPath: undefined,
      });
      summary.skipped += 1;
    } else if (code === "cancelled") {
      patchTask(id, { status: "cancelled", message: "已取消" });
      summary.skipped += 1;
    } else {
      patchTask(id, {
        status: "failed",
        message: errorMessage(reason, "上传失败"),
      });
      summary.failed += 1;
    }
  }
}

export const driveTransfers = {
  getState: () => state,
  subscribe(listener: () => void) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },

  /** 加入上传队列；超过 2 GiB 的文件直接标记失败。返回实际排队数量。 */
  enqueueUploads(files: DriveLocalFile[], parent: string[]) {
    const batch = newTransferId("u");
    const created: LocalTransfer[] = files.map((file) => ({
      id: newTransferId("u"),
      kind: "upload",
      name: file.name,
      total: file.size,
      done: 0,
      status: file.too_large ? "failed" : "queued",
      message: file.too_large ? "单个文件不能超过 2 GiB" : null,
      parent: [...parent],
      relativeDir: [...(file.relative_dir ?? [])],
      localPath: file.too_large ? undefined : file.path,
      batch,
    }));
    const rejectedTokens = files
      .filter((file) => file.too_large)
      .map((file) => file.path);
    if (rejectedTokens.length > 0)
      void releaseDriveUploads(rejectedTokens).catch(() => undefined);
    const queued = created.filter((item) => item.status === "queued");
    uploadQueue.push(...queued.map((item) => item.id));
    emit({ tasks: [...state.tasks, ...created] });
    void drainUploads();
    return { queued: queued.length, rejected: created.length - queued.length };
  },

  /** 串行下载：每个文件先弹出系统保存对话框，再以不覆盖方式提交临时文件。 */
  download(item: DriveItem) {
    const id = newTransferId("d");
    emit({
      tasks: [
        ...state.tasks,
        {
          id,
          kind: "download",
          name: item.name,
          total: item.size,
          done: 0,
          status: "preparing",
          message: "等待选择保存位置…",
          parent: item.path.slice(0, -1),
          relativeDir: [],
          remotePath: [...item.path],
          batch: id,
        },
      ],
    });
    const run = async () => {
      startedDownloads.add(id);
      try {
        if (cancelledDownloads.delete(id)) {
          patchTask(id, { status: "cancelled", message: "已取消" });
          return { status: "cancelled" as const, id };
        }
        await prepareDriveDownload(item.path, id);
        registeredDownloads.add(id);
        if (cancelledDownloads.delete(id)) {
          const result = await cancelDriveTransfer(id);
          if (!result.cancelled) throw new Error("取消下载失败，请重试");
        }
        const result = await downloadDriveItem(item.path, id);
        if (result.status === "cancelled") {
          emit({ tasks: state.tasks.filter((entry) => entry.id !== id) });
          return { status: "cancelled" as const, id };
        }
        patchTask(id, {
          status: "done",
          done: result.size ?? item.size ?? 0,
          total: result.size ?? item.size,
          message: null,
          revealable: true,
          name: result.name || item.name,
        });
        return {
          status: "downloaded" as const,
          id,
          name: result.name || item.name,
        };
      } catch (reason) {
        const cancelled = errorCode(reason) === "cancelled";
        patchTask(id, {
          status: cancelled ? "cancelled" : "failed",
          message: cancelled ? "已取消" : errorMessage(reason, "下载失败"),
        });
        return {
          status: cancelled ? ("cancelled" as const) : ("failed" as const),
          id,
          message: errorMessage(reason, "下载失败"),
        };
      } finally {
        startedDownloads.delete(id);
        registeredDownloads.delete(id);
        cancelledDownloads.delete(id);
        emit({ tasks: trimFinished(state.tasks) });
      }
    };
    const next = downloadChain.then(run, run);
    downloadChain = next.catch(() => undefined);
    return next;
  },

  async cancel(id: string) {
    const current = task(id);
    if (!current) return;
    if (current.status === "queued") {
      uploadQueue = uploadQueue.filter((entry) => entry !== id);
      releaseCapabilities([current]);
      patchTask(id, {
        status: "cancelled",
        message: "已取消",
        localPath: undefined,
      });
      return;
    }
    if (current.kind === "download" && current.status === "preparing") {
      if (!registeredDownloads.has(id)) {
        cancelledDownloads.add(id);
        patchTask(
          id,
          startedDownloads.has(id)
            ? { message: "正在取消…" }
            : { status: "cancelled", message: "已取消" },
        );
        return;
      }
    }
    if (current.status !== "running" && current.status !== "preparing") return;
    patchTask(id, { message: "正在取消…" });
    try {
      const result = await cancelDriveTransfer(id);
      if (!result.cancelled && task(id)?.status === current.status)
        patchTask(id, { message: "无法取消，传输可能已结束" });
    } catch {
      if (task(id)?.status === current.status)
        patchTask(id, { message: "取消失败，请重试" });
    }
  },

  /** 失败或取消的上传可重试；本地授权仅在上传成功后才被消费。 */
  retry(id: string) {
    const current = task(id);
    if (
      !current ||
      current.kind !== "upload" ||
      !current.localPath ||
      !["failed", "cancelled", "skipped"].includes(current.status) ||
      current.message === "单个文件不能超过 2 GiB"
    )
      return;
    const batch = newTransferId("u");
    patchTask(id, { status: "queued", done: 0, message: null, batch });
    uploadQueue.push(id);
    void drainUploads();
  },

  clearFinished() {
    const retained = state.tasks.filter(
      (item) => ACTIVE.includes(item.status) || item.status === "conflict",
    );
    const retainedIds = new Set(retained.map((item) => item.id));
    releaseCapabilities(
      state.tasks.filter((item) => !retainedIds.has(item.id)),
    );
    emit({
      tasks: retained,
    });
  },

  resolveConflict(choice: ConflictChoice, applyAll = false) {
    conflictResolver?.(choice, applyAll);
  },
};

export function useDriveTransfers() {
  return useSyncExternalStore(
    driveTransfers.subscribe,
    driveTransfers.getState,
    driveTransfers.getState,
  );
}

export function isActiveTransfer(item: LocalTransfer) {
  return ACTIVE.includes(item.status) || item.status === "conflict";
}

/** 仅供测试：重置模块级状态。 */
export function resetDriveTransfersForTest() {
  if (pollTimer !== null) clearInterval(pollTimer);
  pollTimer = null;
  uploadQueue = [];
  uploadRunning = false;
  downloadChain = Promise.resolve();
  conflictResolver = null;
  batchPolicy.clear();
  cancelledBatches.clear();
  cancelledDownloads.clear();
  startedDownloads.clear();
  registeredDownloads.clear();
  state = {
    tasks: [],
    conflict: null,
    completion: { seq: 0, parents: [], uploaded: 0, failed: 0, skipped: 0 },
  };
  for (const listener of listeners) listener();
}
