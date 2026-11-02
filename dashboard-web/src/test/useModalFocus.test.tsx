// @vitest-environment jsdom

import { useRef } from "react";
import { createPortal } from "react-dom";
import { afterEach, describe, expect, it } from "vitest";
import { useModalFocus } from "@/lib/useModalFocus";
import { cleanup, render, waitFor } from "@/test/render";

afterEach(cleanup);

function TestModal({ label }: { label: string }) {
  const dialogRef = useRef<HTMLDivElement>(null);
  useModalFocus(dialogRef, () => undefined, {
    shouldRestoreFocus: () => false,
  });
  return createPortal(
    <div data-modal-layer="">
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={label}
        tabIndex={-1}
      >
        <button type="button">close</button>
      </div>
    </div>,
    document.body,
  );
}

describe("useModalFocus background ownership", () => {
  it("keeps the page locked when overlapping modals close out of order", async () => {
    const view = render(
      <div>
        <TestModal key="first" label="first" />
        <TestModal key="second" label="second" />
      </div>,
    );

    await waitFor(() =>
      expect(view.container.hasAttribute("inert")).toBe(true),
    );
    expect(view.container.getAttribute("aria-hidden")).toBe("true");
    const layers = document.querySelectorAll<HTMLElement>("[data-modal-layer]");
    expect(layers).toHaveLength(2);
    expect(layers[0].hasAttribute("inert")).toBe(true);
    expect(layers[1].hasAttribute("inert")).toBe(false);

    view.rerender(
      <div>
        <TestModal key="second" label="second" />
      </div>,
    );
    expect(view.container.hasAttribute("inert")).toBe(true);
    expect(view.container.getAttribute("aria-hidden")).toBe("true");

    view.rerender(<div />);
    await waitFor(() =>
      expect(view.container.hasAttribute("inert")).toBe(false),
    );
    expect(view.container.hasAttribute("aria-hidden")).toBe(false);
  });
});
