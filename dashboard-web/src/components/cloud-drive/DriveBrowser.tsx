import {
  ChevronLeft,
  ChevronRight,
  CloudOff,
  Copy,
  Download,
  Eye,
  FileUp,
  FolderInput,
  FolderOpen,
  FolderPlus,
  FolderUp,
  HardDrive,
  Info,
  KeyRound,
  PanelRight,
  PanelRightClose,
  Pencil,
  RefreshCw,
  Search,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import {
  type KeyboardEvent as ReactKeyboardEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { EmptyState, ErrorState, LoadingState } from "@/components/States";
import { useToast } from "@/components/Toast";
import { Button } from "@/components/ui/Button";
import {
  copyDriveItems,
  createDriveFolder,
  deleteDriveItems,
  getDriveSpace,
  listDrive,
  moveDriveItems,
  pickDriveUploadFiles,
  pickDriveUploadFolder,
  registerDriveDropFile,
  renameDriveItem,
} from "@/lib/api";
import type {
  DriveBatchResult,
  DriveItem,
  DriveLocalFile,
  DriveLocalPick,
  DrivePage,
  DriveSortField,
  DriveSortOrder,
  DriveSpace,
} from "@/lib/types";
import { cn } from "@/lib/utils";
import { DriveDetails } from "./DriveDetails";
import { DeleteDialog, NameDialog, TransferDialog } from "./DriveDialogs";
import { DriveMenu, type DriveMenuItem, type MenuAnchor } from "./DriveMenu";
import { DriveTable, type RowIntent } from "./DriveTable";
import { driveTransfers, useDriveTransfers } from "./driveTransfers";
import {
  errorCode,
  errorMessage,
  formatCapacity,
  itemKey,
  pathKey,
  ROOT_LABEL,
} from "./driveUtils";
import { type NativeDroppedFile, useNativeFileDrop } from "./useNativeFileDrop";

const PAGE_SIZES = [20, 50, 100];
const DEFAULT_ORDER: Record<DriveSortField, DriveSortOrder> = {
  name: "asc",
  modified: "desc",
  size: "desc",
};

type Dialog =
  | { kind: "mkdir" }
  | { kind: "rename"; item: DriveItem }
  | { kind: "delete"; items: DriveItem[] }
  | { kind: "move" | "copy"; items: DriveItem[] };

type MenuState =
  | {
      kind: "items";
      anchor: MenuAnchor;
      targets: DriveItem[];
      trigger: HTMLElement | null;
    }
  | {
      kind: "upload" | "background";
      anchor: MenuAnchor;
      trigger: HTMLElement | null;
    };

interface LoadError {
  code: string;
  message: string;
}

function isTypingTarget(target: EventTarget | null) {
  if (!(target instanceof HTMLElement)) return false;
  return (
    target.isContentEditable ||
    ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)
  );
}

function refs(items: DriveItem[]) {
  return items.map((item) => ({
    path: item.path,
    is_directory: item.is_directory,
  }));
}

/** 交大云盘文件浏览器：浏览、搜索、上传、下载与整理个人空间中的文件。 */
export function DriveBrowser({ active }: { active: boolean }) {
  const { showToast } = useToast();
  const { conflict, completion } = useDriveTransfers();
  const [path, setPath] = useState<string[]>([]);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(PAGE_SIZES[0]);
  const [sortBy, setSortBy] = useState<DriveSortField>("name");
  const [order, setOrder] = useState<DriveSortOrder>("asc");
  const [queryInput, setQueryInput] = useState("");
  const [query, setQuery] = useState("");
  const [data, setData] = useState<DrivePage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<LoadError | null>(null);
  const [space, setSpace] = useState<DriveSpace | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [anchorIndex, setAnchorIndex] = useState<number | null>(null);
  const [focusIndex, setFocusIndex] = useState(0);
  const [detailsOpen, setDetailsOpen] = useState(true);
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const [menu, setMenu] = useState<MenuState | null>(null);
  const [busyKeys, setBusyKeys] = useState<Set<string>>(new Set());
  const [reloadToken, setReloadToken] = useState(0);
  const requestRef = useRef(0);
  const spaceRequestRef = useRef(0);
  const refreshNextRef = useRef(false);
  const searchRef = useRef<HTMLInputElement>(null);
  const uploadButtonRef = useRef<HTMLButtonElement>(null);
  const lastCompletionRef = useRef(completion.seq);

  const items = data?.items ?? [];
  const selectedItems = useMemo(
    () => items.filter((item) => selected.has(itemKey(item))),
    [items, selected],
  );
  const currentName = path.length ? path[path.length - 1] : ROOT_LABEL;

  const loadSpace = useCallback(() => {
    const request = ++spaceRequestRef.current;
    getDriveSpace()
      .then((result) => {
        if (request === spaceRequestRef.current) setSpace(result);
      })
      .catch(() => {
        if (request === spaceRequestRef.current) setSpace(null);
      });
  }, []);

  const reload = useCallback((refresh = true) => {
    refreshNextRef.current = refresh;
    setReloadToken((value) => value + 1);
  }, []);

  useEffect(() => {
    loadSpace();
  }, [loadSpace]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setQuery(queryInput.trim());
      setPage(1);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [queryInput]);

  useEffect(() => {
    const request = ++requestRef.current;
    const refresh = refreshNextRef.current;
    refreshNextRef.current = false;
    setLoading(true);
    setError(null);
    // 同一文件夹/同一搜索内翻页、排序、刷新时保留旧列表，避免表格卸载导致键盘焦点丢失。
    setData((previous) =>
      previous &&
      pathKey(previous.path) === pathKey(path) &&
      previous.query === query
        ? previous
        : null,
    );
    listDrive({ path, page, pageSize, sortBy, order, query, refresh })
      .then((result) => {
        if (request !== requestRef.current) return;
        if (result.page !== page) setPage(result.page);
        setData(result);
        setSelected((previous) => {
          const keys = new Set(result.items.map(itemKey));
          const next = new Set([...previous].filter((key) => keys.has(key)));
          return next.size === previous.size ? previous : next;
        });
        setFocusIndex((index) =>
          Math.min(index, Math.max(0, result.items.length - 1)),
        );
      })
      .catch((reason) => {
        if (request !== requestRef.current) return;
        setData(null);
        setError({
          code: errorCode(reason),
          message: errorMessage(reason, "无法读取交大云盘。"),
        });
      })
      .finally(() => {
        if (request === requestRef.current) setLoading(false);
      });
  }, [path, page, pageSize, sortBy, order, query, reloadToken]);

  // 上传批次结束后，若涉及当前文件夹则刷新列表与容量。
  useEffect(() => {
    if (completion.seq === lastCompletionRef.current) return;
    lastCompletionRef.current = completion.seq;
    if (completion.uploaded > 0) loadSpace();
    if (completion.parents.includes(pathKey(path))) reload(true);
  }, [completion, path, reload, loadSpace]);

  const navigate = useCallback((next: string[]) => {
    setPath(next);
    setPage(1);
    setQueryInput("");
    setQuery("");
    setSelected(new Set());
    setAnchorIndex(null);
    setFocusIndex(0);
    setMenu(null);
  }, []);

  const selectOnly = (item: DriveItem, index: number) => {
    setSelected(new Set([itemKey(item)]));
    setAnchorIndex(index);
    setFocusIndex(index);
  };

  const handleSelect = (
    { item, index }: RowIntent,
    mode: { toggle: boolean; range: boolean; checkbox?: boolean },
  ) => {
    const key = itemKey(item);
    setFocusIndex(index);
    if (mode.range && anchorIndex !== null) {
      const [from, to] =
        anchorIndex < index ? [anchorIndex, index] : [index, anchorIndex];
      const span = items.slice(from, to + 1).map(itemKey);
      setSelected(
        (previous) => new Set(mode.toggle ? [...previous, ...span] : span),
      );
      return;
    }
    setAnchorIndex(index);
    if (mode.toggle) {
      setSelected((previous) => {
        const next = new Set(previous);
        if (next.has(key)) next.delete(key);
        else next.add(key);
        return next;
      });
      return;
    }
    setSelected(new Set([key]));
  };

  const reportBatch = (result: DriveBatchResult, verb: string) => {
    if (result.failed.length === 0) {
      showToast({
        kind: "success",
        message: `已${verb} ${result.done.length} 项`,
      });
      return;
    }
    const first = result.failed[0];
    showToast({
      kind: "error",
      message:
        result.done.length > 0
          ? `${result.done.length} 项已${verb}，${result.failed.length} 项失败：“${first.name}”${first.message}`
          : `${verb}失败：“${first.name}”${first.message}`,
      duration: 8000,
    });
  };

  const withBusy = async <T,>(targets: DriveItem[], run: () => Promise<T>) => {
    const keys = targets.map(itemKey);
    setBusyKeys((previous) => new Set([...previous, ...keys]));
    try {
      return await run();
    } finally {
      setBusyKeys((previous) => {
        const next = new Set(previous);
        for (const key of keys) next.delete(key);
        return next;
      });
    }
  };

  const download = (targets: DriveItem[]) => {
    const files = targets.filter((item) => !item.is_directory);
    if (files.length === 0) {
      showToast({
        kind: "info",
        message: "文件夹暂不支持整体下载，请进入文件夹选择文件。",
      });
      return;
    }
    if (files.length < targets.length)
      showToast({
        kind: "info",
        message: `已跳过 ${targets.length - files.length} 个文件夹，仅下载文件。`,
      });
    for (const file of files) {
      void driveTransfers.download(file).then((result) => {
        if (result.status === "failed")
          showToast({
            kind: "error",
            message: `“${file.name}”下载失败：${result.message}`,
          });
      });
    }
  };

  const open = (item: DriveItem) => {
    if (item.is_directory) navigate(item.path);
    else download([item]);
  };

  const enqueue = (files: DriveLocalFile[], emptyHint: string) => {
    if (files.length === 0) {
      showToast({ kind: "info", message: emptyHint });
      return;
    }
    const { queued, rejected } = driveTransfers.enqueueUploads(files, path);
    if (rejected > 0)
      showToast({
        kind: "error",
        message: `${rejected} 个文件超过 2 GiB，无法上传；其余 ${queued} 个已加入传输列表。`,
      });
  };

  const pickUpload = async (folder: boolean) => {
    let picked: DriveLocalPick;
    try {
      picked = folder
        ? await pickDriveUploadFolder()
        : await pickDriveUploadFiles();
    } catch (reason) {
      showToast({
        kind: "error",
        message: errorMessage(reason, "无法打开文件选择器"),
      });
      return;
    }
    if (picked.cancelled) return;
    if (picked.skipped_count)
      showToast({
        kind: "error",
        message: `${picked.skipped_count} 个文件无法读取或未能加入上传队列。`,
      });
    enqueue(
      picked.files,
      folder ? "所选文件夹中没有可上传的文件。" : "没有选择文件。",
    );
  };

  const registerDropped = async (files: NativeDroppedFile[]) => {
    const targets = files.slice(0, 200);
    const accepted: DriveLocalFile[] = [];
    let failed = files.length - targets.length;
    let cursor = 0;
    const worker = async () => {
      for (;;) {
        const index = cursor++;
        const file = targets[index];
        if (!file) return;
        try {
          accepted[index] = await registerDriveDropFile(
            file.path,
            file.drop_token,
          );
        } catch {
          failed += 1;
        }
      }
    };
    await Promise.all(
      Array.from({ length: Math.min(8, targets.length) }, () => worker()),
    );
    if (failed > 0)
      showToast({
        kind: "error",
        message: `${failed} 个拖入项目无法读取（暂不支持直接拖入文件夹，请使用“上传文件夹”）。`,
      });
    const registered = accepted.filter(Boolean);
    if (registered.length > 0) enqueue(registered, "");
  };

  const drop = useNativeFileDrop({
    enabled: active && !error,
    onFiles: (files) => void registerDropped(files),
    onUnavailable: () =>
      showToast({
        kind: "error",
        message: "无法读取拖入文件的本地路径，请改用“上传文件”。",
      }),
  });

  const sort = (field: DriveSortField) => {
    if (field === sortBy)
      setOrder((value) => (value === "asc" ? "desc" : "asc"));
    else {
      setSortBy(field);
      setOrder(DEFAULT_ORDER[field]);
    }
    setPage(1);
  };

  const askDelete = (targets: DriveItem[]) => {
    if (targets.length) setDialog({ kind: "delete", items: targets });
  };
  const askRename = (targets: DriveItem[]) => {
    if (targets.length === 1) setDialog({ kind: "rename", item: targets[0] });
  };

  const itemMenu = (targets: DriveItem[]): DriveMenuItem[] => {
    const single = targets.length === 1 ? targets[0] : null;
    const files = targets.filter((item) => !item.is_directory);
    return [
      single?.is_directory
        ? {
            key: "open",
            label: "打开",
            icon: FolderOpen,
            shortcut: "Enter",
            onSelect: () => open(single),
          }
        : {
            key: "download",
            label: files.length > 1 ? `下载 ${files.length} 个文件` : "下载",
            icon: Download,
            disabled: files.length === 0,
            shortcut: single ? "Enter" : undefined,
            onSelect: () => download(targets),
          },
      ...(query && single
        ? [
            {
              key: "reveal",
              label: "打开所在文件夹",
              icon: FolderOpen,
              onSelect: () => {
                navigate(single.path.slice(0, -1));
                setSelected(new Set([itemKey(single)]));
              },
            },
          ]
        : []),
      {
        key: "rename",
        label: "重命名",
        icon: Pencil,
        shortcut: "F2",
        disabled: !single,
        onSelect: () => askRename(targets),
      },
      {
        key: "move",
        label: "移动到…",
        icon: FolderInput,
        onSelect: () => setDialog({ kind: "move", items: targets }),
      },
      {
        key: "copy",
        label: "复制到…",
        icon: Copy,
        onSelect: () => setDialog({ kind: "copy", items: targets }),
      },
      {
        key: "details",
        label: "详细信息",
        icon: Info,
        onSelect: () => setDetailsOpen(true),
      },
      {
        key: "delete",
        label: "删除",
        icon: Trash2,
        danger: true,
        separatorBefore: true,
        shortcut: "Del",
        onSelect: () => askDelete(targets),
      },
    ];
  };

  const uploadMenu: DriveMenuItem[] = [
    {
      key: "files",
      label: "上传文件",
      icon: FileUp,
      onSelect: () => void pickUpload(false),
    },
    {
      key: "folder",
      label: "上传文件夹",
      icon: FolderUp,
      onSelect: () => void pickUpload(true),
    },
  ];

  const backgroundMenu: DriveMenuItem[] = [
    {
      key: "mkdir",
      label: "新建文件夹",
      icon: FolderPlus,
      onSelect: () => setDialog({ kind: "mkdir" }),
    },
    ...uploadMenu,
    {
      key: "refresh",
      label: "刷新",
      icon: RefreshCw,
      separatorBefore: true,
      onSelect: () => reload(true),
    },
  ];

  const openRowMenu = (target: HTMLElement, targets: DriveItem[]) => {
    const rect = target.getBoundingClientRect();
    setMenu({
      kind: "items",
      anchor: { x: rect.left + 48, y: rect.bottom },
      targets,
      trigger: target,
    });
  };

  const handleRowKey = (
    event: ReactKeyboardEvent<HTMLElement>,
    { item, index }: RowIntent,
  ) => {
    const move = (next: number) => {
      const bounded = Math.max(0, Math.min(items.length - 1, next));
      if (event.shiftKey && anchorIndex !== null) {
        const [from, to] =
          anchorIndex < bounded
            ? [anchorIndex, bounded]
            : [bounded, anchorIndex];
        setSelected(new Set(items.slice(from, to + 1).map(itemKey)));
        setFocusIndex(bounded);
      } else if (event.metaKey || event.ctrlKey) {
        setFocusIndex(bounded);
      } else {
        selectOnly(items[bounded], bounded);
      }
    };
    const targets = selected.has(itemKey(item)) ? selectedItems : [item];
    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        move(index + 1);
        break;
      case "ArrowUp":
        if (event.altKey) return;
        event.preventDefault();
        move(index - 1);
        break;
      case "Home":
        event.preventDefault();
        move(0);
        break;
      case "End":
        event.preventDefault();
        move(items.length - 1);
        break;
      case "Enter":
        event.preventDefault();
        open(item);
        break;
      case " ":
        event.preventDefault();
        handleSelect({ item, index }, { toggle: true, range: false });
        break;
      case "Delete":
        event.preventDefault();
        askDelete(targets);
        break;
      case "F2":
        event.preventDefault();
        askRename(targets);
        break;
      case "ContextMenu":
        event.preventDefault();
        openRowMenu(event.currentTarget, targets);
        break;
      case "F10":
        if (!event.shiftKey) return;
        event.preventDefault();
        openRowMenu(event.currentTarget, targets);
        break;
      default:
    }
  };

  // 页面级快捷键：/ 搜索、Ctrl/⌘+A 全选、Esc 取消选择、Backspace / Alt+↑ 返回上一级。
  useEffect(() => {
    if (!active) return;
    const onKey = (event: KeyboardEvent) => {
      if (
        dialog ||
        menu ||
        conflict ||
        document.querySelector("[data-modal-layer]")
      )
        return;
      if (event.defaultPrevented) return;
      const typing = isTypingTarget(event.target);
      if (event.key === "/" && !typing && !event.metaKey && !event.ctrlKey) {
        event.preventDefault();
        searchRef.current?.focus();
      } else if (
        (event.key === "a" || event.key === "A") &&
        (event.metaKey || event.ctrlKey) &&
        !typing
      ) {
        event.preventDefault();
        setSelected(new Set(items.map(itemKey)));
      } else if (event.key === "Escape" && !typing && selected.size > 0) {
        setSelected(new Set());
      } else if (
        path.length > 0 &&
        !typing &&
        ((event.key === "Backspace" && !event.metaKey && !event.ctrlKey) ||
          (event.key === "ArrowUp" && event.altKey))
      ) {
        event.preventDefault();
        navigate(path.slice(0, -1));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [active, dialog, menu, conflict, items, selected, path, navigate]);

  const confirmTransfer = async (
    mode: "move" | "copy",
    targets: DriveItem[],
    destination: string[],
  ) => {
    const verb = mode === "move" ? "移动" : "复制";
    try {
      const result = await withBusy(targets, () =>
        (mode === "move" ? moveDriveItems : copyDriveItems)(
          refs(targets),
          destination,
        ),
      );
      setDialog(null);
      reportBatch(result, verb);
      if (mode === "move") setSelected(new Set());
      if (mode === "copy" && result.done.length > 0) loadSpace();
      reload(true);
    } catch (reason) {
      showToast({
        kind: "error",
        message: errorMessage(reason, `${verb}失败`),
      });
    }
  };

  const confirmDelete = async (targets: DriveItem[]) => {
    try {
      const result = await withBusy(targets, () =>
        deleteDriveItems(refs(targets)),
      );
      setDialog(null);
      reportBatch(result, "移入回收站");
      setSelected(new Set());
      reload(true);
      loadSpace();
    } catch (reason) {
      showToast({ kind: "error", message: errorMessage(reason, "删除失败") });
    }
  };

  const used = space?.used ?? null;
  const capacity = space?.capacity ?? null;
  const ratio = used !== null && capacity ? Math.min(1, used / capacity) : null;
  const pages = Math.max(1, data?.pages ?? 1);

  const renderBody = () => {
    if (error) {
      if (error.code === "auth_required")
        return (
          <EmptyState
            illustration={<KeyRound />}
            title="尚未登录交大云盘"
            description="请先在设置中完成 jAccount 登录并授权云盘访问，然后回到此页。"
            action={
              <div className="state-actions">
                <Button
                  size="sm"
                  onClick={() => {
                    window.location.hash = "/settings";
                  }}
                >
                  前往设置
                </Button>
                <Button size="sm" variant="ghost" onClick={() => reload(true)}>
                  重新检查
                </Button>
              </div>
            }
          />
        );
      if (error.code === "not_found" && path.length > 0)
        return (
          <ErrorState
            message="该文件夹不存在或已被移动、删除。"
            retry={() => reload(true)}
            secondaryAction={{
              label: `返回${ROOT_LABEL}`,
              onClick: () => navigate([]),
            }}
          />
        );
      if (error.code === "network_error")
        return (
          <EmptyState
            illustration={<CloudOff />}
            title="无法连接交大云盘"
            description={error.message}
            action={
              <Button size="sm" variant="outline" onClick={() => reload(true)}>
                重试
              </Button>
            }
          />
        );
      return <ErrorState message={error.message} retry={() => reload(true)} />;
    }
    if (loading && !data) return <LoadingState label="正在读取文件列表…" />;
    if (items.length === 0) {
      if (query)
        return (
          <EmptyState
            illustration={<Search />}
            title="没有找到匹配的项目"
            description={`个人空间所有层级中没有名称包含“${query}”的文件或文件夹。`}
            action={
              <Button
                size="sm"
                variant="outline"
                onClick={() => setQueryInput("")}
              >
                清除搜索
              </Button>
            }
          />
        );
      return (
        <EmptyState
          illustration={<FolderOpen />}
          title="此文件夹为空"
          description="可以把文件拖到这里，或使用“上传”添加文件。"
          action={
            <div className="state-actions">
              <Button size="sm" onClick={() => void pickUpload(false)}>
                <Upload aria-hidden="true" />
                上传文件
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => setDialog({ kind: "mkdir" })}
              >
                <FolderPlus aria-hidden="true" />
                新建文件夹
              </Button>
            </div>
          }
        />
      );
    }
    return (
      <DriveTable
        items={items}
        selected={selected}
        focusIndex={focusIndex}
        sortBy={sortBy}
        order={order}
        busyKeys={busyKeys}
        showPath={Boolean(query)}
        onSort={sort}
        onFocusIndex={setFocusIndex}
        onSelect={handleSelect}
        onToggleAll={(checked) =>
          setSelected(checked ? new Set(items.map(itemKey)) : new Set())
        }
        onOpen={open}
        onMenu={({ item }, anchor, trigger) =>
          setMenu({
            kind: "items",
            anchor,
            targets:
              selected.has(itemKey(item)) && selected.size > 1
                ? selectedItems
                : [item],
            trigger,
          })
        }
        onRowKey={handleRowKey}
      />
    );
  };

  return (
    <div className="drive-browser" hidden={!active}>
      <div className="drive-space-bar">
        <div className="drive-space-title">
          <HardDrive aria-hidden="true" />
          <strong>{ROOT_LABEL}</strong>
        </div>
        {space && (
          <div className="drive-space-usage">
            <span>
              已使用{formatCapacity(used)}
              <span className="drive-space-divider" aria-hidden="true">
                |
              </span>
              剩余可用{formatCapacity(space.available)}
            </span>
            {ratio !== null && (
              <span
                className="drive-meter"
                role="meter"
                aria-label="云盘空间使用率"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={Math.round(ratio * 100)}
                data-level={ratio > 0.9 ? "high" : ratio > 0.75 ? "mid" : "low"}
              >
                <span style={{ width: `${Math.max(2, ratio * 100)}%` }} />
              </span>
            )}
          </div>
        )}
      </div>

      <div className="drive-toolbar" role="toolbar" aria-label="文件操作">
        <div className="drive-toolbar-primary">
          <Button
            ref={uploadButtonRef}
            type="button"
            aria-haspopup="menu"
            aria-expanded={menu?.kind === "upload"}
            disabled={Boolean(error)}
            onClick={(event) => {
              const rect = event.currentTarget.getBoundingClientRect();
              setMenu({
                kind: "upload",
                anchor: { x: rect.left, y: rect.bottom + 4 },
                trigger: event.currentTarget,
              });
            }}
          >
            <Upload aria-hidden="true" />
            上传
          </Button>
          <Button
            type="button"
            variant="outline"
            disabled={Boolean(error)}
            onClick={() => setDialog({ kind: "mkdir" })}
          >
            <FolderPlus aria-hidden="true" />
            新建文件夹
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label="刷新"
            title="刷新"
            onClick={() => {
              reload(true);
              loadSpace();
            }}
          >
            <RefreshCw
              className={cn(loading && data && "drive-spin")}
              aria-hidden="true"
            />
          </Button>
        </div>
        <div className="drive-search">
          <Search aria-hidden="true" />
          <input
            ref={searchRef}
            type="search"
            value={queryInput}
            placeholder="搜索全部文件和文件夹"
            aria-label="搜索全部路径"
            aria-keyshortcuts="/"
            onChange={(event) => setQueryInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Escape" && queryInput) {
                event.preventDefault();
                setQueryInput("");
              }
            }}
          />
          {queryInput && (
            <button
              type="button"
              className="drive-search-clear"
              aria-label="清除搜索"
              onClick={() => setQueryInput("")}
            >
              <X aria-hidden="true" />
            </button>
          )}
        </div>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          aria-pressed={detailsOpen}
          aria-label={detailsOpen ? "隐藏详细信息" : "显示详细信息"}
          title={detailsOpen ? "隐藏详细信息" : "显示详细信息"}
          onClick={() => setDetailsOpen((value) => !value)}
        >
          {detailsOpen ? (
            <PanelRightClose aria-hidden="true" />
          ) : (
            <PanelRight aria-hidden="true" />
          )}
        </Button>
      </div>

      <div className="drive-location">
        <Button
          type="button"
          variant="ghost"
          size="icon"
          aria-label="返回上一级"
          title="返回上一级（Backspace）"
          disabled={path.length === 0}
          onClick={() => navigate(path.slice(0, -1))}
        >
          <ChevronLeft aria-hidden="true" />
        </Button>
        <nav className="drive-crumbs" aria-label="当前位置">
          <ol>
            <li>
              <button
                type="button"
                aria-current={path.length === 0 ? "page" : undefined}
                onClick={() => navigate([])}
              >
                {ROOT_LABEL}
              </button>
            </li>
            {path.map((segment, index) => (
              <li key={pathKey(path.slice(0, index + 1))}>
                <ChevronRight aria-hidden="true" />
                <button
                  type="button"
                  title={segment}
                  aria-current={index === path.length - 1 ? "page" : undefined}
                  onClick={() => navigate(path.slice(0, index + 1))}
                >
                  {segment}
                </button>
              </li>
            ))}
          </ol>
        </nav>
        {query && data && (
          <span className="drive-search-note" role="status">
            全部路径搜索“{query}”：{data.total} 个结果
          </span>
        )}
      </div>

      {selectedItems.length > 0 && (
        <div
          className="drive-selection-bar"
          role="region"
          aria-label="批量操作"
        >
          <span aria-live="polite">已选择 {selectedItems.length} 项</span>
          <div>
            {selectedItems.length === 1 && selectedItems[0].is_directory ? (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => open(selectedItems[0])}
              >
                <Eye aria-hidden="true" />
                打开
              </Button>
            ) : (
              <Button
                size="sm"
                variant="ghost"
                disabled={selectedItems.every((item) => item.is_directory)}
                onClick={() => download(selectedItems)}
              >
                <Download aria-hidden="true" />
                下载
              </Button>
            )}
            <Button
              size="sm"
              variant="ghost"
              disabled={selectedItems.length !== 1}
              onClick={() => askRename(selectedItems)}
            >
              <Pencil aria-hidden="true" />
              重命名
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setDialog({ kind: "move", items: selectedItems })}
            >
              <FolderInput aria-hidden="true" />
              移动
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setDialog({ kind: "copy", items: selectedItems })}
            >
              <Copy aria-hidden="true" />
              复制
            </Button>
            <Button
              size="sm"
              variant="ghost"
              className="drive-danger-text"
              onClick={() => askDelete(selectedItems)}
            >
              <Trash2 aria-hidden="true" />
              删除
            </Button>
            <Button
              size="sm"
              variant="ghost"
              aria-label="取消选择"
              onClick={() => setSelected(new Set())}
            >
              <X aria-hidden="true" />
            </Button>
          </div>
        </div>
      )}

      <div className={cn("drive-workspace", detailsOpen && "has-details")}>
        <section
          className={cn("drive-list-area", drop.active && "is-drop-target")}
          aria-label={`“${currentName}”中的内容`}
          aria-busy={loading}
          {...drop.handlers}
          onContextMenu={(event) => {
            if (
              (event.target as HTMLElement).closest(
                ".drive-row, .drive-table thead",
              )
            )
              return;
            if (error) return;
            event.preventDefault();
            setMenu({
              kind: "background",
              anchor: { x: event.clientX, y: event.clientY },
              trigger: null,
            });
          }}
          onClick={(event) => {
            if (event.target === event.currentTarget) setSelected(new Set());
          }}
        >
          {renderBody()}
          {data?.truncated && !error && (
            <p className="drive-notice" role="note">
              {query
                ? "已达到全盘搜索范围上限，当前结果可能不完整；请缩小关键词后重试。"
                : "该文件夹仅显示前 10,000 个项目；请在交大云盘网页端整理或拆分目录后再试。"}
            </p>
          )}
          {data && !error && items.length > 0 && (
            <footer className="drive-pagination">
              <span>共 {data.total} 项</span>
              <label className="drive-page-size">
                每页
                <select
                  value={pageSize}
                  onChange={(event) => {
                    setPageSize(Number(event.target.value));
                    setPage(1);
                  }}
                >
                  {PAGE_SIZES.map((size) => (
                    <option key={size} value={size}>
                      {size}
                    </option>
                  ))}
                </select>
                条
              </label>
              <div className="drive-page-nav">
                <Button
                  size="icon"
                  variant="ghost"
                  aria-label="上一页"
                  disabled={page <= 1}
                  onClick={() => setPage(page - 1)}
                >
                  <ChevronLeft aria-hidden="true" />
                </Button>
                <span aria-live="polite">
                  第 {page} / {pages} 页
                </span>
                <Button
                  size="icon"
                  variant="ghost"
                  aria-label="下一页"
                  disabled={page >= pages}
                  onClick={() => setPage(page + 1)}
                >
                  <ChevronRight aria-hidden="true" />
                </Button>
              </div>
            </footer>
          )}
          {drop.active && (
            <div className="drive-drop-overlay" aria-hidden="true">
              <Upload />
              <p>松开鼠标，上传到“{currentName}”</p>
            </div>
          )}
        </section>
        {detailsOpen && !error && (
          <DriveDetails
            path={path}
            selected={selectedItems}
            folderCount={data?.folder_count ?? 0}
            fileCount={data?.file_count ?? 0}
            onClose={() => setDetailsOpen(false)}
          />
        )}
      </div>

      {menu && (
        <DriveMenu
          anchor={menu.anchor}
          label={
            menu.kind === "upload"
              ? "上传"
              : menu.kind === "background"
                ? "文件夹操作"
                : "文件操作"
          }
          items={
            menu.kind === "items"
              ? itemMenu(menu.targets)
              : menu.kind === "upload"
                ? uploadMenu
                : backgroundMenu
          }
          returnFocusTo={menu.trigger}
          onClose={() => setMenu(null)}
        />
      )}

      {dialog?.kind === "mkdir" && (
        <NameDialog
          mode="mkdir"
          onClose={() => setDialog(null)}
          onSubmit={async (name) => {
            await createDriveFolder(path, name);
            setDialog(null);
            showToast({ kind: "success", message: `已创建文件夹“${name}”` });
            reload(true);
          }}
        />
      )}
      {dialog?.kind === "rename" && (
        <NameDialog
          mode="rename"
          initialName={dialog.item.name}
          isDirectory={dialog.item.is_directory}
          onClose={() => setDialog(null)}
          onSubmit={async (name) => {
            if (name === dialog.item.name) {
              setDialog(null);
              return;
            }
            await renameDriveItem(
              dialog.item.path,
              name,
              dialog.item.is_directory,
            );
            setDialog(null);
            setSelected(
              new Set([pathKey([...dialog.item.path.slice(0, -1), name])]),
            );
            showToast({ kind: "success", message: `已重命名为“${name}”` });
            reload(true);
          }}
        />
      )}
      {dialog?.kind === "delete" && (
        <DeleteDialog
          items={dialog.items}
          onClose={() => setDialog(null)}
          onConfirm={() => confirmDelete(dialog.items)}
        />
      )}
      {(dialog?.kind === "move" || dialog?.kind === "copy") && (
        <TransferDialog
          mode={dialog.kind}
          items={dialog.items}
          startPath={path}
          onClose={() => setDialog(null)}
          onConfirm={(destination) =>
            confirmTransfer(dialog.kind, dialog.items, destination)
          }
        />
      )}
    </div>
  );
}
