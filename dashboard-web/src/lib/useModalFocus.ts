import { type RefObject, useEffect, useRef } from "react";

const FOCUSABLE = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

export interface ModalFocusOptions {
  initialFocusRef?: RefObject<HTMLElement | null>;
  triggerRef?: RefObject<HTMLElement | null>;
  shouldRestoreFocus?: () => boolean;
}

type BackgroundState = {
  element: HTMLElement;
  hadInert: boolean;
  ariaHidden: string | null;
};

function backgroundSiblings(container: HTMLElement): BackgroundState[] {
  const modalLayer = container.closest<HTMLElement>("[data-modal-layer]");
  const siblings = new Set<HTMLElement>();
  let current: HTMLElement | null = modalLayer ?? container;

  while (
    current?.parentElement &&
    current.parentElement !== document.documentElement
  ) {
    for (const sibling of current.parentElement.children) {
      if (sibling !== current && sibling instanceof HTMLElement) {
        siblings.add(sibling);
      }
    }
    current = current.parentElement;
  }

  return [...siblings].map((element) => ({
    element,
    hadInert: element.hasAttribute("inert"),
    ariaHidden: element.getAttribute("aria-hidden"),
  }));
}

export function useModalFocus(
  containerRef: RefObject<HTMLElement | null>,
  onDismiss: () => void,
  options: ModalFocusOptions = {},
) {
  const dismissRef = useRef(onDismiss);
  const optionsRef = useRef(options);
  dismissRef.current = onDismiss;
  optionsRef.current = options;

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const capturedTrigger =
      optionsRef.current.triggerRef?.current ??
      (document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null);
    const background = backgroundSiblings(container);

    for (const { element } of background) {
      element.setAttribute("inert", "");
      element.setAttribute("aria-hidden", "true");
    }

    const focusables = () =>
      Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
        (element) =>
          !element.hasAttribute("disabled") &&
          !element.hasAttribute("hidden") &&
          element.getAttribute("aria-hidden") !== "true" &&
          !element.closest("[inert]"),
      );

    const initialTimer = window.setTimeout(() => {
      const initial = optionsRef.current.initialFocusRef?.current;
      const target =
        initial?.isConnected && container.contains(initial)
          ? initial
          : (focusables()[0] ?? container);
      target.focus();
    }, 0);

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        dismissRef.current();
        return;
      }
      if (event.key !== "Tab") return;

      const items = focusables();
      if (items.length === 0) {
        event.preventDefault();
        container.focus();
        return;
      }

      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;
      const inside = active instanceof Node && container.contains(active);

      if (!inside) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      } else if (event.shiftKey && active === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", onKeyDown);

    return () => {
      window.clearTimeout(initialTimer);
      document.removeEventListener("keydown", onKeyDown);

      for (const { element, hadInert, ariaHidden } of background) {
        if (hadInert) element.setAttribute("inert", "");
        else element.removeAttribute("inert");

        if (ariaHidden === null) element.removeAttribute("aria-hidden");
        else element.setAttribute("aria-hidden", ariaHidden);
      }

      if (
        optionsRef.current.shouldRestoreFocus?.() !== false &&
        capturedTrigger?.isConnected
      ) {
        capturedTrigger.focus();
      }
    };
  }, [containerRef]);
}
