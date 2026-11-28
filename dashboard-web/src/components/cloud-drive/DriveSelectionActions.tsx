import {
  Copy,
  Download,
  Eye,
  FolderInput,
  Pencil,
  Trash2,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/Button";
import type { DriveItem } from "@/lib/types";
import "@/v2/drive-selection.css";

interface Props {
  items: DriveItem[];
  inline: boolean;
  onOpen: (item: DriveItem) => void;
  onDownload: (items: DriveItem[]) => void;
  onRename: (items: DriveItem[]) => void;
  onMove: (items: DriveItem[]) => void;
  onCopy: (items: DriveItem[]) => void;
  onDelete: (items: DriveItem[]) => void;
  onClear: () => void;
}

/** 选中操作复用已有业务回调；新版在固定工具栏内切换。 */
export function DriveSelectionActions({
  items,
  inline,
  onOpen,
  onDownload,
  onRename,
  onMove,
  onCopy,
  onDelete,
  onClear,
}: Props) {
  return (
    <div
      className={inline ? "drive-selection-tools" : "drive-selection-bar"}
      role="region"
      aria-label="批量操作"
    >
      <span aria-live="polite">已选择 {items.length} 项</span>
      <div>
        {items.length === 1 && items[0].is_directory ? (
          <Button
            size="sm"
            variant="ghost"
            aria-label="打开"
            title="打开"
            onClick={() => onOpen(items[0])}
          >
            <Eye aria-hidden="true" />
            <span className="drive-selection-label">打开</span>
          </Button>
        ) : (
          <Button
            size="sm"
            variant="ghost"
            disabled={items.every((item) => item.is_directory)}
            aria-label="下载"
            title="下载"
            onClick={() => onDownload(items)}
          >
            <Download aria-hidden="true" />
            <span className="drive-selection-label">下载</span>
          </Button>
        )}
        <Button
          size="sm"
          variant="ghost"
          disabled={items.length !== 1}
          aria-label="重命名"
          title="重命名"
          onClick={() => onRename(items)}
        >
          <Pencil aria-hidden="true" />
          <span className="drive-selection-label">重命名</span>
        </Button>
        <Button
          size="sm"
          variant="ghost"
          aria-label="移动"
          title="移动"
          onClick={() => onMove(items)}
        >
          <FolderInput aria-hidden="true" />
          <span className="drive-selection-label">移动</span>
        </Button>
        <Button
          size="sm"
          variant="ghost"
          aria-label="复制"
          title="复制"
          onClick={() => onCopy(items)}
        >
          <Copy aria-hidden="true" />
          <span className="drive-selection-label">复制</span>
        </Button>
        <Button
          size="sm"
          variant="ghost"
          className="drive-danger-text"
          aria-label="删除"
          title="删除"
          onClick={() => onDelete(items)}
        >
          <Trash2 aria-hidden="true" />
          <span className="drive-selection-label">删除</span>
        </Button>
        <Button
          size="sm"
          variant="ghost"
          aria-label="取消选择"
          onClick={() => onClear()}
        >
          <X aria-hidden="true" />
        </Button>
      </div>
    </div>
  );
}
