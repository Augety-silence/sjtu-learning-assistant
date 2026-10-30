import {
  ChevronRight,
  Folder,
  FolderInput,
  FolderOpen,
  HardDrive,
  TriangleAlert,
  X,
} from "lucide-react";
import {
  type FormEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import { Button } from "@/components/ui/Button";
import { listDrive } from "@/lib/api";
import type { DriveItem } from "@/lib/types";
import { useModalFocus } from "@/lib/useModalFocus";
import {
  errorMessage,
  isInside,
  isSamePath,
  pathKey,
  ROOT_LABEL,
  validateName,
} from "./driveUtils";

function DialogFrame({
  title,
  description,
  busy = false,
  onClose,
  children,
  footer,
  initialFocusRef,
  size = "default",
}: {
  title: string;
  description?: ReactNode;
  busy?: boolean;
  onClose: () => void;
  children?: ReactNode;
  footer: ReactNode;
  initialFocusRef?: React.RefObject<HTMLElement | null>;
  size?: "default" | "wide";
}) {
  const dialogRef = useRef<HTMLElement>(null);
  const titleId = useId();
  const descriptionId = useId();
  const dismiss = useCallback(() => {
    if (!busy) onClose();
  }, [busy, onClose]);
  useModalFocus(dialogRef, dismiss, {
    initialFocusRef,
    dismissible: !busy,
  });
  return createPortal(
    <div className="drive-dialog-layer" data-modal-layer>
      <button
        type="button"
        className="drive-dialog-backdrop"
        aria-label="关闭对话框"
        tabIndex={-1}
        disabled={busy}
        onClick={dismiss}
      />
      <section
        ref={dialogRef}
        className={`drive-dialog${size === "wide" ? " drive-dialog-wide" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descriptionId : undefined}
        aria-busy={busy}
        tabIndex={-1}
      >
        <header className="drive-dialog-header">
          <div>
            <h3 id={titleId}>{title}</h3>
            {description && <p id={descriptionId}>{description}</p>}
          </div>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label="关闭"
            disabled={busy}
            onClick={dismiss}
          >
            <X aria-hidden="true" />
          </Button>
        </header>
        {children}
        <footer className="drive-dialog-footer">{footer}</footer>
      </section>
    </div>,
    document.body,
  );
}

export function NameDialog({
  mode,
  initialName = "",
  isDirectory = true,
  onSubmit,
  onClose,
}: {
  mode: "mkdir" | "rename";
  initialName?: string;
  isDirectory?: boolean;
  onSubmit: (name: string) => Promise<void>;
  onClose: () => void;
}) {
  const [value, setValue] = useState(initialName);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const label = mode === "mkdir" || isDirectory ? "文件夹名称" : "文件名";

  useEffect(() => {
    const input = inputRef.current;
    if (!input) return;
    // 重命名文件时只选中主文件名，保留扩展名（与主流网盘一致）。
    const dot = !isDirectory ? initialName.lastIndexOf(".") : -1;
    input.setSelectionRange(0, dot > 0 ? dot : initialName.length);
  }, [initialName, isDirectory]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const problem = validateName(value, label);
    if (problem) {
      setError(problem);
      inputRef.current?.focus();
      return;
    }
    setBusy(true);
    setError("");
    try {
      await onSubmit(value.normalize("NFC").trim());
    } catch (reason) {
      setError(errorMessage(reason, "操作失败，请重试。"));
      setBusy(false);
      inputRef.current?.focus();
    }
  };

  const formId = useId();
  const errorId = useId();
  return (
    <DialogFrame
      title={mode === "mkdir" ? "新建文件夹" : "重命名"}
      busy={busy}
      onClose={onClose}
      initialFocusRef={inputRef}
      footer={
        <>
          <Button
            type="button"
            variant="outline"
            disabled={busy}
            onClick={onClose}
          >
            取消
          </Button>
          <Button
            type="submit"
            form={formId}
            loading={busy}
            loadingLabel={mode === "mkdir" ? "正在创建…" : "正在保存…"}
          >
            {mode === "mkdir" ? "创建" : "确定"}
          </Button>
        </>
      }
    >
      <form
        id={formId}
        className="drive-dialog-body"
        onSubmit={submit}
        noValidate
      >
        <label className="drive-field">
          <span>{label}</span>
          <input
            ref={inputRef}
            value={value}
            maxLength={255}
            autoComplete="off"
            spellCheck={false}
            aria-invalid={Boolean(error)}
            aria-describedby={error ? errorId : undefined}
            disabled={busy}
            onChange={(event) => {
              setValue(event.target.value);
              if (error) setError("");
            }}
          />
        </label>
        {error && (
          <p id={errorId} className="drive-field-error" role="alert">
            {error}
          </p>
        )}
      </form>
    </DialogFrame>
  );
}

export function DeleteDialog({
  items,
  onConfirm,
  onClose,
}: {
  items: DriveItem[];
  onConfirm: () => Promise<void>;
  onClose: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const folders = items.filter((item) => item.is_directory).length;
  const subject =
    items.length === 1 ? `“${items[0].name}”` : `选中的 ${items.length} 个项目`;
  return (
    <DialogFrame
      title={`删除${subject}？`}
      description={
        <>
          删除后将移入交大云盘<strong>回收站</strong>，可在网页端回收站中恢复。
          {folders > 0 && " 文件夹内的全部内容会一并移入回收站。"}
        </>
      }
      busy={busy}
      onClose={onClose}
      initialFocusRef={cancelRef}
      footer={
        <>
          <Button
            ref={cancelRef}
            type="button"
            variant="outline"
            disabled={busy}
            onClick={onClose}
          >
            取消
          </Button>
          <Button
            type="button"
            className="drive-danger-button"
            loading={busy}
            loadingLabel="正在删除…"
            onClick={async () => {
              setBusy(true);
              try {
                await onConfirm();
              } finally {
                setBusy(false);
              }
            }}
          >
            移入回收站
          </Button>
        </>
      }
    >
      {items.length > 1 && (
        <ul className="drive-dialog-list" aria-label="待删除项目">
          {items.slice(0, 6).map((item) => (
            <li key={pathKey(item.path)}>
              {item.is_directory ? (
                <Folder aria-hidden="true" />
              ) : (
                <span className="drive-dot" aria-hidden="true" />
              )}
              <span>{item.name}</span>
            </li>
          ))}
          {items.length > 6 && <li>…以及另外 {items.length - 6} 项</li>}
        </ul>
      )}
    </DialogFrame>
  );
}

/** 选择移动/复制的目标文件夹：仅列出文件夹，可逐级进入。 */
export function TransferDialog({
  mode,
  items,
  startPath,
  onConfirm,
  onClose,
}: {
  mode: "move" | "copy";
  items: DriveItem[];
  startPath: string[];
  onConfirm: (destination: string[]) => Promise<void>;
  onClose: () => void;
}) {
  const [path, setPath] = useState<string[]>(startPath);
  const [folders, setFolders] = useState<DriveItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [truncated, setTruncated] = useState(false);
  const requestRef = useRef(0);

  useEffect(() => {
    const request = ++requestRef.current;
    setLoading(true);
    setError("");
    const load = async () => {
      const found: DriveItem[] = [];
      let current = 1;
      let pages = 1;
      let wasTruncated = false;
      do {
        const page = await listDrive({
          path,
          page: current,
          pageSize: 200,
          sortBy: "name",
          order: "asc",
        });
        if (request !== requestRef.current) return;
        found.push(...page.items.filter((item) => item.is_directory));
        pages = page.pages;
        wasTruncated ||= page.truncated;
        current += 1;
      } while (current <= pages);
      setFolders(found);
      setTruncated(wasTruncated);
    };
    void load()
      .catch((reason) => {
        if (request !== requestRef.current) return;
        setFolders([]);
        setError(errorMessage(reason, "无法读取该文件夹。"));
      })
      .finally(() => {
        if (request === requestRef.current) setLoading(false);
      });
  }, [path]);

  const verb = mode === "move" ? "移动" : "复制";
  const movingInto = items.some(
    (item) => item.is_directory && isInside(path, item.path),
  );
  const sameParent =
    mode === "move" &&
    items.every((item) => isSamePath(item.path.slice(0, -1), path));
  const disabledReason = movingInto
    ? "不能移动或复制到自身或其子文件夹中。"
    : sameParent
      ? "项目已在当前文件夹中。"
      : "";
  const blockedFolder = (folder: DriveItem) =>
    items.some(
      (item) => item.is_directory && isSamePath(item.path, folder.path),
    );

  return (
    <DialogFrame
      title={
        items.length === 1
          ? `${verb}“${items[0].name}”到…`
          : `${verb} ${items.length} 个项目到…`
      }
      description="选择目标文件夹。同名项目不会被覆盖。"
      busy={busy}
      onClose={onClose}
      size="wide"
      footer={
        <>
          {disabledReason && (
            <span className="drive-dialog-hint" role="note">
              {disabledReason}
            </span>
          )}
          <Button
            type="button"
            variant="outline"
            disabled={busy}
            onClick={onClose}
          >
            取消
          </Button>
          <Button
            type="button"
            disabled={Boolean(disabledReason) || loading || Boolean(error)}
            loading={busy}
            loadingLabel={`正在${verb}…`}
            onClick={async () => {
              setBusy(true);
              try {
                await onConfirm(path);
              } finally {
                setBusy(false);
              }
            }}
          >
            <FolderInput aria-hidden="true" />
            {verb}到此处
          </Button>
        </>
      }
    >
      <nav className="drive-picker-crumbs" aria-label="目标位置">
        <button
          type="button"
          onClick={() => setPath([])}
          aria-current={path.length === 0 ? "location" : undefined}
        >
          <HardDrive aria-hidden="true" />
          {ROOT_LABEL}
        </button>
        {path.map((segment, index) => (
          <span
            key={pathKey(path.slice(0, index + 1))}
            className="drive-crumb-item"
          >
            <ChevronRight aria-hidden="true" />
            <button
              type="button"
              onClick={() => setPath(path.slice(0, index + 1))}
              aria-current={index === path.length - 1 ? "location" : undefined}
            >
              {segment}
            </button>
          </span>
        ))}
      </nav>
      <div className="drive-picker-list" aria-busy={loading}>
        {loading ? (
          <p className="drive-picker-state">正在读取文件夹…</p>
        ) : error ? (
          <p className="drive-picker-state drive-picker-error" role="alert">
            <TriangleAlert aria-hidden="true" />
            {error}
          </p>
        ) : folders.length === 0 ? (
          <p className="drive-picker-state">此文件夹中没有子文件夹</p>
        ) : (
          <ul aria-label="子文件夹">
            {folders.map((folder) => {
              const blocked = blockedFolder(folder);
              return (
                <li key={pathKey(folder.path)}>
                  <button
                    type="button"
                    disabled={blocked || busy}
                    title={blocked ? "不能进入正在移动的文件夹" : undefined}
                    onClick={() => setPath(folder.path)}
                  >
                    <FolderOpen aria-hidden="true" />
                    <span>{folder.name}</span>
                    <ChevronRight aria-hidden="true" />
                  </button>
                </li>
              );
            })}
          </ul>
        )}
        {truncated && !loading && (
          <p className="drive-picker-state">
            该目录仅显示云盘服务返回的前 10,000 个项目。
          </p>
        )}
      </div>
    </DialogFrame>
  );
}

export type ConflictChoice = "overwrite" | "rename" | "skip";

export function ConflictDialog({
  name,
  remaining,
  onChoose,
}: {
  name: string;
  remaining: number;
  onChoose: (choice: ConflictChoice | "cancel", applyToAll: boolean) => void;
}) {
  const [applyAll, setApplyAll] = useState(false);
  const keepRef = useRef<HTMLButtonElement>(null);
  return (
    <DialogFrame
      title="存在同名文件"
      description={<>当前文件夹已存在“{name}”。请选择处理方式。</>}
      onClose={() => onChoose("cancel", false)}
      initialFocusRef={keepRef}
      footer={
        <>
          <Button
            type="button"
            variant="ghost"
            onClick={() => onChoose("skip", applyAll)}
          >
            跳过
          </Button>
          <Button
            type="button"
            variant="outline"
            onClick={() => onChoose("overwrite", applyAll)}
          >
            覆盖
          </Button>
          <Button
            ref={keepRef}
            type="button"
            onClick={() => onChoose("rename", applyAll)}
          >
            保留两者
          </Button>
        </>
      }
    >
      <div className="drive-dialog-body">
        <p className="drive-dialog-note">
          “保留两者”会自动生成一个不冲突的新名称。覆盖会替换云端已有版本。
        </p>
        {remaining > 0 && (
          <label className="drive-checkbox-row">
            <input
              type="checkbox"
              checked={applyAll}
              onChange={(event) => setApplyAll(event.target.checked)}
            />
            <span>对本次上传中其余 {remaining} 个同名冲突应用相同操作</span>
          </label>
        )}
      </div>
    </DialogFrame>
  );
}
