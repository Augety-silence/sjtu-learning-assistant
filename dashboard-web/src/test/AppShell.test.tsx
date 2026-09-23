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
  Object.defineProperty(window, "innerWidth", {
    configurable: true,
    value: 1024,
  });
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: undefined,
  });
});

describe("AppShell 移动导航", () => {
  it("保留全高壳体结构，并将设置放在侧栏底部辅助分组", () => {
    render(<Harness />);
    const shell = document.querySelector(".app-shell");
    const sidebar = shell?.querySelector(":scope > .sidebar");
    const workspace = shell?.querySelector(":scope > .workspace");
    const mainNavigation = within(sidebar as HTMLElement).getByRole(
      "navigation",
      { name: "主导航" },
    );
    const utilityNavigation = within(sidebar as HTMLElement).getByRole(
      "navigation",
      { name: "辅助导航" },
    );

    expect(shell).toBeTruthy();
    expect(sidebar).toBeTruthy();
    expect(workspace).toBeTruthy();
    expect(workspace?.querySelector(":scope > .page-header")).toBeTruthy();
    expect(within(mainNavigation).getAllByRole("button")).toHaveLength(7);
    expect(within(utilityNavigation).getAllByRole("button")).toHaveLength(1);
    const overviewButton = within(mainNavigation).getByRole("button", {
      name: "概览",
    });
    expect(overviewButton.getAttribute("aria-label")).toBe("概览");
    expect(overviewButton.getAttribute("title")).toBe("概览");
    expect(
      within(mainNavigation).queryByRole("button", { name: "设置" }),
    ).toBeNull();
    expect(
      within(utilityNavigation).getByRole("button", { name: "设置" }),
    ).toBeTruthy();
    expect(
      sidebar?.querySelector(".sidebar-lower")?.contains(utilityNavigation),
    ).toBe(true);
  });

  it("在左侧与移动导航中提供独立云盘备份入口", () => {
    render(<Harness />);
    const sidebar = document.querySelector(".sidebar");
    expect(sidebar).toBeTruthy();
    expect(
      within(sidebar as HTMLElement).getByRole("button", { name: "云盘备份" }),
    ).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "菜单" }));
    const dialog = screen.getByRole("dialog", { name: "移动导航" });
    fireEvent.click(within(dialog).getByRole("button", { name: "云盘备份" }));
    expect(
      screen.getByRole("heading", { level: 1, name: "云盘备份" }),
    ).toBeTruthy();
  });

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
