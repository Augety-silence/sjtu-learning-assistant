import { motion, useIsPresent, useReducedMotion } from "motion/react";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ContentReveal } from "@/components/ContentReveal";
import { RichPreviewContent } from "@/components/RichPreviewContent";
import { Button } from "@/components/ui/Button";
import { motionDuration, motionEase } from "@/lib/motion";
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

function usePdfSource(preview: MaterialPreview): string | null {
  const dataUrl = preview.kind === "pdf" ? preview.data_url : null;
  const [source, setSource] = useState<{
    dataUrl: string;
    src: string;
  } | null>(null);
  useEffect(() => {
    if (!dataUrl) {
      setSource(null);
      return;
    }
    const isPdfDataUrl = /^data:application\/pdf(;[^,]*)?;base64,/i.test(
      dataUrl,
    );
    if (
      !isPdfDataUrl ||
      typeof fetch !== "function" ||
      typeof URL.createObjectURL !== "function"
    ) {
      setSource({ dataUrl, src: dataUrl });
      return;
    }
    const controller = new AbortController();
    let objectUrl: string | null = null;
    // Chromium/WebView2 caps data: URLs at 2MB. Let the browser decode the
    // Base64 asynchronously so a 30 MiB PDF cannot block the dialog controls.
    void fetch(dataUrl, { signal: controller.signal })
      .then((response) => response.blob())
      .then((blob) => {
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(
          new Blob([blob], { type: "application/pdf" }),
        );
        setSource({ dataUrl, src: objectUrl });
      })
      .catch(() => {
        if (!controller.signal.aborted) setSource({ dataUrl, src: dataUrl });
      });
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [dataUrl]);
  return source && source.dataUrl === dataUrl ? source.src : null;
}

function renderPreview(
  preview: MaterialPreview,
  previewer: PreviewerKind,
  pdfSource: string | null,
) {
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
  if (preview.kind === "pdf" && !pdfSource) return null;
  return (
    <RichPreviewContent
      kind={previewer}
      title={preview.name}
      content={preview.kind === "text" ? preview.text : undefined}
      sourceUrl={
        preview.kind === "text"
          ? undefined
          : preview.kind === "pdf"
            ? pdfSource
            : preview.data_url
      }
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
  const pdfSource = usePdfSource(preview);
  const mediaKey =
    preview.kind === "text" ? null : `${preview.kind}:${preview.data_url}`;
  const [readyMediaKey, setReadyMediaKey] = useState<string | null>(null);
  const latestMediaKey = useRef(mediaKey);
  latestMediaKey.current = mediaKey;
  const mediaReady =
    preview.kind === "text" ||
    previewer === "unsupported" ||
    readyMediaKey === mediaKey;
  useModalFocus(dialogRef, onClose, {
    initialFocusRef: closeButtonRef,
    active: isPresent,
  });

  return createPortal(
    <div
      className="message-dialog-layer material-preview-layer"
      data-modal-layer
      data-motion-layer="modal"
      aria-hidden={isPresent ? undefined : true}
    >
      <motion.button
        type="button"
        className="message-dialog-backdrop"
        initial={reduceMotion ? false : { opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        transition={{
          type: "tween",
          duration: reduceMotion
            ? 0
            : isPresent
              ? motionDuration.control
              : motionDuration.fast,
        }}
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
        initial={reduceMotion ? false : { opacity: 0.55 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        transition={{
          type: "tween",
          duration: reduceMotion
            ? 0
            : isPresent
              ? motionDuration.overlayEnter
              : motionDuration.fast,
          ease: motionEase.out,
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
        <ContentReveal
          className="material-preview-content"
          style={{ position: "relative" }}
          variant="detail"
          ready={mediaReady}
          aria-busy={!mediaReady}
          onLoadCapture={async (event) => {
            const target = event.target;
            const requestedMediaKey = mediaKey;
            if (target instanceof HTMLImageElement && target.decode) {
              try {
                await target.decode();
              } catch {
                // The load event still allows a useful preview if decode is unavailable.
              }
            } else if (
              !(target instanceof HTMLIFrameElement) &&
              !(target instanceof HTMLImageElement)
            ) {
              return;
            }
            // iframe load is a reveal trigger, not proof that PDF pixels have painted.
            if (latestMediaKey.current === requestedMediaKey)
              setReadyMediaKey(requestedMediaKey);
          }}
          onErrorCapture={() => setReadyMediaKey(mediaKey)}
        >
          {!mediaReady && (
            <span
              role="status"
              style={{ position: "absolute", top: 12, left: 12 }}
            >
              正在准备预览内容…
            </span>
          )}
          {renderPreview(preview, previewer, pdfSource)}
        </ContentReveal>
      </motion.section>
    </div>,
    document.body,
  );
}
