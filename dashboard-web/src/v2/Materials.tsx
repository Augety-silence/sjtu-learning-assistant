import {
  ArrowUp,
  Download,
  File,
  Folder,
  HardDrive,
  Search,
} from "lucide-react";
import {
  type ReactNode,
  type RefObject,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { FilePreviewContent } from "@/components/FilePreviewDialog";
import { FloatingDocumentPreview } from "@/components/FloatingDocumentPreview";
import { formatDateTime, formatSize } from "@/lib/format";
import type { MaterialNode, MaterialPreview, MaterialTree } from "@/lib/types";
import { V2Button } from "./primitives";
export function V2Materials({
  tree,
  directory,
  folders,
  files,
  path,
  query,
  onQuery,
  onSelect,
  treeContent,
  preview,
  onPreview,
  onClosePreview,
  onAct,
  onMove,
  onRestore,
  busy,
  searchRef,
  onFileDrag,
  onDragEnd,
}: {
  tree: MaterialTree;
  directory?: MaterialNode;
  folders: MaterialNode[];
  files: MaterialNode[];
  path: MaterialNode[];
  query: string;
  onQuery: (v: string) => void;
  onSelect: (v: string) => void;
  treeContent: ReactNode;
  preview: MaterialPreview | null;
  onPreview: (file: MaterialNode) => Promise<void>;
  onClosePreview: () => void;
  onAct: (
    file: MaterialNode,
    action: "download" | "open" | "reveal",
  ) => Promise<void>;
  onMove: (file: MaterialNode, category: string) => void;
  onRestore: (file: MaterialNode) => Promise<void>;
  busy: Record<string, string>;
  searchRef: RefObject<HTMLInputElement | null>;
  onFileDrag: (f: MaterialNode) => void;
  onDragEnd: () => void;
}) {
  const [selected, setSelected] = useState<string | null>(null),
    [checked, setChecked] = useState<Set<string>>(() => new Set()),
    [downloading, setDownloading] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);
  const anchor = useRef<number | null>(null);
  const [cache, setCache] = useState(false);
  useEffect(() => {
    setChecked(new Set());
    setSelected(null);
    anchor.current = null;
    setPreviewOpen(false);
    onClosePreview();
  }, [directory?.id, onClosePreview]);
  const file = files.find((f) => f.id === selected);
  useEffect(() => {
    if (selected && !file) {
      setPreviewOpen(false);
      onClosePreview();
    }
  }, [selected, file, onClosePreview]);
  const closePreview = () => {
    setPreviewOpen(false);
    onClosePreview();
  };
  const openPreview = (f: MaterialNode) => {
    setSelected(f.id);
    onClosePreview();
    setPreviewOpen(true);
    if (f.can_preview) void onPreview(f);
  };
  const downloaded = useMemo(
    () => files.filter((f) => f.download_status === "downloaded"),
    [files],
  );
  const toggle = (i: number, range: boolean) => {
    setChecked((old) => {
      const next = new Set(old);
      if (range && anchor.current !== null) {
        for (
          let k = Math.min(i, anchor.current);
          k <= Math.max(i, anchor.current);
          k++
        )
          next.add(files[k].id);
      } else {
        const id = files[i].id;
        if (next.has(id)) next.delete(id);
        else next.add(id);
      }
      return next;
    });
    anchor.current = i;
  };
  const download = async () => {
    setDownloading(true);
    try {
      for (const f of files.filter((f) => checked.has(f.id)))
        await onAct(f, "download");
    } finally {
      setDownloading(false);
    }
  };
  return (
    <div className="v2-materials-layout v2-file-browser-materials">
      <aside className="v2-material-folders">
        <header>
          <strong>所有学期</strong>
          <small>课程资料目录</small>
        </header>
        <div className="v2-material-tree" role="tree" aria-label="资料目录">
          {treeContent}
        </div>
        <V2Button onClick={() => setCache(!cache)}>
          <HardDrive />
          本地缓存
        </V2Button>
        {cache && (
          <div className="v2-cache-info">
            <p>当前目录已下载 {downloaded.length} 个文件</p>
            <strong>
              {formatSize(downloaded.reduce((s, f) => s + (f.size ?? 0), 0))}
            </strong>
            <small>根据资料索引统计；完整同步与存储配置在设置中管理。</small>
          </div>
        )}
      </aside>
      <section className="v2-material-files">
        <div className="v2-feature-toolbar">
          <label className="v2-search">
            <Search />
            <input
              ref={searchRef}
              value={query}
              onChange={(e) => onQuery(e.target.value)}
              aria-label="搜索文件名"
              placeholder="搜索课程资料"
            />
          </label>
          <V2Button
            disabled={!checked.size || downloading}
            onClick={() => void download()}
          >
            <Download />
            {downloading
              ? "下载中…"
              : `下载${checked.size ? ` (${checked.size})` : ""}`}
          </V2Button>
        </div>
        <nav className="v2-file-path" aria-label="资料路径">
          {path.length > 1 && (
            <V2Button
              aria-label="返回上级资料目录"
              onClick={() => onSelect(path[path.length - 2].id)}
            >
              <ArrowUp />
            </V2Button>
          )}
          {path.map((p, i) => (
            <span key={p.id}>
              <button type="button" onClick={() => onSelect(p.id)}>
                {p.name}
              </button>
              {i < path.length - 1 && <i>/</i>}
            </span>
          ))}
        </nav>
        <div className="v2-material-columns">
          <input
            type="checkbox"
            aria-label="全选资料"
            checked={files.length > 0 && files.every((f) => checked.has(f.id))}
            onChange={(e) =>
              setChecked(
                e.target.checked ? new Set(files.map((f) => f.id)) : new Set(),
              )
            }
          />
          <span>名称</span>
          <span>状态</span>
        </div>
        <div className="v2-material-file-scroll" role="list">
          {folders.map((folder) => (
            <button
              type="button"
              key={folder.id}
              className="v2-material-folder-row"
              onClick={() => onSelect(folder.id)}
            >
              <Folder />
              <strong>{folder.name}</strong>
            </button>
          ))}
          {files.map((f, i) => (
            <div
              key={f.id}
              className={`v2-material-row ${selected === f.id ? "selected" : ""}`}
              role="listitem"
              aria-label={f.name}
              draggable={Boolean(f.source_id && f.course_id && !downloading)}
              onDragStart={(e) => {
                if (!f.source_id || !f.course_id) return;
                e.dataTransfer.effectAllowed = "move";
                e.dataTransfer.setData(
                  "application/x-sjtu-material",
                  JSON.stringify({
                    sourceId: f.source_id,
                    courseId: f.course_id,
                    name: f.name,
                  }),
                );
                e.dataTransfer.setData("text/plain", f.source_id);
                onFileDrag(f);
              }}
              onDragEnd={onDragEnd}
            >
              <input
                type="checkbox"
                aria-label={`选择资料 ${f.name}`}
                checked={checked.has(f.id)}
                onChange={() => toggle(i, false)}
                onClick={(e) => {
                  if (e.shiftKey) {
                    e.preventDefault();
                    toggle(i, true);
                  }
                }}
              />
              <button
                type="button"
                onFocus={() => setSelected(f.id)}
                onClick={() => openPreview(f)}
                onKeyDown={(event) => {
                  if (event.key === " ") {
                    event.preventDefault();
                    if (!event.repeat) openPreview(f);
                    return;
                  }
                  if (
                    !["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)
                  )
                    return;
                  event.preventDefault();
                  const rows = event.currentTarget
                    .closest(".v2-material-file-scroll")
                    ?.querySelectorAll<HTMLButtonElement>(
                      ".v2-material-row > button",
                    );
                  const next =
                    event.key === "Home"
                      ? 0
                      : event.key === "End"
                        ? files.length - 1
                        : Math.max(
                            0,
                            Math.min(
                              files.length - 1,
                              i + (event.key === "ArrowDown" ? 1 : -1),
                            ),
                          );
                  rows?.[next]?.focus();
                }}
                onDoubleClick={() => {
                  if (f.can_open) void onAct(f, "open");
                }}
              >
                <File />
                <span>
                  <strong>{f.name}</strong>
                  <small>
                    {formatSize(f.size ?? null)} ·{" "}
                    {formatDateTime(f.updated_at ?? null)}
                  </small>
                </span>
              </button>
              <span className="v2-badge">
                {f.download_status === "downloaded"
                  ? "本地"
                  : f.cloud_ready
                    ? "云端"
                    : "待下载"}
              </span>
            </div>
          ))}
          {!folders.length && !files.length && (
            <div className="v2-empty">
              {query ? "没有匹配资料" : "当前目录为空"}
            </div>
          )}
        </div>
      </section>
      {previewOpen && file && (
        <FloatingDocumentPreview
          title={file.name}
          detail={
            /\.(pptx?|docx?|xlsx?)$/i.test(file.name)
              ? "Office 内容预览"
              : undefined
          }
          onClose={closePreview}
          footer={
            <>
              <small>
                {file.local_path ?? file.cloud_path ?? "尚未下载到本地"}
              </small>
              <div>
                {file.local_path && (
                  <V2Button
                    disabled={Boolean(busy[file.source_id ?? ""])}
                    onClick={() => void onAct(file, "reveal")}
                  >
                    在文件夹中显示
                  </V2Button>
                )}
                {file.can_open && (
                  <V2Button onClick={() => void onAct(file, "open")}>
                    本地打开
                  </V2Button>
                )}
                {file.download_status !== "downloaded" && (
                  <V2Button onClick={() => void onAct(file, "download")}>
                    下载
                  </V2Button>
                )}
              </div>
              {file.course_id && (
                <label>
                  归档到
                  <select
                    aria-label={`归档“${file.name}”到分类`}
                    value=""
                    onChange={(e) => onMove(file, e.target.value)}
                  >
                    <option value="">选择分类</option>
                    {tree.categories.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.label}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              {file.manual_override && (
                <V2Button onClick={() => void onRestore(file)}>
                  恢复自动分类
                </V2Button>
              )}
            </>
          }
        >
          {preview ? (
            <FilePreviewContent key={file.id} preview={preview} />
          ) : (
            <div className="v2-empty" role="status">
              {busy[file.source_id ?? ""] === "preview"
                ? "正在加载文件预览…"
                : file.can_preview
                  ? "预览未加载，可重试或在本地打开"
                  : "此格式可下载或在本地应用中打开"}
              {file.can_preview && !busy[file.source_id ?? ""] && (
                <V2Button onClick={() => void onPreview(file)}>
                  重试预览
                </V2Button>
              )}
            </div>
          )}
        </FloatingDocumentPreview>
      )}
    </div>
  );
}
