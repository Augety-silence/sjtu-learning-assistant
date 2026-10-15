// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  EvidencePeek,
  resolveEvidenceTiming,
} from "@/components/experiments/EvidencePeek";
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

describe("resolveEvidenceTiming", () => {
  it("never invents timestamps", () => {
    expect(resolveEvidenceTiming(undefined, 5000)).toEqual({
      startMs: null,
      endMs: null,
      label: "时间未知",
    });
    expect(resolveEvidenceTiming(-1, 5000).label).toBe("时间未知");
    expect(resolveEvidenceTiming(Number.NaN).label).toBe("时间未知");
    expect(resolveEvidenceTiming(90_000, 30_000)).toEqual({
      startMs: 90_000,
      endMs: null,
      label: "01:30",
    });
    expect(resolveEvidenceTiming(90_000, 95_500).label).toBe("01:30–01:35");
  });
});

describe.each(["inline", "popover"] as const)(
  "EvidencePeek (%s)",
  (variant) => {
    it("starts compact, expands on demand, seeks only on click", () => {
      const onSeekMs = vi.fn();
      render(
        <EvidencePeek
          variant={variant}
          quote="合成引文"
          sourceLabel="合成字幕"
          startMs={754_000}
          endMs={771_500}
          onSeekMs={onSeekMs}
        />,
      );
      const trigger = screen.getByRole("button", {
        name: "合成字幕 · 12:34–12:51",
      });
      expect(trigger.getAttribute("aria-expanded")).toBe("false");
      expect(screen.queryByText("合成引文")).toBeNull();

      fireEvent.click(trigger);
      expect(trigger.getAttribute("aria-expanded")).toBe("true");
      expect(screen.getByRole("region").textContent).toContain("合成引文");
      expect(onSeekMs).not.toHaveBeenCalled();
      expect(document.activeElement).not.toBe(
        screen.getByRole("button", { name: "跳到 12:34" }),
      );

      fireEvent.click(screen.getByRole("button", { name: "跳到 12:34" }));
      expect(onSeekMs).toHaveBeenCalledExactlyOnceWith(754_000);
    });

    it("closes on Escape and returns focus to the trigger", () => {
      reducedMotion.mockReturnValue(true);
      render(
        <EvidencePeek
          variant={variant}
          quote="合成引文"
          sourceLabel="合成字幕"
          startMs={1000}
          onSeekMs={() => undefined}
        />,
      );
      const trigger = screen.getByRole("button", { name: /合成字幕/ });
      fireEvent.click(trigger);
      const seek = screen.getByRole("button", { name: "跳到 00:01" });
      seek.focus();
      fireEvent.keyDown(seek, { key: "Escape" });
      expect(trigger.getAttribute("aria-expanded")).toBe("false");
      expect(screen.queryByRole("region")).toBeNull();
      expect(document.activeElement).toBe(trigger);
    });

    it("shows missing fields honestly and disables seeking without valid time", () => {
      const onSeekMs = vi.fn();
      render(
        <EvidencePeek
          variant={variant}
          motion="static"
          quote="   "
          sourceLabel={null}
          startMs={Number.NaN}
          onSeekMs={onSeekMs}
          defaultOpen
        />,
      );
      expect(
        screen.getByRole("button", { name: "来源未标注 · 时间未知" }),
      ).toBeTruthy();
      expect(screen.getByText("未提供字幕引文。")).toBeTruthy();
      expect(screen.getByText("缺少有效时间，无法跳转视频。")).toBeTruthy();
      expect(screen.queryByRole("button", { name: /跳到/ })).toBeNull();
      expect(onSeekMs).not.toHaveBeenCalled();
    });
  },
);

describe("EvidencePeek popover dismissal", () => {
  it("closes on outside pointer without moving focus", () => {
    render(
      <>
        <button type="button">外部按钮</button>
        <EvidencePeek
          variant="popover"
          motion="static"
          quote="合成引文"
          sourceLabel="合成字幕"
          startMs={0}
        />
      </>,
    );
    const outside = screen.getByRole("button", { name: "外部按钮" });
    const trigger = screen.getByRole("button", { name: /合成字幕/ });
    fireEvent.click(trigger);
    expect(screen.getByRole("region")).toBeTruthy();
    fireEvent.pointerDown(outside);
    expect(screen.queryByRole("region")).toBeNull();
    expect(document.activeElement).not.toBe(trigger);
  });

  it("inline variant stays open on outside pointer", () => {
    render(
      <EvidencePeek
        variant="inline"
        motion="static"
        quote="合成引文"
        sourceLabel="合成字幕"
        startMs={0}
        defaultOpen
      />,
    );
    fireEvent.pointerDown(document.body);
    expect(screen.getByRole("region")).toBeTruthy();
  });
});
