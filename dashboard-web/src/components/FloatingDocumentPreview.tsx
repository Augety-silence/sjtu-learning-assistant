import { X } from "lucide-react";
import { type ReactNode, useId, useRef } from "react";
import { createPortal } from "react-dom";
import { useModalFocus } from "@/lib/useModalFocus";
import "@/v2/file-browser.css";

/** 文件快速查看独立于目录布局，关闭后回到原文件和滚动位置。 */
export function FloatingDocumentPreview({
  title,
  detail,
  actions,
  footer,
  children,
  onClose,
}: {
  title: string;
  detail?: ReactNode;
  actions?: ReactNode;
  footer?: ReactNode;
  children: ReactNode;
  onClose: () => void;
}) {
  const titleId = useId();
  const dialog = useRef<HTMLElement>(null);
  const close = useRef<HTMLButtonElement>(null);
  useModalFocus(dialog, onClose, { initialFocusRef: close });
  return createPortal(
    <div className="v2-file-preview-layer" data-modal-layer>
      <button
        type="button"
        className="v2-file-preview-backdrop"
        tabIndex={-1}
        aria-label="点击遮罩关闭文件预览"
        onClick={onClose}
      />
      <section
        ref={dialog}
        className="v2-file-preview-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
      >
        <header className="v2-file-preview-header">
          <div className="v2-file-preview-heading">
            <h2 id={titleId} title={title}>
              {title}
            </h2>
            {detail && <div>{detail}</div>}
          </div>
          <div className="v2-file-preview-actions">
            {actions}
            <button
              ref={close}
              type="button"
              className="v2-file-preview-close"
              aria-label="关闭文件预览"
              title="关闭（Esc）"
              onClick={onClose}
            >
              <X size={18} />
            </button>
          </div>
        </header>
        <div className="v2-file-preview-body">{children}</div>
        {footer && <footer className="v2-file-preview-footer">{footer}</footer>}
      </section>
    </div>,
    document.body,
  );
}
