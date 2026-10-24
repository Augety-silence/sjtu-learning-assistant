import { type RefObject, useEffect, useRef } from "react";

const FOCUSABLE = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "summary",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

export interface ModalFocusOptions {
  initialFocusRef?: RefObject<HTMLElement | null>;
  triggerRef?: RefObject<HTMLElement | null>;
  shouldRestoreFocus?: () => boolean;
  dismissible?: boolean;
  active?: boolean;
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
      if (
        sibling !== current &&
        sibling instanceof HTMLElement &&
        // Live regions such as the toast viewport opt out of being inerted.
        !sibling.hasAttribute("data-modal-exempt")
      ) {
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

function isInsideClosedDetails(element: HTMLElement) {
  let details = element.closest("details");
  while (details) {
    const ownSummary =
      element.tagName === "SUMMARY" && element.parentElement === details;
    if (!details.open && !ownSummary) return true;
    details = details.parentElement?.closest("details") ?? null;
  }
  return false;
}

function tryFocus(
  element: HTMLElement | null | undefined,
  preventScroll = false,
): boolean {
  if (
    !element?.isConnected ||
    element.closest("[inert]") ||
    (typeof element.checkVisibility === "function" &&
      !element.checkVisibility())
  ) {
    return false;
  }
  element.focus({ preventScroll });
  return document.activeElement === element;
}

// The trigger may be hidden (e.g. the narrow-screen menu button once the
// window widens) or unmounted by the time the modal closes. Fall back to a
// still-open outer modal, then the page heading / main landmark, instead of
// silently dropping focus on <body>.
function restoreFocus(trigger: HTMLElement | null, container: HTMLElement) {
  if (trigger !== document.body && tryFocus(trigger)) return;
  const current = document.activeElement;
  if (
    current instanceof HTMLElement &&
    current !== document.body &&
    current.isConnected &&
    !container.contains(current)
  ) {
    return;
  }
  const openModals = Array.from(
    document.querySelectorAll<HTMLElement>("[aria-modal='true']"),
  ).filter((element) => element !== container && !container.contains(element));
  if (tryFocus(openModals[openModals.length - 1], true)) return;
  const main = document.querySelector<HTMLElement>("main");
  if (tryFocus(main?.querySelector<HTMLElement>("h1[tabindex]"), true)) return;
  if (main && !main.hasAttribute("tabindex")) main.tabIndex = -1;
  tryFocus(main, true);
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
  const active = options.active ?? true;

  useEffect(() => {
    if (!active) return;
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
          !element.closest("[inert]") &&
          element.tabIndex >= 0 &&
          !isInsideClosedDetails(element) &&
          (typeof element.checkVisibility !== "function" ||
            element.checkVisibility()),
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
      const modalLayer = container.closest("[data-modal-layer]");
      const modalLayers = document.querySelectorAll("[data-modal-layer]");
      if (
        modalLayer &&
        modalLayers.item(modalLayers.length - 1) !== modalLayer
      ) {
        return;
      }
      if (event.key === "Escape") {
        if (event.defaultPrevented || event.isComposing) return;
        event.preventDefault();
        if (optionsRef.current.dismissible !== false) dismissRef.current();
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

      if (!inside || active === container) {
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

      if (optionsRef.current.shouldRestoreFocus?.() !== false) {
        restoreFocus(capturedTrigger, container);
      }
    };
  }, [active, containerRef]);
}
