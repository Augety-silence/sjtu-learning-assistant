import { motion, useIsPresent } from "motion/react";
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
  const isPresent = useIsPresent();
  useModalFocus(dialogRef, onClose, {
    initialFocusRef: closeButtonRef,
    active: isPresent,
  });

  return (
    <motion.div
      className="message-dialog-layer material-preview-layer"
      data-modal-layer
      data-motion-layer="modal"
      aria-hidden={isPresent ? undefined : true}
      initial={{ opacity: 0.01 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: isPresent ? 0.14 : 0.12 }}
    >
      <button
        type="button"
        className="message-dialog-backdrop"
        aria-label="点击遮罩关闭文件预览"
        disabled={!isPresent}
        onClick={onClose}
      />
      <motion.section
        ref={dialogRef}
        className="material-preview-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="material-preview-title"
        aria-hidden={isPresent ? undefined : true}
        data-motion-surface="modal"
        tabIndex={-1}
        initial={{ opacity: 0.9, y: 6, scale: 0.99 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        exit={{ opacity: 0.88, y: 4, scale: 0.99 }}
        transition={{
          duration: isPresent ? 0.18 : 0.14,
          ease: [0.16, 1, 0.3, 1],
        }}
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
      </motion.section>
    </motion.div>
  );
}
