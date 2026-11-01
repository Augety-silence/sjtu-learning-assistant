import {
  ChevronDown,
  ChevronUp,
  CircleCheck,
  CircleX,
  CloudDownload,
  CloudUpload,
  FolderOpen,
  RotateCcw,
  X,
} from "lucide-react";
import { useState } from "react";
import { useToast } from "@/components/Toast";
import { Button } from "@/components/ui/Button";
import { revealDriveDownload } from "@/lib/api";
import { formatSize } from "@/lib/format";
import {
  driveTransfers,
  isActiveTransfer,
  type LocalTransfer,
  useDriveTransfers,
} from "./driveTransfers";
import { errorMessage } from "./driveUtils";

const STATUS_LABEL: Record<LocalTransfer["status"], string> = {
  queued: "排队中",
  preparing: "准备中",
  running: "传输中",
  done: "已完成",
  failed: "失败",
  cancelled: "已取消",
  conflict: "同名待处理",
  skipped: "已跳过",
};

function percent(item: LocalTransfer) {
  if (item.status === "done") return 100;
  if (!item.total || item.total <= 0)
    return item.status === "running" ? null : 0;
  return Math.min(100, Math.round((item.done / item.total) * 100));
}

function TransferRow({ item }: { item: LocalTransfer }) {
  const { showToast } = useToast();
  const value = percent(item);
  const Icon = item.kind === "upload" ? CloudUpload : CloudDownload;
  const target = item.relativeDir.length
    ? `${item.relativeDir.join("/")}/`
    : "";
  return (
    <li className="drive-transfer-row" data-status={item.status}>
      <Icon className="drive-transfer-kind" aria-hidden="true" />
      <div className="drive-transfer-main">
        <div className="drive-transfer-title">
          <span className="drive-transfer-name" title={target + item.name}>
            {target}
            {item.name}
          </span>
          <span className="drive-transfer-status">
            {item.status === "done" ? (
              <CircleCheck aria-hidden="true" />
            ) : item.status === "failed" ? (
              <CircleX aria-hidden="true" />
            ) : null}
            {STATUS_LABEL[item.status]}
          </span>
        </div>
        {(item.status === "running" || item.status === "preparing") && (
          <div
            className="drive-progress"
            role="progressbar"
            aria-label={`${item.name} 传输进度`}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={value ?? undefined}
          >
            <span
              className={value === null ? "is-indeterminate" : undefined}
              style={value === null ? undefined : { width: `${value}%` }}
            />
          </div>
        )}
        <p className="drive-transfer-meta">
          {item.message
            ? item.message
            : item.status === "running" && item.total
              ? `${formatSize(item.done)} / ${formatSize(item.total)}`
              : item.total !== null
                ? formatSize(item.total)
                : ""}
        </p>
      </div>
      <div className="drive-transfer-actions">
        {(item.status === "queued" ||
          item.status === "preparing" ||
          item.status === "running") && (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label={`取消${item.kind === "upload" ? "上传" : "下载"}“${item.name}”`}
            onClick={() => void driveTransfers.cancel(item.id)}
          >
            <X aria-hidden="true" />
          </Button>
        )}
        {item.kind === "upload" &&
          item.localPath &&
          ["failed", "cancelled", "skipped"].includes(item.status) &&
          item.message !== "单个文件不能超过 2 GiB" && (
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label={`重试上传“${item.name}”`}
              onClick={() => driveTransfers.retry(item.id)}
            >
              <RotateCcw aria-hidden="true" />
            </Button>
          )}
        {item.kind === "download" &&
          item.status === "done" &&
          item.revealable && (
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label={`在文件夹中显示“${item.name}”`}
              title="在文件夹中显示"
              onClick={async () => {
                try {
                  await revealDriveDownload(item.id);
                } catch (reason) {
                  showToast({
                    kind: "error",
                    message: errorMessage(reason, "无法打开下载位置"),
                  });
                }
              }}
            >
              <FolderOpen aria-hidden="true" />
            </Button>
          )}
      </div>
    </li>
  );
}

/** 右下角传输面板：参照主流网盘的“传输列表”，可折叠、可取消/重试。 */
export function TransferTray() {
  const { tasks } = useDriveTransfers();
  const [collapsed, setCollapsed] = useState(false);
  if (tasks.length === 0) return null;
  const active = tasks.filter(isActiveTransfer);
  const failed = tasks.filter((item) => item.status === "failed").length;
  const uploads = active.filter((item) => item.kind === "upload").length;
  const downloads = active.filter((item) => item.kind === "download").length;
  const summary =
    active.length > 0
      ? [
          uploads && `正在上传 ${uploads} 项`,
          downloads && `正在下载 ${downloads} 项`,
        ]
          .filter(Boolean)
          .join("，")
      : failed > 0
        ? `传输结束，${failed} 项失败`
        : "全部传输已完成";
  return (
    <section
      className="drive-transfer-tray"
      aria-label="传输列表"
      data-collapsed={collapsed}
    >
      <header>
        <p aria-live="polite">{summary}</p>
        <div>
          {active.length === 0 && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => driveTransfers.clearFinished()}
            >
              清除记录
            </Button>
          )}
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-expanded={!collapsed}
            aria-label={collapsed ? "展开传输列表" : "收起传输列表"}
            onClick={() => setCollapsed((value) => !value)}
          >
            {collapsed ? (
              <ChevronUp aria-hidden="true" />
            ) : (
              <ChevronDown aria-hidden="true" />
            )}
          </Button>
        </div>
      </header>
      {!collapsed && (
        <ul className="drive-transfer-list">
          {[...tasks].reverse().map((item) => (
            <TransferRow key={item.id} item={item} />
          ))}
        </ul>
      )}
    </section>
  );
}
