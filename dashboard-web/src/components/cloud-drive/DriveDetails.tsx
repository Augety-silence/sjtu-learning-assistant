import { Folder, Info, X } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { formatSize } from "@/lib/format";
import type { DriveItem } from "@/lib/types";
import {
  describeItem,
  formatModified,
  pathKey,
  ROOT_LABEL,
} from "./driveUtils";

/** 右侧详情栏：未选中时展示当前文件夹统计，选中单项时展示元数据（对应网页端右栏）。 */
export function DriveDetails({
  path,
  selected,
  folderCount,
  fileCount,
  onClose,
}: {
  path: string[];
  selected: DriveItem[];
  folderCount: number;
  fileCount: number;
  onClose: () => void;
}) {
  const single = selected.length === 1 ? selected[0] : null;
  const folderName = path.length ? path[path.length - 1] : ROOT_LABEL;
  const meta = single ? describeItem(single) : null;
  const Icon = meta?.icon ?? Folder;
  const totalSize = selected.reduce(
    (sum, item) => sum + (item.is_directory ? 0 : (item.size ?? 0)),
    0,
  );
  const unknownSizeCount = selected.filter(
    (item) => !item.is_directory && item.size === null,
  ).length;

  return (
    <aside className="drive-details" aria-label="详细信息">
      <header className="drive-details-header">
        <h3>
          <Info aria-hidden="true" />
          详细信息
        </h3>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          aria-label="关闭详细信息"
          onClick={onClose}
        >
          <X aria-hidden="true" />
        </Button>
      </header>
      <div className="drive-details-hero">
        <span
          className="drive-file-icon drive-file-icon-lg"
          data-tone={meta?.tone ?? "folder"}
          aria-hidden="true"
        >
          <Icon />
        </span>
        <p className="drive-details-name" title={single?.name ?? folderName}>
          {single
            ? single.name
            : selected.length > 1
              ? `已选择 ${selected.length} 项`
              : folderName}
        </p>
      </div>
      <dl className="drive-details-list">
        {single ? (
          <>
            <div>
              <dt>类型</dt>
              <dd>{meta?.label}</dd>
            </div>
            {!single.is_directory && (
              <div>
                <dt>大小</dt>
                <dd>{formatSize(single.size)}</dd>
              </div>
            )}
            <div>
              <dt>修改时间</dt>
              <dd>{formatModified(single.modified_at)}</dd>
            </div>
            <div>
              <dt>创建时间</dt>
              <dd>{formatModified(single.created_at)}</dd>
            </div>
            <div>
              <dt>位置</dt>
              <dd className="drive-details-path">
                {[ROOT_LABEL, ...single.path.slice(0, -1)].join(" / ")}
              </dd>
            </div>
          </>
        ) : selected.length > 1 ? (
          <>
            <div>
              <dt>文件夹</dt>
              <dd>{selected.filter((item) => item.is_directory).length} 个</dd>
            </div>
            <div>
              <dt>文件</dt>
              <dd>{selected.filter((item) => !item.is_directory).length} 个</dd>
            </div>
            <div>
              <dt>文件合计</dt>
              <dd>
                {formatSize(totalSize)}
                {unknownSizeCount > 0
                  ? `（另有 ${unknownSizeCount} 个文件大小未知）`
                  : ""}
              </dd>
            </div>
          </>
        ) : (
          <>
            <div>
              <dt>内容</dt>
              <dd>共有 {folderCount + fileCount} 个项目</dd>
            </div>
            <div>
              <dt>文件夹</dt>
              <dd>{folderCount} 个</dd>
            </div>
            <div>
              <dt>文件</dt>
              <dd>{fileCount} 个</dd>
            </div>
            <div>
              <dt>位置</dt>
              <dd className="drive-details-path" data-path={pathKey(path)}>
                {[ROOT_LABEL, ...path].join(" / ")}
              </dd>
            </div>
          </>
        )}
      </dl>
    </aside>
  );
}
