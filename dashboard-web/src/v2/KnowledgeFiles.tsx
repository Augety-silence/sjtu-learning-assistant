import { ArrowUp, FileText, Folder } from "lucide-react";
import { useEffect, useState } from "react";
import { KnowledgeFolders } from "@/components/KnowledgeFolders";
import type {
  CompiledKnowledgeDocument,
  CompiledKnowledgeSearch,
} from "@/lib/types";
import { V2Button } from "./primitives";

function parentPath(path: string) {
  return path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "";
}

export function V2KnowledgeFiles({
  folders,
  search,
  folder,
  sourceId,
  query,
  loading,
  selectedId,
  onSelectFolder,
  onPreview,
  onNext,
  onFirst,
}: {
  folders: NonNullable<CompiledKnowledgeSearch["folders"]>;
  search: CompiledKnowledgeSearch | null;
  folder: string;
  sourceId: string;
  query: string;
  loading: boolean;
  selectedId?: string;
  onSelectFolder: (source: string, path: string) => void;
  onPreview: (doc: CompiledKnowledgeDocument) => void;
  onNext: () => void;
  onFirst?: () => void;
}) {
  const [selected, setSelected] = useState<string | undefined>(selectedId);
  useEffect(() => setSelected(undefined), [folder, sourceId, query]);
  const childFolders = query
    ? []
    : folders.filter(
        (item) =>
          parentPath(item.path) === folder &&
          (!sourceId || item.source_id === sourceId),
      );
  const crumbs = folder.split("/").filter(Boolean);
  return (
    <div className="v2-knowledge-file-layout">
      <aside className="v2-knowledge-directory">
        <header>
          <strong>课程知识库</strong>
        </header>
        <nav aria-label="课程与课时文件夹">
          <V2Button
            aria-pressed={!folder && !sourceId}
            onClick={() => onSelectFolder("", "")}
          >
            全部课程
          </V2Button>
          <KnowledgeFolders
            folders={folders}
            selected={folder}
            selectedSource={sourceId}
            onSelect={onSelectFolder}
          />
        </nav>
      </aside>
      <section
        className="v2-knowledge-directory-content"
        aria-label="当前目录文件"
      >
        <nav className="v2-file-path" aria-label="知识库路径">
          {folder && (
            <V2Button
              aria-label="返回上级知识目录"
              onClick={() => onSelectFolder(sourceId, parentPath(folder))}
            >
              <ArrowUp />
            </V2Button>
          )}
          <button type="button" onClick={() => onSelectFolder("", "")}>
            全部课程
          </button>
          {crumbs.map((name, index) => (
            <span key={crumbs.slice(0, index + 1).join("/")}>
              <i>/</i>
              <button
                type="button"
                onClick={() =>
                  onSelectFolder(sourceId, crumbs.slice(0, index + 1).join("/"))
                }
              >
                {name}
              </button>
            </span>
          ))}
          {query && <span>搜索结果</span>}
        </nav>
        <div className="v2-knowledge-file-columns">
          <span>名称</span>
          <span>格式</span>
        </div>
        <div
          className="v2-knowledge-file-scroll"
          role="list"
          aria-label="知识库文件列表"
          aria-busy={loading}
        >
          {childFolders.map((item) => (
            <div key={`${item.source_id}:${item.path}`} role="listitem">
              <button
                type="button"
                className="v2-knowledge-folder-row"
                onClick={() => onSelectFolder(item.source_id, item.path)}
              >
                <Folder />
                <span>{item.name}</span>
                <small>{item.count} 份资料</small>
              </button>
            </div>
          ))}
          {search?.items.map((doc, index) => (
            <div key={doc.id} role="listitem" aria-label={doc.title}>
              <button
                type="button"
                className="v2-knowledge-file-row"
                aria-pressed={(selectedId ?? selected) === doc.id}
                title={doc.relative_path}
                onFocus={() => setSelected(doc.id)}
                onClick={() => onPreview(doc)}
                onKeyDown={(event) => {
                  if (event.key === " ") {
                    event.preventDefault();
                    if (!event.repeat) onPreview(doc);
                    return;
                  }
                  if (
                    !["ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)
                  )
                    return;
                  event.preventDefault();
                  const rows = event.currentTarget
                    .closest(".v2-knowledge-file-scroll")
                    ?.querySelectorAll<HTMLButtonElement>(
                      ".v2-knowledge-file-row",
                    );
                  const last = (rows?.length ?? 1) - 1;
                  const next =
                    event.key === "Home"
                      ? 0
                      : event.key === "End"
                        ? last
                        : Math.max(
                            0,
                            Math.min(
                              last,
                              index + (event.key === "ArrowDown" ? 1 : -1),
                            ),
                          );
                  rows?.[next]?.focus();
                }}
              >
                <FileText />
                <span>
                  <strong>{doc.title}</strong>
                  <small>
                    {query && doc.snippet ? doc.snippet : doc.relative_path}
                  </small>
                </span>
                <small>Markdown</small>
              </button>
            </div>
          ))}
          {!search?.items.length && !childFolders.length && (
            <div className="v2-empty">
              {loading
                ? "正在加载目录…"
                : query
                  ? "未找到匹配资料"
                  : "当前目录为空"}
            </div>
          )}
        </div>
        {(search?.next_offset != null || onFirst) && (
          <div className="v2-knowledge-file-pagination">
            {onFirst && (
              <V2Button disabled={loading} onClick={onFirst}>
                返回首组结果
              </V2Button>
            )}
            {search?.next_offset != null && (
              <V2Button disabled={loading} onClick={onNext}>
                下一组结果
              </V2Button>
            )}
          </div>
        )}
      </section>
    </div>
  );
}
