// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  formatClockMs,
  isFiniteNonNegative,
} from "@/components/experiments/widgetMotion";
import { WidgetLab } from "@/experiments/WidgetLab";
import { cleanup, fireEvent, render, screen } from "@/test/render";

afterEach(() => {
  cleanup();
  delete document.documentElement.dataset.theme;
});

describe("widget lab shell", () => {
  it("labels demo content as synthetic and switches theme and width", () => {
    window.matchMedia ??= vi.fn().mockReturnValue({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    });
    const { container } = render(<WidgetLab />);
    expect(screen.getByText("合成数据 · 实验中")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "深色" }));
    expect(document.documentElement.dataset.theme).toBe("dark");
    fireEvent.click(screen.getByRole("button", { name: "窄屏 340px" }));
    expect(
      container.querySelector(".widget-lab")?.getAttribute("data-width"),
    ).toBe("narrow");
    fireEvent.click(screen.getByRole("button", { name: "模拟减少动态效果" }));
    expect(
      container
        .querySelector(".widget-lab")
        ?.getAttribute("data-reduced-motion"),
    ).toBe("always");
  });
});

describe("widget helpers", () => {
  it("formats milliseconds as a clock and rejects invalid numbers", () => {
    expect(formatClockMs(0)).toBe("00:00");
    expect(formatClockMs(65_900)).toBe("01:05");
    expect(formatClockMs(3_726_000)).toBe("1:02:06");
    expect(formatClockMs(-5)).toBe("00:00");
    expect(isFiniteNonNegative(0)).toBe(true);
    expect(isFiniteNonNegative(-1)).toBe(false);
    expect(isFiniteNonNegative(Number.NaN)).toBe(false);
    expect(isFiniteNonNegative(Number.POSITIVE_INFINITY)).toBe(false);
    expect(isFiniteNonNegative(undefined)).toBe(false);
  });
});
