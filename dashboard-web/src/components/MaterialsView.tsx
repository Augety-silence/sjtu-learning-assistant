import { ChevronDown, File, Folder, Search } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { EmptyState, ErrorState, LoadingState } from "@/components/States";
import { Button } from "@/components/ui/Button";
import { invoke } from "@/lib/api";
import { formatDateTime, formatSize } from "@/lib/format";
import {
  containerChildren,
  directFiles,
  filesBelow,
  findNodePath,
} from "@/lib/materialTree";
import type { MaterialNode, MaterialTree } from "@/lib/types";

function TreeBranch({
  node,
  selected,
  select,
}: {
  node: MaterialNode;
  selected: string;
  select: (node: MaterialNode) => void;
}) {
  const [expanded, setExpanded] = useState(
    node.kind === "root" || node.kind === "term",
  );
  const children = containerChildren(node);
  if (node.kind === "root") {
    return (
      <>
        {children.map((child) => (
          <TreeBranch
            key={child.id}
            node={child}
            selected={selected}
            select={select}
          />
        ))}
      </>
    );
  }
  return (
    <div className="tree-branch">
      <button
        type="button"
        className={`tree-row ${selected === node.id ? "tree-selected" : ""}`}
        onClick={() => select(node)}
      >
        {children.length > 0 && (
          <ChevronDown
            className={`tree-chevron ${expanded ? "tree-chevron-open" : ""}`}
            aria-hidden="true"
            onClick={(event) => {
              event.stopPropagation();
              setExpanded((value) => !value);
            }}
          />
        )}
        <Folder className="tree-kind-icon" aria-hidden="true" />
        <span>{node.name}</span>
      </button>
      {expanded && children.length > 0 && (
        <div className="tree-children">
          {children.map((child) => (
            <TreeBranch
              key={child.id}
              node={child}
              selected={selected}
              select={select}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export function MaterialsView() {
  const [tree, setTree] = useState<MaterialTree | null>(null);
  const [selectedId, setSelectedId] = useState("root");
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("all");
  const [status, setStatus] = useState("all");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState<Record<string, string>>({});

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

  return (
    <div className="section-stack materials-page">
      <div className="view-intro">
        <div>
          <h2>资料库</h2>
          <p>按学期、课程、动态分类与 Canvas 目录浏览，不修改原始数据。</p>
        </div>
      </div>
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
        <div className="notice" role="status">
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
            <TreeBranch
              node={tree.root}
              selected={selectedId}
              select={(node) => setSelectedId(node.id)}
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
              {childFolders.map((folder) => (
                <button
                  type="button"
                  role="listitem"
                  className="finder-row folder-row"
                  key={folder.id}
                  onDoubleClick={() => setSelectedId(folder.id)}
                  onClick={() => setSelectedId(folder.id)}
                >
                  <Folder aria-hidden="true" />
                  <span className="finder-name">{folder.name}</span>
                  <span className="finder-meta">文件夹</span>
                </button>
              ))}
              {visibleFiles.map(({ file, path: filePath }) => {
                const active = file.source_id
                  ? busy[file.source_id]
                  : undefined;
                return (
                  <div className="finder-row" role="listitem" key={file.id}>
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
