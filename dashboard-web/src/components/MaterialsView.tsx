import { ChevronLeft, File, Folder, Search } from "lucide-react";
import {
  type KeyboardEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { FilePreviewDialog } from "@/components/FilePreviewDialog";
import {
  isMaterialDropTarget,
  type MaterialDragSource,
  type MaterialDropHandler,
  MaterialTreeBranch,
  materialDropClass,
  materialTargetAriaLabel,
} from "@/components/MaterialTreeBranch";
import { EmptyState, ErrorState, LoadingState } from "@/components/States";
import { useToast } from "@/components/Toast";
import { Button } from "@/components/ui/Button";
import {
  invoke,
  moveMaterial,
  previewMaterial,
  restoreMaterialAuto,
} from "@/lib/api";
import { formatDateTime, formatSize } from "@/lib/format";
import {
  containerChildren,
  directFiles,
  filesBelow,
  findNodePath,
  visibleTreeItems,
} from "@/lib/materialTree";
import type { MaterialNode, MaterialPreview, MaterialTree } from "@/lib/types";
import { useCompactViewport } from "@/lib/useCompactViewport";

function defaultExpandedIds(root: MaterialNode) {
  const expanded = new Set<string>();
  const visit = (node: MaterialNode) => {
    if (["root", "term"].includes(node.kind)) {
      expanded.add(node.id);
      for (const child of containerChildren(node)) visit(child);
    }
  };
  visit(root);
  return expanded;
}

export function MaterialsView() {
  const [tree, setTree] = useState<MaterialTree | null>(null);
  const [selectedId, setSelectedId] = useState("root");
  const [activeId, setActiveId] = useState("root");
  const [expandedIds, setExpandedIds] = useState<Set<string>>(
    () => new Set(["root"]),
  );
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("all");
  const [status, setStatus] = useState("all");
  const [selectedContentId, setSelectedContentId] = useState<string | null>(
    null,
  );
  const [mobilePane, setMobilePane] = useState<"directory" | "content">(
    "directory",
  );
  const [restoreTreeFocus, setRestoreTreeFocus] = useState(false);
  const [error, setError] = useState("");
  const [preview, setPreview] = useState<MaterialPreview | null>(null);
  const [busy, setBusy] = useState<Record<string, string>>({});
  const [dragSource, setDragSource] = useState<MaterialDragSource | null>(null);
  const [dropTargetId, setDropTargetId] = useState<string | null>(null);
  const itemRefs = useRef(new Map<string, HTMLElement>());
  const contentHeadingRef = useRef<HTMLHeadingElement>(null);
  const compact = useCompactViewport();
  const { showToast } = useToast();

  const load = useCallback(async () => {
    setError("");
    try {
      const nextTree = await invoke<MaterialTree>("material_tree");
      setTree(nextTree);
      setExpandedIds((current) =>
        current.size > 1 ? current : defaultExpandedIds(nextTree.root),
      );
      setActiveId((current) =>
        findNodePath(nextTree.root, current) ? current : nextTree.root.id,
      );
      setSelectedId((current) =>
        findNodePath(nextTree.root, current) ? current : nextTree.root.id,
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "资料加载失败");
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  const path = tree ? (findNodePath(tree.root, selectedId) ?? [tree.root]) : [];
  const current = path[path.length - 1];
  const allMatches = tree ? filesBelow(tree.root) : [];
  const visibleFiles = (
    query.trim()
      ? allMatches
      : current
        ? directFiles(current, path.slice(1, -1))
        : []
  ).filter(({ file }) => {
    const searchMatched =
      !query.trim() ||
      file.name.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase());
    const categoryMatched = category === "all" || file.category === category;
    const statusMatched =
      status === "all" ||
      file.download_status === status ||
      (status === "pending" &&
        !["downloaded", "cloud_only", "failed"].includes(
          file.download_status ?? "pending",
        ));
    return searchMatched && categoryMatched && statusMatched;
  });
  const childFolders =
    query.trim() || !current ? [] : containerChildren(current);
  const visibleItems = useMemo(
    () => (tree ? visibleTreeItems(tree.root, expandedIds) : []),
    [expandedIds, tree],
  );
  const hasFilters = Boolean(
    query.trim() || category !== "all" || status !== "all",
  );

  useEffect(() => {
    setSelectedContentId(null);
  }, [category, query, selectedId, status]);

  useEffect(() => {
    if (!compact || mobilePane !== "directory" || !restoreTreeFocus) return;
    itemRefs.current.get(activeId)?.focus();
    setRestoreTreeFocus(false);
  }, [activeId, compact, mobilePane, restoreTreeFocus]);

  const registerItem = useCallback(
    (id: string) => (node: HTMLElement | null) => {
      if (node) itemRefs.current.set(id, node);
      else itemRefs.current.delete(id);
    },
    [],
  );

  const focusItem = useCallback((id: string) => {
    setActiveId(id);
    itemRefs.current.get(id)?.focus();
  }, []);

  const toggleTreeItem = useCallback(
    (id: string, expanded: boolean) => {
      setExpandedIds((currentIds) => {
        const next = new Set(currentIds);
        if (expanded) next.add(id);
        else next.delete(id);
        return next;
      });
      if (!expanded && tree) {
        const activePath = findNodePath(tree.root, activeId);
        if (activePath?.some((node) => node.id === id) && activeId !== id) {
          focusItem(id);
        }
      }
    },
    [activeId, focusItem, tree],
  );

  const selectTreeItem = useCallback(
    (node: MaterialNode) => {
      setSelectedId(node.id);
      setActiveId(node.id);
      if (compact) setMobilePane("content");
    },
    [compact],
  );

  useEffect(() => {
    if (compact && mobilePane === "content") contentHeadingRef.current?.focus();
  }, [compact, mobilePane, selectedId]);

  const onTreeKeyDown = useCallback(
    (event: KeyboardEvent<HTMLElement>, node: MaterialNode) => {
      const index = visibleItems.findIndex((item) => item.id === node.id);
      if (index < 0) return;
      const item = visibleItems[index];
      const children = containerChildren(node);
      const expanded = expandedIds.has(node.id);
      let handled = true;

      switch (event.key) {
        case "ArrowDown":
          focusItem(
            visibleItems[Math.min(index + 1, visibleItems.length - 1)].id,
          );
          break;
        case "ArrowUp":
          focusItem(visibleItems[Math.max(index - 1, 0)].id);
          break;
        case "Home":
          focusItem(visibleItems[0].id);
          break;
        case "End":
          focusItem(visibleItems[visibleItems.length - 1].id);
          break;
        case "ArrowRight":
          if (children.length === 0) handled = false;
          else if (!expanded) toggleTreeItem(node.id, true);
          else focusItem(children[0].id);
          break;
        case "ArrowLeft":
          if (expanded && children.length > 0) toggleTreeItem(node.id, false);
          else if (item.parentId) focusItem(item.parentId);
          else handled = false;
          break;
        case "Enter":
        case " ":
          selectTreeItem(node);
          if (children.length > 0) toggleTreeItem(node.id, !expanded);
          break;
        default:
          handled = false;
      }

      if (handled) event.preventDefault();
    },
    [expandedIds, focusItem, selectTreeItem, toggleTreeItem, visibleItems],
  );

  const act = async (
    file: MaterialNode,
    action: "download" | "open" | "reveal",
  ) => {
    if (!file.source_id) return;
    const toastId = `material:${file.source_id}:${action}`;
    setBusy((value) => ({ ...value, [file.source_id as string]: action }));
    showToast({
      id: toastId,
      kind: "info",
      message:
        action === "download"
          ? `正在下载“${file.name}”…`
          : action === "reveal"
            ? `正在定位“${file.name}”…`
            : `正在打开“${file.name}”…`,
      duration: 0,
    });
    try {
      await invoke(`material_${action}`, { source_id: file.source_id });
      showToast({
        id: toastId,
        kind: "success",
        message:
          action === "download"
            ? `“${file.name}”下载完成`
            : action === "reveal"
              ? `已在 Finder 中显示“${file.name}”`
              : `已打开“${file.name}”`,
      });
      if (action === "download") await load();
    } catch (reason) {
      showToast({
        id: toastId,
        kind: "error",
        message: reason instanceof Error ? reason.message : "操作失败",
      });
    } finally {
      setBusy((value) => {
        const next = { ...value };
        delete next[file.source_id as string];
        return next;
      });
    }
  };

  const previewFile = async (file: MaterialNode) => {
    if (!file.source_id) return;
    const toastId = `material:${file.source_id}:preview`;
    setBusy((value) => ({ ...value, [file.source_id as string]: "preview" }));
    showToast({
      id: toastId,
      kind: "info",
      message: `正在加载“${file.name}”预览…`,
      duration: 0,
    });
    try {
      const nextPreview = await previewMaterial(file.source_id);
      setPreview(nextPreview);
      showToast({
        id: toastId,
        kind: "success",
        message: `已加载“${file.name}”`,
      });
    } catch (reason) {
      showToast({
        id: toastId,
        kind: "error",
        message: reason instanceof Error ? reason.message : "预览失败",
      });
    } finally {
      setBusy((value) => {
        const next = { ...value };
        delete next[file.source_id as string];
        return next;
      });
    }
  };

  const runDefaultAction = (node: MaterialNode) => {
    if (node.kind === "file") {
      if (busy[node.source_id ?? ""]) return;
      if (node.can_preview) void previewFile(node);
      else void act(node, node.can_open ? "open" : "download");
      return;
    }
    setSelectedId(node.id);
    setActiveId(node.id);
    setSelectedContentId(null);
  };

  const onContentKeyDown = (
    event: KeyboardEvent<HTMLElement>,
    node: MaterialNode,
  ) => {
    if (event.target !== event.currentTarget || event.key !== "Enter") return;
    event.preventDefault();
    runDefaultAction(node);
  };

  const clearFilters = () => {
    setQuery("");
    setCategory("all");
    setStatus("all");
  };

  const move = useCallback(
    async (file: MaterialNode, target: MaterialNode) => {
      if (!file.source_id || !file.course_id) return;
      if (!isMaterialDropTarget(target)) {
        showToast({
          kind: "error",
          message: "文件只能归档到分类目录或 Canvas 子文件夹。",
        });
        return;
      }
      if (target.course_id !== file.course_id) {
        showToast({
          kind: "error",
          message: `不能将“${file.name}”移动到其他课程。`,
        });
        return;
      }
      const toastId = `material:${file.source_id}:move`;
      setBusy((value) => ({ ...value, [file.source_id as string]: "move" }));
      showToast({
        id: toastId,
        kind: "info",
        message: `正在将“${file.name}”归档到“${target.name}”…`,
        duration: 0,
      });
      try {
        await moveMaterial(file.source_id, target.id);
        setSelectedId(target.id);
        await load();
        showToast({
          id: toastId,
          kind: "success",
          message: `已将“${file.name}”归档到“${target.name}”。`,
        });
      } catch (reason) {
        showToast({
          id: toastId,
          kind: "error",
          message: reason instanceof Error ? reason.message : "移动资料失败",
        });
      } finally {
        setBusy((value) => {
          const next = { ...value };
          delete next[file.source_id as string];
          return next;
        });
      }
    },
    [load, showToast],
  );

  const drop: MaterialDropHandler = useCallback(
    (event, target) => {
      event.preventDefault();
      event.stopPropagation();
      setDropTargetId(null);
      if (!dragSource || !tree) {
        showToast({ kind: "error", message: "未识别要移动的资料，请重试。" });
        return;
      }
      const sourcePath = findNodePath(tree.root, `file:${dragSource.sourceId}`);
      const file = sourcePath?.[sourcePath.length - 1];
      if (!file || file.kind !== "file") {
        showToast({ kind: "error", message: "资料已变化，请刷新后重试。" });
        return;
      }
      void move(file, target);
    },
    [dragSource, move, showToast, tree],
  );

  const restoreAuto = async (file: MaterialNode) => {
    if (!file.source_id) return;
    const toastId = `material:${file.source_id}:restore`;
    setBusy((value) => ({ ...value, [file.source_id as string]: "restore" }));
    showToast({
      id: toastId,
      kind: "info",
      message: `正在恢复“${file.name}”的自动分类…`,
      duration: 0,
    });
    try {
      await restoreMaterialAuto(file.source_id);
      await load();
      showToast({
        id: toastId,
        kind: "success",
        message: `“${file.name}”已恢复自动分类。`,
      });
    } catch (reason) {
      showToast({
        id: toastId,
        kind: "error",
        message: reason instanceof Error ? reason.message : "恢复自动分类失败",
      });
    } finally {
      setBusy((value) => {
        const next = { ...value };
        delete next[file.source_id as string];
        return next;
      });
    }
  };

  return (
    <div className="section-stack materials-page">
      <div className="view-intro">
        <div>
          <h2>资料库</h2>
          <p>按学期、课程、动态分类与 Canvas 目录浏览，可人工拖拽归档。</p>
        </div>
      </div>
      <p id="material-drop-help" className="sr-only">
        仅可将文件移动到同一课程的四个分类或其现有 Canvas 子文件夹。
      </p>
      <div className="finder-toolbar" role="search">
        <label className="search-control">
          <Search aria-hidden="true" />
          <input
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              if (compact) setMobilePane("content");
            }}
            placeholder="搜索文件名"
            aria-label="搜索文件名"
          />
        </label>
        <label>
          <span>分类</span>
          <select
            value={category}
            onChange={(event) => {
              setCategory(event.target.value);
              if (compact) setMobilePane("content");
            }}
          >
            <option value="all">全部分类</option>
            {tree?.categories.map((item) => (
              <option key={item.id} value={item.id}>
                {item.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>状态</span>
          <select
            value={status}
            onChange={(event) => {
              setStatus(event.target.value);
              if (compact) setMobilePane("content");
            }}
          >
            <option value="all">全部状态</option>
            <option value="downloaded">已下载</option>
            <option value="cloud_only">仅云端</option>
            <option value="pending">未下载</option>
            <option value="failed">下载失败</option>
          </select>
        </label>
      </div>
      {error ? (
        <ErrorState message={error} retry={load} />
      ) : !tree ? (
        <LoadingState label="正在整理课程资料…" />
      ) : tree.root.children?.length === 0 ? (
        <EmptyState
          title="暂无课程资料"
          description="完成 Canvas 同步后，资料会按课程目录显示。"
          action={
            <Button variant="outline" size="sm" onClick={() => void load()}>
              重新检查
            </Button>
          }
        />
      ) : (
        <div className={`finder finder-${mobilePane}`}>
          {(!compact || mobilePane === "directory") && (
            <aside className="finder-tree" role="tree" aria-label="资料目录">
              <MaterialTreeBranch
                node={tree.root}
                level={1}
                selectedId={selectedId}
                activeId={activeId}
                expandedIds={expandedIds}
                select={selectTreeItem}
                focusItem={setActiveId}
                toggle={toggleTreeItem}
                onKeyDown={onTreeKeyDown}
                registerItem={registerItem}
                dragSource={dragSource}
                dropTargetId={dropTargetId}
                hoverTarget={(node) => setDropTargetId(node?.id ?? null)}
                drop={drop}
              />
            </aside>
          )}
          {(!compact || mobilePane === "content") && (
            <section className="finder-content" aria-label="目录内容">
              {compact && (
                <div className="finder-mobile-header">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      setRestoreTreeFocus(true);
                      setMobilePane("directory");
                    }}
                  >
                    <ChevronLeft aria-hidden="true" />
                    返回目录
                  </Button>
                  <h3 ref={contentHeadingRef} tabIndex={-1}>
                    {query.trim() ? "搜索结果" : current?.name}
                  </h3>
                </div>
              )}
              <nav className="breadcrumbs" aria-label="路径">
                {path.map((node, index) => (
                  <span key={node.id}>
                    <button
                      type="button"
                      onClick={() => {
                        setSelectedId(node.id);
                        setActiveId(node.id);
                      }}
                    >
                      {node.name}
                    </button>
                    {index < path.length - 1 && <span>/</span>}
                  </span>
                ))}
              </nav>
              <p
                className="result-count finder-result-count"
                aria-live="polite"
              >
                {childFolders.length + visibleFiles.length} 个结果
              </p>
              <div className="finder-list" role="list">
                {childFolders.map((folder, folderIndex) => {
                  const target = isMaterialDropTarget(folder);
                  return (
                    <div
                      role="listitem"
                      tabIndex={
                        selectedContentId === folder.id ||
                        (selectedContentId === null && folderIndex === 0)
                          ? 0
                          : -1
                      }
                      aria-current={
                        selectedContentId === folder.id ? "true" : undefined
                      }
                      aria-label={materialTargetAriaLabel(folder)}
                      aria-describedby={
                        target ? "material-drop-help" : undefined
                      }
                      data-material-target-id={target ? folder.id : undefined}
                      className={`finder-row folder-row${selectedContentId === folder.id ? " finder-row-selected" : ""}${materialDropClass(folder, dragSource, dropTargetId)}`}
                      key={folder.id}
                      onFocus={() => setSelectedContentId(folder.id)}
                      onClick={() => setSelectedContentId(folder.id)}
                      onDoubleClick={() => runDefaultAction(folder)}
                      onKeyDown={(event) => onContentKeyDown(event, folder)}
                      onDragEnter={
                        target
                          ? (event) => {
                              event.preventDefault();
                              setDropTargetId(folder.id);
                            }
                          : undefined
                      }
                      onDragOver={
                        target
                          ? (event) => {
                              event.preventDefault();
                              event.dataTransfer.dropEffect =
                                dragSource?.courseId === folder.course_id
                                  ? "move"
                                  : "none";
                              setDropTargetId(folder.id);
                            }
                          : undefined
                      }
                      onDragLeave={
                        target
                          ? (event) => {
                              if (
                                !event.currentTarget.contains(
                                  event.relatedTarget as Node,
                                )
                              ) {
                                setDropTargetId(null);
                              }
                            }
                          : undefined
                      }
                      onDrop={
                        target ? (event) => drop(event, folder) : undefined
                      }
                    >
                      <Folder aria-hidden="true" />
                      <span className="finder-name">{folder.name}</span>
                      <span className="finder-meta">文件夹</span>
                    </div>
                  );
                })}
                {visibleFiles.map(({ file, path: filePath }, fileIndex) => {
                  const active = file.source_id
                    ? busy[file.source_id]
                    : undefined;
                  return (
                    <div
                      className={`finder-row material-file-row${selectedContentId === file.id ? " finder-row-selected" : ""}${dragSource?.sourceId === file.source_id ? " material-dragging" : ""}`}
                      role="listitem"
                      tabIndex={
                        selectedContentId === file.id ||
                        (selectedContentId === null &&
                          childFolders.length === 0 &&
                          fileIndex === 0)
                          ? 0
                          : -1
                      }
                      aria-current={
                        selectedContentId === file.id ? "true" : undefined
                      }
                      aria-label={`拖动资料：${file.name}`}
                      aria-describedby="material-drop-help"
                      onFocus={() => setSelectedContentId(file.id)}
                      onClick={() => setSelectedContentId(file.id)}
                      onDoubleClick={(event) => {
                        if (
                          !(event.target as Element).closest(".finder-actions")
                        ) {
                          runDefaultAction(file);
                        }
                      }}
                      onKeyDown={(event) => onContentKeyDown(event, file)}
                      draggable={Boolean(
                        file.source_id && file.course_id && !active,
                      )}
                      onDragStart={(event) => {
                        if (!file.source_id || !file.course_id) {
                          event.preventDefault();
                          return;
                        }
                        const source = {
                          sourceId: file.source_id,
                          courseId: file.course_id,
                          name: file.name,
                        };
                        event.dataTransfer.effectAllowed = "move";
                        event.dataTransfer.setData(
                          "application/x-sjtu-material",
                          JSON.stringify(source),
                        );
                        event.dataTransfer.setData(
                          "text/plain",
                          file.source_id,
                        );
                        setDragSource(source);
                        showToast({
                          kind: "info",
                          message: `正在移动“${file.name}”，请选择同课程目标目录。`,
                        });
                      }}
                      onDragEnd={() => {
                        setDragSource(null);
                        setDropTargetId(null);
                      }}
                      key={file.id}
                    >
                      <File aria-hidden="true" />
                      <div className="finder-file-details">
                        <span
                          className="finder-name file-name"
                          title={filePath.map((node) => node.name).join(" / ")}
                        >
                          {file.name}
                        </span>
                        {file.cloud_ready && file.cloud_path ? (
                          <span
                            className="finder-local-path finder-cloud-path"
                            title={`交大云盘 · ${file.cloud_path}`}
                          >
                            交大云盘 · {file.cloud_path}
                          </span>
                        ) : (
                          file.local_path && (
                            <span
                              className="finder-local-path"
                              title={file.local_path}
                            >
                              {file.local_path}
                            </span>
                          )
                        )}
                      </div>
                      <span
                        className={`status-tag status-${file.download_status}`}
                      >
                        {file.cloud_ready
                          ? "云端"
                          : file.download_status === "downloaded"
                            ? "已下载"
                            : file.download_status === "failed"
                              ? "失败"
                              : "未下载"}
                      </span>
                      <span className="finder-meta">
                        {formatSize(file.size ?? null)} ·{" "}
                        {formatDateTime(file.updated_at ?? null)}
                      </span>
                      <div className="finder-actions">
                        {file.course_id && (
                          <label className="material-move-menu">
                            <span className="sr-only">
                              选择“{file.name}”的归档分类
                            </span>
                            <select
                              aria-label={`归档“${file.name}”到分类`}
                              tabIndex={selectedContentId === file.id ? 0 : -1}
                              value=""
                              disabled={Boolean(active)}
                              onChange={(event) => {
                                const selectedCategory = event.target.value;
                                if (!selectedCategory || !tree) return;
                                const targetPath = findNodePath(
                                  tree.root,
                                  `category:${file.course_id}:${selectedCategory}`,
                                );
                                const target =
                                  targetPath?.[targetPath.length - 1];
                                if (target) void move(file, target);
                              }}
                            >
                              <option value="">归档到…</option>
                              {tree.categories.map((item) => (
                                <option key={item.id} value={item.id}>
                                  {item.label}
                                </option>
                              ))}
                            </select>
                          </label>
                        )}
                        {file.manual_override && (
                          <Button
                            variant="link"
                            tabIndex={selectedContentId === file.id ? 0 : -1}
                            loading={active === "restore"}
                            loadingLabel="恢复中…"
                            disabled={Boolean(active)}
                            onClick={() => void restoreAuto(file)}
                          >
                            恢复自动分类
                          </Button>
                        )}
                        {file.can_preview && (
                          <Button
                            variant="link"
                            tabIndex={selectedContentId === file.id ? 0 : -1}
                            loading={active === "preview"}
                            loadingLabel="加载中…"
                            disabled={Boolean(active)}
                            onClick={() => void previewFile(file)}
                          >
                            预览
                          </Button>
                        )}
                        {file.can_open && !file.can_preview && (
                          <Button
                            variant="link"
                            tabIndex={selectedContentId === file.id ? 0 : -1}
                            loading={active === "open"}
                            loadingLabel="打开中…"
                            disabled={Boolean(active)}
                            onClick={() => void act(file, "open")}
                          >
                            打开
                          </Button>
                        )}
                        {file.local_path && (
                          <Button
                            variant="link"
                            tabIndex={selectedContentId === file.id ? 0 : -1}
                            loading={active === "reveal"}
                            loadingLabel="定位中…"
                            disabled={Boolean(active)}
                            onClick={() => void act(file, "reveal")}
                          >
                            Reveal
                          </Button>
                        )}
                        {!file.can_open && (
                          <Button
                            variant="link"
                            tabIndex={selectedContentId === file.id ? 0 : -1}
                            loading={active === "download"}
                            loadingLabel="下载中…"
                            disabled={Boolean(active)}
                            onClick={() => void act(file, "download")}
                          >
                            下载
                          </Button>
                        )}
                      </div>
                    </div>
                  );
                })}
                {childFolders.length === 0 && visibleFiles.length === 0 && (
                  <div className="finder-empty">
                    {hasFilters ? (
                      <EmptyState
                        title="没有匹配资料"
                        description="请调整筛选条件，或清除筛选查看当前目录。"
                        action={
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={clearFilters}
                          >
                            清除筛选
                          </Button>
                        }
                      />
                    ) : (
                      <EmptyState
                        title="当前目录为空"
                        description="这个目录暂时没有可显示的文件或子目录。"
                      />
                    )}
                  </div>
                )}
              </div>
            </section>
          )}
        </div>
      )}
      {preview && (
        <FilePreviewDialog preview={preview} onClose={() => setPreview(null)} />
      )}
    </div>
  );
}
