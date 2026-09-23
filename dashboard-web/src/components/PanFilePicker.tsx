import { ChevronLeft, ChevronRight, File, Folder } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { ErrorState, LoadingState } from "@/components/States";
import { Button } from "@/components/ui/Button";
import { getPanFiles } from "@/lib/api";
import type { PanItem, PanPage } from "@/lib/types";

export function PanFilePicker({
  onSelect,
  disabled = false,
}: {
  onSelect: (item: PanItem) => void;
  disabled?: boolean;
}) {
  const [path, setPath] = useState("");
  const [pageNumber, setPageNumber] = useState(1);
  const [page, setPage] = useState<PanPage | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setPage(null);
    setError("");
    try {
      setPage(await getPanFiles(path, pageNumber));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "云盘目录读取失败");
    }
  }, [pageNumber, path]);

  useEffect(() => void load(), [load]);

  const openDirectory = (item: PanItem) => {
    setPath(item.remote_path);
    setPageNumber(1);
  };
  const goUp = () => {
    setPath(path.split("/").slice(0, -1).join("/"));
    setPageNumber(1);
  };

  return (
    <section className="pan-picker" aria-label="交大云盘文件选择器">
      <div className="pan-toolbar">
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={!path || disabled}
          onClick={goUp}
        >
          <ChevronLeft aria-hidden="true" />
          上一级
        </Button>
        <span title={path || "根目录"}>{path || "根目录"}</span>
      </div>
      {error ? (
        <ErrorState message={error} retry={() => void load()} />
      ) : page === null ? (
        <LoadingState />
      ) : (
        <>
          <div className="pan-list" role="list" aria-label="云盘文件">
            {page.items.length === 0 && (
              <p className="finder-empty">此目录为空</p>
            )}
            {page.items.map((item) => (
              <button
                type="button"
                role="listitem"
                className="pan-row"
                key={item.remote_path}
                disabled={disabled}
                onClick={() =>
                  item.is_directory ? openDirectory(item) : onSelect(item)
                }
              >
                {item.is_directory ? (
                  <Folder aria-hidden="true" />
                ) : (
                  <File aria-hidden="true" />
                )}
                <span>{item.name}</span>
                <small>
                  {item.is_directory
                    ? "文件夹"
                    : item.size === null
                      ? "—"
                      : `${item.size} B`}
                </small>
                <small>{item.modified_at || ""}</small>
              </button>
            ))}
          </div>
          <div className="pan-pagination" aria-label="分页">
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={pageNumber === 1 || disabled}
              onClick={() => setPageNumber((value) => value - 1)}
            >
              上一页
            </Button>
            <span>
              第 {pageNumber} 页 · 共 {page.total} 项
            </span>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={!page.has_more || disabled}
              onClick={() => setPageNumber((value) => value + 1)}
            >
              下一页
              <ChevronRight aria-hidden="true" />
            </Button>
          </div>
        </>
      )}
    </section>
  );
}
