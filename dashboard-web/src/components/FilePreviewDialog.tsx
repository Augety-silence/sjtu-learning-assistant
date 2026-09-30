import { motion, useIsPresent, useReducedMotion } from "motion/react";
import { useRef } from "react";
import { RichPreviewContent } from "@/components/RichPreviewContent";
import { Button } from "@/components/ui/Button";
import { type PreviewerKind, selectPreviewer } from "@/lib/previewerRegistry";
import type { MaterialPreview } from "@/lib/types";
import { useModalFocus } from "@/lib/useModalFocus";

const previewerLabels: Record<PreviewerKind, string> = {
  pdf: "PDF",
  image: "图片",
  markdown: "Markdown",
  text: "文本",
  code: "代码",
  unsupported: "不支持预览",
};

function selectedPreviewer(preview: MaterialPreview) {
  const mimeType =
    preview.kind === "pdf"
      ? "application/pdf"
      : preview.kind === "image"
        ? "image/*"
        : "text/plain";
  const selected = selectPreviewer(mimeType, preview.name);
  if (preview.kind === "text" && selected === "unsupported") return "text";
  return selected;
}

function renderPreview(preview: MaterialPreview, previewer: PreviewerKind) {
  const compatible =
    (preview.kind === "image" && previewer === "image") ||
    (preview.kind === "pdf" && previewer === "pdf") ||
    (preview.kind === "text" &&
      ["text", "code", "markdown"].includes(previewer));
  if (!compatible) {
    return (
      <RichPreviewContent
        kind="unsupported"
        content="你仍可关闭预览，并在资料列表中打开或下载该文件。"
      />
    );
  }
  return (
    <RichPreviewContent
      kind={previewer}
      title={preview.name}
      content={preview.kind === "text" ? preview.text : undefined}
      sourceUrl={preview.kind === "text" ? undefined : preview.data_url}
      alt={preview.name}
    />
  );
}

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
  const reduceMotion = useReducedMotion();
  const previewer = selectedPreviewer(preview);
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
      initial={{ opacity: reduceMotion ? 1 : 0.01 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: reduceMotion ? 1 : 0 }}
      transition={{ duration: reduceMotion ? 0 : isPresent ? 0.14 : 0.12 }}
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
        initial={reduceMotion ? false : { opacity: 0.9, y: 6, scale: 0.99 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        exit={reduceMotion ? undefined : { opacity: 0.88, y: 4, scale: 0.99 }}
        transition={{
          duration: reduceMotion ? 0 : isPresent ? 0.18 : 0.14,
          ease: [0.16, 1, 0.3, 1],
        }}
      >
        <header className="material-preview-header">
          <div className="material-preview-heading">
            <h3 id="material-preview-title">{preview.name}</h3>
            <span>{previewerLabels[previewer]}</span>
          </div>
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
          {renderPreview(preview, previewer)}
        </div>
      </motion.section>
    </motion.div>
  );
}
