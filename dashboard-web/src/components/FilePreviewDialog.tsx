import { useRef } from "react";
import { Button } from "@/components/ui/Button";
import type { MaterialPreview } from "@/lib/types";
import { useModalFocus } from "@/lib/useModalFocus";

export function FilePreviewDialog({
  preview,
  onClose,
}: {
  preview: MaterialPreview;
  onClose: () => void;
}) {
  const dialogRef = useRef<HTMLElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  useModalFocus(dialogRef, onClose, { initialFocusRef: closeButtonRef });

  return (
    <div
      className="message-dialog-layer material-preview-layer"
      data-modal-layer
    >
      <button
        type="button"
        className="message-dialog-backdrop"
        aria-label="点击遮罩关闭文件预览"
        onClick={onClose}
      />
      <section
        ref={dialogRef}
        className="material-preview-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="material-preview-title"
        tabIndex={-1}
      >
        <header className="material-preview-header">
          <h3 id="material-preview-title">{preview.name}</h3>
          <Button
            ref={closeButtonRef}
            type="button"
            variant="ghost"
            size="sm"
            aria-label="关闭文件预览"
            onClick={onClose}
          >
            关闭
          </Button>
        </header>
        <div className="material-preview-content">
          {preview.kind === "image" ? (
            <img src={preview.data_url} alt={preview.name} />
          ) : preview.kind === "pdf" ? (
            <iframe src={preview.data_url} title={`${preview.name} PDF 预览`} />
          ) : (
            <pre>{preview.text}</pre>
          )}
        </div>
      </section>
    </div>
  );
}
