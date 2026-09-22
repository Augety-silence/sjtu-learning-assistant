// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AppShell } from "@/components/AppShell";

const baseProps = {
  view: "overview" as const,
  setView: vi.fn(),
  setDrawerOpen: vi.fn(),
  syncStatus: null,
  syncing: false,
  onSync: vi.fn(),
};

describe("AppShell brand", () => {
  afterEach(cleanup);

  it("uses the generated logo in the desktop sidebar and mobile drawer", () => {
    render(
      <AppShell {...baseProps} drawerOpen>
        <div>content</div>
      </AppShell>,
    );
    const logos = screen.getAllByRole("img", { name: "SJTU 学习助手标志" });
    expect(logos).toHaveLength(2);
    expect(
      logos.every((logo) => logo.getAttribute("src")?.endsWith("app-logo.png")),
    ).toBe(true);
  });
});
