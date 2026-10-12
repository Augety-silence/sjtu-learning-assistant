// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { WorkspaceTransition } from "@/components/WorkspaceTransition";
import { cleanup, render, screen, waitFor } from "@/test/render";

const { reduced } = vi.hoisted(() => ({ reduced: vi.fn(() => false) }));
vi.mock("motion/react", async (original) => ({
  ...(await original<typeof import("motion/react")>()),
  useReducedMotion: reduced,
}));
afterEach(() => {
  cleanup();
  reduced.mockReturnValue(false);
});

const workbench = (
  <div className="page-heading">
    <h1 tabIndex={-1}>概览</h1>
  </div>
);
const assistant = <textarea aria-label="输入问题" defaultValue="草稿" />;

describe("workspace navigation", () => {
  it("mounts the new workspace immediately, disables the leaving one and moves focus", async () => {
    const { container, rerender } = render(
      <WorkspaceTransition mode="workbench">{workbench}</WorkspaceTransition>,
    );
    expect(document.activeElement).not.toBe(screen.getByRole("heading"));
    rerender(
      <WorkspaceTransition mode="assistant">{assistant}</WorkspaceTransition>,
    );
    expect(screen.getByRole("textbox")).toBe(document.activeElement);
    const leaving = container.querySelector('[data-workspace-state="exiting"]');
    expect(leaving?.hasAttribute("inert")).toBe(true);
    expect(leaving?.getAttribute("aria-hidden")).toBe("true");
    await waitFor(() =>
      expect(container.querySelectorAll(".workspace-scene")).toHaveLength(1),
    );
    rerender(
      <WorkspaceTransition mode="workbench">{workbench}</WorkspaceTransition>,
    );
    expect(screen.getByRole("heading")).toBe(document.activeElement);
  });

  it("keeps the current input mounted while the same workspace updates", () => {
    const { rerender } = render(
      <WorkspaceTransition mode="assistant">{assistant}</WorkspaceTransition>,
    );
    const input = screen.getByRole("textbox") as HTMLTextAreaElement;
    input.value = "仍在编辑";
    input.focus();
    rerender(
      <WorkspaceTransition mode="assistant">
        {assistant}
        <p>状态更新</p>
      </WorkspaceTransition>,
    );
    expect(screen.getByRole("textbox")).toBe(input);
    expect(input.value).toBe("仍在编辑");
    expect(document.activeElement).toBe(input);
  });

  it("makes only the final workspace interactive during a rapid round trip", async () => {
    const { container, rerender } = render(
      <WorkspaceTransition mode="assistant">{assistant}</WorkspaceTransition>,
    );
    const input = screen.getByRole("textbox") as HTMLTextAreaElement;
    input.value = "快速返回的草稿";
    rerender(
      <WorkspaceTransition mode="workbench">{workbench}</WorkspaceTransition>,
    );
    rerender(
      <WorkspaceTransition mode="assistant">{assistant}</WorkspaceTransition>,
    );
    expect(
      container.querySelectorAll('[data-workspace-state="active"]'),
    ).toHaveLength(1);
    expect(screen.getByRole("textbox")).toBe(document.activeElement);
    expect(screen.getByRole("textbox")).toBe(input);
    expect(input.value).toBe("快速返回的草稿");
    await waitFor(() =>
      expect(container.querySelectorAll(".workspace-scene")).toHaveLength(1),
    );
    expect(screen.queryByRole("heading")).toBeNull();
  });

  it("replaces workspaces without a retained exit when reduced motion is enabled", async () => {
    reduced.mockReturnValue(true);
    const { container, rerender } = render(
      <WorkspaceTransition mode="workbench">{workbench}</WorkspaceTransition>,
    );
    rerender(
      <WorkspaceTransition mode="assistant">{assistant}</WorkspaceTransition>,
    );
    await waitFor(() =>
      expect(container.querySelectorAll(".workspace-scene")).toHaveLength(1),
    );
    expect(screen.queryByRole("heading")).toBeNull();
  });

  it("retains the input and draft when the motion preference changes", () => {
    const { rerender } = render(
      <WorkspaceTransition mode="assistant">{assistant}</WorkspaceTransition>,
    );
    const input = screen.getByRole("textbox") as HTMLTextAreaElement;
    input.value = "未发送的草稿";
    for (const preference of [true, false]) {
      reduced.mockReturnValue(preference);
      rerender(
        <WorkspaceTransition mode="assistant">{assistant}</WorkspaceTransition>,
      );
      expect(screen.getByRole("textbox")).toBe(input);
      expect(input.value).toBe("未发送的草稿");
    }
  });
});
