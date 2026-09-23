// @vitest-environment jsdom

import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppShell } from "@/components/AppShell";
import type { ViewName } from "@/lib/types";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@/test/render";

const mediaListeners = new Set<(event: MediaQueryListEvent) => void>();
let desktop = false;

function emitViewport(width: number) {
  desktop = width >= 600;
  Object.defineProperty(window, "innerWidth", {
    configurable: true,
    value: width,
  });
  for (const listener of mediaListeners) {
    listener({ matches: desktop } as MediaQueryListEvent);
  }
  window.dispatchEvent(new Event("resize"));
}

function Harness({ initiallyOpen = false }: { initiallyOpen?: boolean }) {
  const [view, setView] = useState<ViewName>("overview");
  const [drawerOpen, setDrawerOpen] = useState(initiallyOpen);
  return (
    <AppShell
      view={view}
      setView={setView}
      drawerOpen={drawerOpen}
      setDrawerOpen={setDrawerOpen}
      syncStatus={null}
      syncing={false}
      onSync={vi.fn()}
    >
      <div>content</div>
    </AppShell>
  );
}

beforeEach(() => {
  desktop = false;
  mediaListeners.clear();
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn(() => ({
      matches: desktop,
      media: "(min-width: 600px)",
      onchange: null,
      addEventListener: (
        _type: string,
        listener: (event: MediaQueryListEvent) => void,
      ) => mediaListeners.add(listener),
      removeEventListener: (
        _type: string,
        listener: (event: MediaQueryListEvent) => void,
      ) => mediaListeners.delete(listener),
      dispatchEvent: () => true,
    })),
  });
  emitViewport(599);
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
  mediaListeners.clear();
});

describe("AppShell 移动导航", () => {
  it("使用 dialog 语义、聚焦当前项并圈闭首尾焦点", async () => {
    render(<Harness />);
    const menu = screen.getByRole("button", { name: "菜单" });
    fireEvent.click(menu);

    const dialog = screen.getByRole("dialog", { name: "移动导航" });
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    const current = within(dialog).getByRole("button", { name: "概览" });
    const close = within(dialog).getByRole("button", { name: "关闭" });
    await waitFor(() => expect(document.activeElement).toBe(current));

    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(close);
    fireEvent.keyDown(document, { key: "Tab" });
    expect(document.activeElement).toBe(current);

    screen.getByRole("button", { name: "立即同步", hidden: true }).focus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(document.activeElement).toBe(current);
  });

  it.each(["Escape", "关闭", "关闭导航"])(
    "通过 %s 普通关闭并回到菜单",
    async (method) => {
      render(<Harness />);
      const menu = screen.getByRole("button", { name: "菜单" });
      fireEvent.click(menu);
      const dialog = screen.getByRole("dialog", { name: "移动导航" });
      await waitFor(() =>
        expect(document.activeElement).toBe(
          within(dialog).getByRole("button", { name: "概览" }),
        ),
      );

      if (method === "Escape") fireEvent.keyDown(document, { key: "Escape" });
      else fireEvent.click(screen.getByRole("button", { name: method }));

      expect(screen.queryByRole("dialog", { name: "移动导航" })).toBeNull();
      await waitFor(() => expect(document.activeElement).toBe(menu));
    },
  );

  it("选择导航后聚焦新页面 h1，首次加载不抢焦点", async () => {
    render(<Harness />);
    const initialHeading = screen.getByRole("heading", {
      level: 1,
      name: "概览",
    });
    expect(document.activeElement).not.toBe(initialHeading);

    fireEvent.click(screen.getByRole("button", { name: "菜单" }));
    const dialog = screen.getByRole("dialog", { name: "移动导航" });
    fireEvent.click(within(dialog).getByRole("button", { name: "消息" }));

    const heading = screen.getByRole("heading", { level: 1, name: "消息" });
    await waitFor(() => expect(document.activeElement).toBe(heading));
    expect(screen.queryByRole("dialog", { name: "移动导航" })).toBeNull();
  });

  it("从 599px 跨到 600px 时自动卸载并清理监听器", async () => {
    const { unmount } = render(<Harness initiallyOpen />);
    const menu = screen.getByRole("button", { name: "菜单", hidden: true });
    expect(screen.getByRole("dialog", { name: "移动导航" })).toBeTruthy();
    expect(mediaListeners.size).toBe(1);

    emitViewport(600);
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "移动导航" })).toBeNull(),
    );
    expect(menu.getAttribute("aria-expanded")).toBe("false");
    await waitFor(() => expect(document.activeElement).toBe(menu));

    unmount();
    expect(mediaListeners.size).toBe(0);
  });
});
