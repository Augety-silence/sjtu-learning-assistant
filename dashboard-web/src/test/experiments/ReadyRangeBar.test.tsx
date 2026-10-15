// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  normalizeRangesMs,
  ReadyRangeBar,
} from "@/components/experiments/ReadyRangeBar";
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

const MIN = 60_000;

describe("normalizeRangesMs", () => {
  it("merges overlaps and drops invalid, inverted, empty and out-of-range input", () => {
    expect(
      normalizeRangesMs(
        [
          [-1000, 60_000],
          [50_000, 180_000],
          [600_000, 480_000],
          [Number.NaN, 300_000],
          [1_200_000, 1_200_000],
          [2_400_000, 9_999_999],
          [3_000_000, 3_100_000],
        ],
        2_700_000,
      ),
    ).toEqual([
      [0, 180_000],
      [2_400_000, 2_700_000],
    ]);
  });

  it("returns an empty list for null input and keeps ranges when duration is unknown", () => {
    expect(normalizeRangesMs(null)).toEqual([]);
    expect(normalizeRangesMs(undefined, 1000)).toEqual([]);
    expect(
      normalizeRangesMs(
        [
          [5000, 9000],
          [1000, 5000],
        ],
        null,
      ),
    ).toEqual([[1000, 9000]]);
  });
});

describe("ReadyRangeBar", () => {
  it("never seeks on prop updates and seeks only after explicit confirmation", () => {
    const onSeekMs = vi.fn();
    const { rerender } = render(
      <ReadyRangeBar
        durationMs={10 * MIN}
        currentTimeMs={MIN}
        availableRangesMs={[[0, 2 * MIN]]}
        onSeekMs={onSeekMs}
      />,
    );
    rerender(
      <ReadyRangeBar
        durationMs={10 * MIN}
        currentTimeMs={3 * MIN}
        availableRangesMs={[
          [0, 2 * MIN],
          [4 * MIN, 6 * MIN],
        ]}
        onSeekMs={onSeekMs}
      />,
    );
    expect(onSeekMs).not.toHaveBeenCalled();

    const slider = screen.getByRole("slider");
    fireEvent.keyDown(slider, { key: "ArrowRight" });
    expect(onSeekMs).not.toHaveBeenCalled();
    expect(
      screen.getByRole("group", { name: "确认跳转" }).textContent,
    ).toContain("03:05（此处暂无学习材料）");

    fireEvent.click(screen.getByRole("button", { name: "确认跳转" }));
    expect(onSeekMs).toHaveBeenCalledExactlyOnceWith(3 * MIN + 5000);
    expect(screen.queryByRole("button", { name: "确认跳转" })).toBeNull();
  });

  it("supports keyboard: Home/End pick, Escape cancels, Enter confirms", () => {
    const onSeekMs = vi.fn();
    render(
      <ReadyRangeBar
        durationMs={10 * MIN}
        currentTimeMs={MIN}
        availableRangesMs={[[0, 2 * MIN]]}
        onSeekMs={onSeekMs}
      />,
    );
    const slider = screen.getByRole("slider");
    fireEvent.keyDown(slider, { key: "End" });
    expect(slider.getAttribute("aria-valuenow")).toBe(String(10 * MIN));
    fireEvent.keyDown(slider, { key: "Escape" });
    expect(screen.queryByRole("group", { name: "确认跳转" })).toBeNull();
    fireEvent.keyDown(slider, { key: "Enter" });
    expect(onSeekMs).not.toHaveBeenCalled();
    fireEvent.keyDown(slider, { key: "Home" });
    fireEvent.keyDown(slider, { key: "Enter" });
    expect(onSeekMs).toHaveBeenCalledExactlyOnceWith(0);
  });

  it("states coverage honestly without implying quality or progress", () => {
    const { container } = render(
      <ReadyRangeBar
        durationMs={10 * MIN}
        currentTimeMs={MIN}
        availableRangesMs={[
          [0, 2 * MIN],
          [MIN, 3 * MIN],
        ]}
      />,
    );
    expect(screen.getByText("已有材料覆盖 1 段")).toBeTruthy();
    expect(screen.getByText("当前位置有学习材料")).toBeTruthy();
    expect(
      screen.getByText("覆盖只表示已有材料，不代表已校对或质量通过。"),
    ).toBeTruthy();
    expect(container.textContent).not.toMatch(/%|预计|完成/);
    expect(screen.queryByRole("slider")).toBeNull();
  });

  it("handles empty data, unknown duration and missing position", () => {
    const onSeekMs = vi.fn();
    const { rerender } = render(
      <ReadyRangeBar durationMs={10 * MIN} availableRangesMs={null} />,
    );
    expect(screen.getByText("暂无已整理的学习材料区间")).toBeTruthy();
    expect(screen.getByText("播放位置未知")).toBeTruthy();

    rerender(
      <ReadyRangeBar
        durationMs={Number.NaN}
        currentTimeMs={null}
        availableRangesMs={[[2 * MIN, 3 * MIN]]}
        onSeekMs={onSeekMs}
      />,
    );
    expect(screen.getByText(/视频时长未知/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "02:00–03:00" }));
    expect(onSeekMs).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认跳转" }));
    expect(onSeekMs).toHaveBeenCalledExactlyOnceWith(2 * MIN);
  });

  it("renders statically when reduced motion is requested or motion is static", () => {
    reducedMotion.mockReturnValue(true);
    const { container, rerender } = render(
      <ReadyRangeBar durationMs={MIN} currentTimeMs={0} />,
    );
    expect(
      container.querySelector(".slw-range")?.getAttribute("data-motion"),
    ).toBe("static");
    reducedMotion.mockReturnValue(false);
    rerender(<ReadyRangeBar durationMs={MIN} currentTimeMs={0} />);
    expect(
      container.querySelector(".slw-range")?.getAttribute("data-motion"),
    ).toBe("light");
    rerender(
      <ReadyRangeBar motion="static" durationMs={MIN} currentTimeMs={0} />,
    );
    expect(
      container.querySelector(".slw-range")?.getAttribute("data-motion"),
    ).toBe("static");
  });
});
