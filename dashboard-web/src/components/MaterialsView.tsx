import { File, Folder, Search } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import {
  isMaterialDropTarget,
  type MaterialDragSource,
  type MaterialDropHandler,
  MaterialTreeBranch,
  materialDropClass,
  materialTargetAriaLabel,
} from "@/components/MaterialTreeBranch";
import { EmptyState, ErrorState, LoadingState } from "@/components/States";
import { Button } from "@/components/ui/Button";
import { invoke, moveMaterial, restoreMaterialAuto } from "@/lib/api";
import { formatDateTime, formatSize } from "@/lib/format";
import {
  containerChildren,
  directFiles,
  filesBelow,
  findNodePath,
} from "@/lib/materialTree";
import type { MaterialNode, MaterialTree } from "@/lib/types";

export function MaterialsView() {
  const [tree, setTree] = useState<MaterialTree | null>(null);
  const [selectedId, setSelectedId] = useState("root");
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("all");
  const [status, setStatus] = useState("all");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState<Record<string, string>>({});
  const [dragSource, setDragSource] = useState<MaterialDragSource | null>(null);
  const [dropTargetId, setDropTargetId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError("");
    try {
      setTree(await invoke<MaterialTree>("material_tree"));
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
        !["downloaded", "failed"].includes(file.download_status ?? "pending"));
    return searchMatched && categoryMatched && statusMatched;
  });
  const childFolders =
    query.trim() || !current ? [] : containerChildren(current);

  const act = async (
    file: MaterialNode,
    action: "download" | "open" | "reveal",
  ) => {
    if (!file.source_id) return;
    setBusy((value) => ({ ...value, [file.source_id as string]: action }));
    setNotice("");
    try {
      await invoke(`material_${action}`, { source_id: file.source_id });
      setNotice(
        action === "download"
          ? `“${file.name}”下载完成`
          : action === "reveal"
            ? `已在 Finder 中显示“${file.name}”`
            : `已打开“${file.name}”`,
      );
      if (action === "download") await load();
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : "操作失败");
    } finally {
      setBusy((value) => {
        const next = { ...value };
        delete next[file.source_id as string];
        return next;
      });
    }
  };

  const move = useCallback(
    async (file: MaterialNode, target: MaterialNode) => {
      if (!file.source_id || !file.course_id) return;
      if (!isMaterialDropTarget(target)) {
        setNotice("文件只能归档到分类目录或 Canvas 子文件夹。");
        return;
      }
      if (target.course_id !== file.course_id) {
        setNotice(`不能将“${file.name}”移动到其他课程。`);
        return;
      }
      setBusy((value) => ({ ...value, [file.source_id as string]: "move" }));
      setNotice("");
      try {
        await moveMaterial(file.source_id, target.id);
        setSelectedId(target.id);
        await load();
        setNotice(`已将“${file.name}”归档到“${target.name}”。`);
      } catch (reason) {
        setNotice(reason instanceof Error ? reason.message : "移动资料失败");
      } finally {
        setBusy((value) => {
          const next = { ...value };
          delete next[file.source_id as string];
          return next;
        });
      }
    },
    [load],
  );

  const drop: MaterialDropHandler = useCallback(
    (event, target) => {
      event.preventDefault();
      event.stopPropagation();
      setDropTargetId(null);
      if (!dragSource || !tree) {
        setNotice("未识别要移动的资料，请重试。");
        return;
      }
      const sourcePath = findNodePath(tree.root, `file:${dragSource.sourceId}`);
      const file = sourcePath?.[sourcePath.length - 1];
      if (!file || file.kind !== "file") {
        setNotice("资料已变化，请刷新后重试。");
        return;
      }
      void move(file, target);
    },
    [dragSource, move, tree],
  );

  const restoreAuto = async (file: MaterialNode) => {
    if (!file.source_id) return;
    setBusy((value) => ({ ...value, [file.source_id as string]: "restore" }));
    setNotice("");
    try {
      await restoreMaterialAuto(file.source_id);
      await load();
      setNotice(`“${file.name}”已恢复自动分类。`);
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : "恢复自动分类失败");
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
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索文件名"
            aria-label="搜索文件名"
          />
        </label>
        <label>
          <span>分类</span>
          <select
            value={category}
            onChange={(event) => setCategory(event.target.value)}
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
            onChange={(event) => setStatus(event.target.value)}
          >
            <option value="all">全部状态</option>
            <option value="downloaded">已下载</option>
            <option value="pending">未下载</option>
            <option value="failed">下载失败</option>
          </select>
        </label>
      </div>
      {notice && (
        <div className="notice" role="status" aria-live="polite">
          {notice}
        </div>
      )}
      {error ? (
        <ErrorState message={error} retry={() => void load()} />
      ) : !tree ? (
        <LoadingState label="正在整理课程资料…" />
      ) : tree.root.children?.length === 0 ? (
        <EmptyState
          title="暂无课程资料"
          description="完成 Canvas 同步后，资料会按课程目录显示。"
        />
      ) : (
        <div className="finder">
          <aside className="finder-tree" aria-label="资料目录">
            <button
              type="button"
              className={`tree-row ${selectedId === "root" ? "tree-selected" : ""}`}
              onClick={() => setSelectedId("root")}
            >
              <Folder className="tree-kind-icon" aria-hidden="true" />
              <span>全部资料</span>
            </button>
            <MaterialTreeBranch
              node={tree.root}
              selected={selectedId}
              select={(node) => setSelectedId(node.id)}
              dragSource={dragSource}
              dropTargetId={dropTargetId}
              hoverTarget={(node) => setDropTargetId(node?.id ?? null)}
              drop={drop}
            />
          </aside>
          <section className="finder-content">
            <nav className="breadcrumbs" aria-label="路径">
              {path.map((node, index) => (
                <span key={node.id}>
                  <button type="button" onClick={() => setSelectedId(node.id)}>
                    {node.name}
                  </button>
                  {index < path.length - 1 && <span>/</span>}
                </span>
              ))}
            </nav>
            <div className="finder-list" role="list">
              {childFolders.map((folder) => {
                const target = isMaterialDropTarget(folder);
                return (
                  <button
                    type="button"
                    role="listitem"
                    aria-label={materialTargetAriaLabel(folder)}
                    aria-describedby={target ? "material-drop-help" : undefined}
                    data-material-target-id={target ? folder.id : undefined}
                    className={`finder-row folder-row${materialDropClass(folder, dragSource, dropTargetId)}`}
                    key={folder.id}
                    onDoubleClick={() => setSelectedId(folder.id)}
                    onClick={() => setSelectedId(folder.id)}
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
                    onDrop={target ? (event) => drop(event, folder) : undefined}
                  >
                    <Folder aria-hidden="true" />
                    <span className="finder-name">{folder.name}</span>
                    <span className="finder-meta">文件夹</span>
                  </button>
                );
              })}
              {visibleFiles.map(({ file, path: filePath }) => {
                const active = file.source_id
                  ? busy[file.source_id]
                  : undefined;
                return (
                  <div
                    className={`finder-row material-file-row${dragSource?.sourceId === file.source_id ? " material-dragging" : ""}`}
                    role="listitem"
                    aria-label={`拖动资料：${file.name}`}
                    aria-describedby="material-drop-help"
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
                      event.dataTransfer.setData("text/plain", file.source_id);
                      setDragSource(source);
                      setNotice(
                        `正在移动“${file.name}”，请选择同课程目标目录。`,
                      );
                    }}
                    onDragEnd={() => {
                      setDragSource(null);
                      setDropTargetId(null);
                    }}
                    key={file.id}
                  >
                    <File aria-hidden="true" />
                    <div className="finder-file-details">
                      <button
                        className="finder-name file-name"
                        type="button"
                        title={filePath.map((node) => node.name).join(" / ")}
                        onDoubleClick={() =>
                          file.can_open && void act(file, "open")
                        }
                      >
                        {file.name}
                      </button>
                      {file.local_path && (
                        <span
                          className="finder-local-path"
                          title={file.local_path}
                        >
                          {file.local_path}
                        </span>
                      )}
                    </div>
                    <span
                      className={`status-tag status-${file.download_status}`}
                    >
                      {file.download_status === "downloaded"
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
                          disabled={Boolean(active)}
                          onClick={() => void restoreAuto(file)}
                        >
                          恢复自动分类
                        </Button>
                      )}
                      {file.can_open ? (
                        <>
                          <Button
                            variant="link"
                            disabled={Boolean(active)}
                            onClick={() => void act(file, "open")}
                          >
                            打开
                          </Button>
                          <Button
                            variant="link"
                            disabled={Boolean(active)}
                            onClick={() => void act(file, "reveal")}
                          >
                            Reveal
                          </Button>
                        </>
                      ) : (
                        <Button
                          variant="link"
                          disabled={Boolean(active)}
                          onClick={() => void act(file, "download")}
                        >
                          {active === "download" ? "下载中…" : "下载"}
                        </Button>
                      )}
                    </div>
                  </div>
                );
              })}
              {childFolders.length === 0 && visibleFiles.length === 0 && (
                <div className="finder-empty">
                  当前目录或筛选条件下没有文件。
                </div>
              )}
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
