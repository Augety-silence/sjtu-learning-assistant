// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { useEffect } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ContentReveal } from "@/components/ContentReveal";
import { cleanup, fireEvent, render, screen } from "@/test/render";

const { reducedMotion } = vi.hoisted(() => ({
  reducedMotion: vi.fn(() => false),
}));
vi.mock("motion/react", async (importOriginal) => ({
  ...(await importOriginal<typeof import("motion/react")>()),
  useReducedMotion: reducedMotion,
}));
afterEach(() => {
  cleanup();
  reducedMotion.mockReturnValue(false);
});

describe("ready content reveal", () => {
  it("shows loading immediately and reveals ready content without remounting or losing edits", () => {
    const mounted = vi.fn();
    const unmounted = vi.fn();
    function Content({ label }: { label: string }) {
      useEffect(() => {
        mounted();
        return unmounted;
      }, []);
      return (
        <>
          <span>{label}</span>
          <input aria-label="草稿" defaultValue="初稿" />
        </>
      );
    }
    const { rerender, container } = render(
      <ContentReveal as="main" className="video-workbench" ready={false}>
        <Content label="正在加载" />
      </ContentReveal>,
    );
    const body = container.querySelector("main");
    expect(body?.getAttribute("data-content-reveal")).toBe("loading");
    expect(screen.getByText("正在加载")).toBeTruthy();
    const input = screen.getByRole("textbox");
    fireEvent.change(input, { target: { value: "保留编辑" } });
    rerender(
      <ContentReveal as="main" className="video-workbench" ready>
        <Content label="正文" />
      </ContentReveal>,
    );
    expect(container.querySelector("main")).toBe(body);
    expect(body?.getAttribute("data-content-reveal")).toBe("ready");
    expect(body?.style.getPropertyValue("--content-reveal-duration")).toBe(
      "420ms",
    );
    expect(screen.getByRole("textbox")).toBe(input);
    expect((input as HTMLInputElement).value).toBe("保留编辑");
    rerender(
      <ContentReveal as="main" className="video-workbench" ready>
        <Content label="追加回复" />
      </ContentReveal>,
    );
    expect(body?.getAttribute("data-content-reveal")).toBe("ready");
    expect(mounted).toHaveBeenCalledTimes(1);
    expect(unmounted).not.toHaveBeenCalled();
  });

  it("uses the configured immediate, loaded and detail durations without a delay", () => {
    const { container, rerender } = render(
      <ContentReveal as="section">正文</ContentReveal>,
    );
    const body = container.querySelector("section");
    expect(body?.style.getPropertyValue("--content-reveal-duration")).toBe(
      "320ms",
    );
    rerender(
      <ContentReveal as="section" afterLoad>
        加载后的正文
      </ContentReveal>,
    );
    expect(body?.style.getPropertyValue("--content-reveal-duration")).toBe(
      "420ms",
    );
    rerender(
      <ContentReveal as="section" variant="detail" afterLoad>
        详情正文
      </ContentReveal>,
    );
    expect(body?.style.getPropertyValue("--content-reveal-duration")).toBe(
      "360ms",
    );
    expect(body?.style.animationDelay).toBe("");
    expect(screen.getByText("详情正文")).toBeTruthy();
  });

  it("disables the reveal when reduced motion is requested", () => {
    reducedMotion.mockReturnValue(true);
    const { container } = render(
      <ContentReveal ready afterLoad>
        立即可读
      </ContentReveal>,
    );
    expect(
      container.firstElementChild?.getAttribute("data-content-reveal"),
    ).toBe("reduced");
    expect(screen.getByText("立即可读")).toBeTruthy();
  });

  it("reveals page blocks once and preserves a focused draft through later loading", () => {
    const { container, rerender } = render(
      <ContentReveal ready={false}>
        <input aria-label="可继续编辑的草稿" defaultValue="" />
      </ContentReveal>,
    );
    const body = container.firstElementChild;
    const input = screen.getByRole("textbox") as HTMLInputElement;
    expect(body?.getAttribute("data-page-reveal")).toBe("loading");
    rerender(
      <ContentReveal ready>
        <input aria-label="可继续编辑的草稿" defaultValue="" />
      </ContentReveal>,
    );
    input.focus();
    fireEvent.change(input, { target: { value: "下一步继续写" } });
    for (const ready of [false, true, true]) {
      rerender(
        <ContentReveal ready={ready}>
          <input aria-label="可继续编辑的草稿" defaultValue="" />
        </ContentReveal>,
      );
      expect(container.firstElementChild).toBe(body);
      expect(body?.getAttribute("data-page-reveal")).toBe("ready");
      expect(screen.getByRole("textbox")).toBe(input);
      expect(input.value).toBe("下一步继续写");
      expect(document.activeElement).toBe(input);
    }
  });

  it("supports manual page sequencing and leaves detail reveals independent", () => {
    const { container, rerender } = render(
      <ContentReveal sequence="manual">页面框架</ContentReveal>,
    );
    const body = container.firstElementChild;
    expect(body?.getAttribute("data-page-reveal-sequence")).toBe("manual");
    expect(body?.getAttribute("data-reveal-variant")).toBe("page");
    rerender(<ContentReveal variant="detail">加载就绪的正文</ContentReveal>);
    expect(body?.getAttribute("data-reveal-variant")).toBe("detail");
    expect(body?.hasAttribute("data-page-reveal")).toBe(false);
    expect(body?.hasAttribute("data-page-reveal-sequence")).toBe(false);
  });

  it("only animates opacity and has a CSS reduced-motion fallback", () => {
    const css = readFileSync(
      resolve(process.cwd(), "src/performance.css"),
      "utf8",
    );
    const reveal =
      css.match(/@keyframes content-reveal-in\s*\{[\s\S]*?\n\}/)?.[0] ?? "";
    expect(reveal).not.toBe("");
    expect(reveal).toContain("opacity: 0.55");
    expect(reveal).toContain("opacity: 1");
    expect(reveal).not.toMatch(
      /\b(transform|height|width|filter|scale|translate)\s*:/,
    );
    expect(css).toMatch(
      /@media \(prefers-reduced-motion: reduce\)\s*\{\s*\[data-content-reveal\]\s*\{[^}]*animation: none/,
    );
  });
});
