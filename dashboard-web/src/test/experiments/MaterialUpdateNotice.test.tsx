// @vitest-environment jsdom
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MaterialUpdateNotice } from "@/components/experiments/MaterialUpdateNotice";
import { act, cleanup, fireEvent, render, screen } from "@/test/render";

const { reducedMotion } = vi.hoisted(() => ({
  reducedMotion: vi.fn(() => false),
}));
vi.mock("motion/react", async (importOriginal) => ({
  ...(await importOriginal<typeof import("motion/react")>()),
  useReducedMotion: reducedMotion,
}));

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  reducedMotion.mockReturnValue(false);
});

const notice = () => document.querySelector(".slw-update-notice");

function setup(
  props: Partial<Parameters<typeof MaterialUpdateNotice>[0]> = {},
) {
  const base = { motion: "static" as const, revision: "r1", ...props };
  const view = render(
    <StrictMode>
      <MaterialUpdateNotice {...base} />
    </StrictMode>,
  );
  const update = (next: Partial<typeof base>) =>
    view.rerender(
      <StrictMode>
        <MaterialUpdateNotice {...base} {...next} />
      </StrictMode>,
    );
  return { ...view, update };
}

describe("MaterialUpdateNotice", () => {
  it("treats the first revision as a baseline and ignores same-data polling", () => {
    const { update } = setup();
    expect(notice()).toBeNull();
    // Live region is always mounted, so insertion gets announced.
    expect(screen.getByRole("status").getAttribute("aria-live")).toBe("polite");
    for (let i = 0; i < 5; i += 1) update({ revision: "r1" });
    update({ revision: "r1", message: "无关的重新渲染" });
    expect(notice()).toBeNull();
  });

  it("shows once on a real change and only says materials were updated", () => {
    const { update } = setup();
    update({ revision: "r2" });
    expect(notice()).not.toBeNull();
    expect(screen.getByRole("status").textContent).toContain("材料有更新");
    expect(screen.getByRole("status").textContent).not.toMatch(
      /完成|全部|已就绪|%/,
    );
    update({ revision: "r2" });
    update({ revision: "r2" });
    expect(document.querySelectorAll(".slw-update-notice")).toHaveLength(1);
  });

  it("does not replay on rapid changes; keeps one element and extends the stay", () => {
    const { update } = setup({ autoHideMs: 1000 });
    update({ revision: "r2" });
    const first = notice();
    for (let i = 3; i < 10; i += 1) {
      act(() => {
        vi.advanceTimersByTime(400);
      });
      update({ revision: `r${i}` });
    }
    expect(notice()).toBe(first);
    act(() => {
      vi.advanceTimersByTime(999);
    });
    expect(notice()).toBe(first);
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(notice()).toBeNull();
  });

  it("ignores null / empty revisions without losing the baseline", () => {
    const { update } = setup({ revision: null });
    update({ revision: "" });
    update({ revision: "a" });
    expect(notice()).toBeNull();
    update({ revision: null });
    update({ revision: "a" });
    expect(notice()).toBeNull();
    update({ revision: Number.NaN as unknown as number });
    expect(notice()).toBeNull();
    update({ revision: "b" });
    expect(notice()).not.toBeNull();
  });

  it("never steals focus or scrolls; onView runs only on click", () => {
    const onView = vi.fn();
    const scrollTo = vi.fn();
    const scrollIntoView = vi.fn();
    window.scrollTo = scrollTo as unknown as typeof window.scrollTo;
    Element.prototype.scrollIntoView = scrollIntoView;
    const outside = document.createElement("button");
    document.body.append(outside);
    outside.focus();
    try {
      const { update } = setup({ onView });
      update({ revision: "r2" });
      expect(document.activeElement).toBe(outside);
      expect(scrollTo).not.toHaveBeenCalled();
      expect(scrollIntoView).not.toHaveBeenCalled();
      expect(onView).not.toHaveBeenCalled();

      fireEvent.click(screen.getByRole("button", { name: "查看" }));
      expect(onView).toHaveBeenCalledOnce();
      expect(notice()).toBeNull();
    } finally {
      outside.remove();
    }
  });

  it("hides after the timer, pauses while focused, and can be closed", () => {
    const { update } = setup({ autoHideMs: 1000 });
    update({ revision: "r2" });
    const close = screen.getByRole("button", { name: "关闭更新提示" });
    fireEvent.focus(close);
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(notice()).not.toBeNull();
    fireEvent.click(close);
    expect(notice()).toBeNull();

    update({ revision: "r3" });
    expect(notice()).not.toBeNull();
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(notice()).toBeNull();
  });

  it("keeps the notice when autoHideMs is null and cleans timers on unmount", () => {
    const { update, unmount } = setup({ autoHideMs: null });
    update({ revision: "r2" });
    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    expect(notice()).not.toBeNull();
    unmount();

    const second = setup({ autoHideMs: 1000 });
    second.update({ revision: "r2" });
    expect(vi.getTimerCount()).toBeGreaterThan(0);
    second.unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("renders a static notice under reduced motion", () => {
    reducedMotion.mockReturnValue(true);
    const { update } = setup({ motion: "auto" });
    update({ revision: "r2" });
    const root = screen.getByRole("status");
    expect(root.getAttribute("data-motion")).toBe("static");
    expect(notice()?.getAttribute("style")).toBeNull();
  });

  it("light motion keeps the same element across rapid changes", () => {
    const { update } = setup({ motion: "auto" });
    expect(screen.getByRole("status").getAttribute("data-motion")).toBe(
      "light",
    );
    update({ revision: "r2" });
    const first = notice();
    expect(first).not.toBeNull();
    update({ revision: "r3" });
    update({ revision: "r4" });
    expect(notice()).toBe(first);
    expect(document.querySelectorAll(".slw-update-notice")).toHaveLength(1);
  });
});
