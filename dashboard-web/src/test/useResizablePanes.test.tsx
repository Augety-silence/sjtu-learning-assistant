// @vitest-environment jsdom
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useResizablePanes } from "@/lib/useResizablePanes";

function Panes() {
  const { leftWidth, rightWidth, beginResize, resizeWithKeyboard } =
    useResizablePanes();
  return (
    <>
      {(["left", "right"] as const).map((pane) => (
        <div
          key={pane}
          role="separator"
          aria-label={pane}
          aria-valuenow={pane === "left" ? leftWidth : rightWidth}
          tabIndex={0}
          onPointerDown={(event) => beginResize(pane, event)}
          onKeyDown={(event) => resizeWithKeyboard(pane, event)}
        />
      ))}
    </>
  );
}
let frames: Map<number, FrameRequestCallback>;
let sequence: number;
beforeEach(() => {
  frames = new Map();
  sequence = 0;
  vi.stubGlobal("PointerEvent", MouseEvent);
  vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
    frames.set(++sequence, callback);
    return sequence;
  });
  vi.spyOn(window, "cancelAnimationFrame").mockImplementation((id) => {
    frames.delete(id);
  });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  window.localStorage.clear();
});
function paint() {
  act(() => {
    const pending = [...frames.values()];
    frames.clear();
    for (const callback of pending) callback(16);
  });
}
describe("pane resize scheduling", () => {
  it("coalesces moves, saves once on release and flushes the final pending width", () => {
    render(<Panes />);
    const pane = screen.getByRole("separator", { name: "left" });
    const save = vi.spyOn(Storage.prototype, "setItem");
    fireEvent.pointerDown(pane, { button: 0, clientX: 100 });
    for (const clientX of [110, 130, 150])
      fireEvent.pointerMove(window, { clientX });
    expect(frames.size).toBe(1);
    expect(pane.getAttribute("aria-valuenow")).toBe("240");
    paint();
    expect(pane.getAttribute("aria-valuenow")).toBe("290");
    expect(save).not.toHaveBeenCalled();
    fireEvent.pointerMove(window, { clientX: 160 });
    fireEvent.pointerUp(window);
    expect(pane.getAttribute("aria-valuenow")).toBe("300");
    expect(frames.size).toBe(0);
    expect(save).toHaveBeenCalledExactlyOnceWith("ai-sidebar-width", "300");
    expect(document.body.style.cursor).toBe("");
  });
  it("cancels pending frames and listeners when unmounted during a drag", () => {
    const { unmount } = render(<Panes />);
    fireEvent.pointerDown(screen.getByRole("separator", { name: "right" }), {
      button: 0,
      clientX: 100,
    });
    fireEvent.pointerMove(window, { clientX: 110 });
    expect(frames.size).toBe(1);
    unmount();
    expect(frames.size).toBe(0);
    expect(document.body.style.userSelect).toBe("");
    fireEvent.pointerMove(window, { clientX: 200 });
    expect(frames.size).toBe(0);
    expect(window.localStorage.getItem("ai-activity-width")).toBeNull();
  });
  it("loads saved bounds and persists keyboard steps for both panes", () => {
    window.localStorage.setItem("ai-sidebar-width", "1000");
    render(<Panes />);
    const left = screen.getByRole("separator", { name: "left" });
    const right = screen.getByRole("separator", { name: "right" });
    expect(left.getAttribute("aria-valuenow")).toBe("320");
    fireEvent.keyDown(left, { key: "ArrowLeft", shiftKey: true });
    expect(left.getAttribute("aria-valuenow")).toBe("296");
    expect(window.localStorage.getItem("ai-sidebar-width")).toBe("296");
    fireEvent.keyDown(right, { key: "ArrowRight", shiftKey: true });
    expect(right.getAttribute("aria-valuenow")).toBe("300");
    expect(window.localStorage.getItem("ai-activity-width")).toBe("300");
  });
});
