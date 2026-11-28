import { X } from "lucide-react";
import { type ReactNode, useId, useRef } from "react";
import { createPortal } from "react-dom";
import { useModalFocus } from "@/lib/useModalFocus";

/** 扩展配置独立打开，列表保持原来的位置和密度。 */
export function ExtensionConfigDialog({
  title,
  onClose,
  actions,
  children,
}: {
  title: string;
  onClose: () => void;
  actions?: ReactNode;
  children: ReactNode;
}) {
  const titleId = useId();
  const dialog = useRef<HTMLElement>(null);
  const close = useRef<HTMLButtonElement>(null);
  useModalFocus(dialog, onClose, { initialFocusRef: close });
  return createPortal(
    <div className="extension-modal-layer" data-modal-layer>
      <button
        className="extension-modal-backdrop"
        type="button"
        tabIndex={-1}
        aria-label={`关闭${title}`}
        onClick={onClose}
      />
      <section
        ref={dialog}
        className="extension-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        <header className="extension-modal-header">
          <h2 id={titleId}>{title}</h2>
          <div>
            {actions}
            <button
              ref={close}
              type="button"
              className="extension-modal-close"
              aria-label="关闭配置窗口"
              onClick={onClose}
            >
              <X size={18} />
            </button>
          </div>
        </header>
        <div className="extension-modal-body">{children}</div>
      </section>
    </div>,
    document.body,
  );
}
