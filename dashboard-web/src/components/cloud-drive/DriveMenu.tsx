import type { LucideIcon } from "lucide-react";
import {
  type KeyboardEvent as ReactKeyboardEvent,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import { cn } from "@/lib/utils";

export interface DriveMenuItem {
  key: string;
  label: string;
  icon?: LucideIcon;
  danger?: boolean;
  disabled?: boolean;
  separatorBefore?: boolean;
  shortcut?: string;
  onSelect: () => void;
}

export interface MenuAnchor {
  x: number;
  y: number;
  /** 由按钮触发时对齐按钮右下角；右键菜单则以指针位置为准。 */
  alignRight?: boolean;
}

/** 轻量下拉/右键菜单：方向键导航、Esc 关闭并把焦点还给触发元素。 */
export function DriveMenu({
  anchor,
  items,
  label,
  onClose,
  returnFocusTo,
}: {
  anchor: MenuAnchor;
  items: DriveMenuItem[];
  label: string;
  onClose: () => void;
  returnFocusTo?: HTMLElement | null;
}) {
  const menuRef = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState({ left: anchor.x, top: anchor.y });
  const enabled = items.filter((item) => !item.disabled);
  // 父组件传入的是内联 onClose；传输进度轮询会让父组件每 800ms 重渲染，
  // 若把 onClose 放进依赖，effect 会反复执行并把焦点抢回第一项。
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useLayoutEffect(() => {
    const menu = menuRef.current;
    if (!menu) return;
    const { width, height } = menu.getBoundingClientRect();
    const margin = 8;
    let left = anchor.alignRight ? anchor.x - width : anchor.x;
    let top = anchor.y;
    if (left + width > window.innerWidth - margin)
      left = window.innerWidth - width - margin;
    if (top + height > window.innerHeight - margin)
      top = Math.max(margin, anchor.y - height - (anchor.alignRight ? 36 : 0));
    setPosition({ left: Math.max(margin, left), top: Math.max(margin, top) });
  }, [anchor]);

  useEffect(() => {
    const first = menuRef.current?.querySelector<HTMLElement>(
      '[role="menuitem"]:not([aria-disabled="true"])',
    );
    first?.focus();
    const close = (event: Event) => {
      if (menuRef.current?.contains(event.target as Node)) return;
      onCloseRef.current();
    };
    const closeOnScroll = () => onCloseRef.current();
    window.addEventListener("pointerdown", close, true);
    window.addEventListener("resize", closeOnScroll);
    window.addEventListener("blur", closeOnScroll);
    return () => {
      window.removeEventListener("pointerdown", close, true);
      window.removeEventListener("resize", closeOnScroll);
      window.removeEventListener("blur", closeOnScroll);
    };
  }, []);

  const dismiss = (restore = true) => {
    onClose();
    if (restore) returnFocusTo?.focus();
  };

  const onKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    const nodes = Array.from(
      menuRef.current?.querySelectorAll<HTMLElement>(
        '[role="menuitem"]:not([aria-disabled="true"])',
      ) ?? [],
    );
    const index = nodes.indexOf(document.activeElement as HTMLElement);
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const delta = event.key === "ArrowDown" ? 1 : -1;
      nodes[(index + delta + nodes.length) % nodes.length]?.focus();
    } else if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      nodes[event.key === "Home" ? 0 : nodes.length - 1]?.focus();
    } else if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      dismiss();
    } else if (event.key === "Tab") {
      event.preventDefault();
      dismiss();
    }
  };

  return createPortal(
    <div
      ref={menuRef}
      className="drive-menu"
      role="menu"
      aria-label={label}
      style={{ left: position.left, top: position.top }}
      onKeyDown={onKeyDown}
      onContextMenu={(event) => event.preventDefault()}
    >
      {items.map((item) => {
        const Icon = item.icon;
        return (
          <div key={item.key} role="none">
            {item.separatorBefore && (
              <div className="drive-menu-separator" role="separator" />
            )}
            <button
              type="button"
              role="menuitem"
              tabIndex={-1}
              aria-disabled={item.disabled || undefined}
              className={cn("drive-menu-item", item.danger && "is-danger")}
              onClick={() => {
                if (item.disabled) return;
                dismiss(false);
                item.onSelect();
              }}
            >
              {Icon ? (
                <Icon aria-hidden="true" />
              ) : (
                <span className="drive-menu-icon-gap" />
              )}
              <span>{item.label}</span>
              {item.shortcut && (
                <kbd className="drive-menu-shortcut">{item.shortcut}</kbd>
              )}
            </button>
          </div>
        );
      })}
      {enabled.length === 0 && <p className="drive-menu-empty">没有可用操作</p>}
    </div>,
    document.body,
  );
}
