// @vitest-environment jsdom
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RecallCard } from "@/components/experiments/RecallCard";
import { act, cleanup, fireEvent, render, screen } from "@/test/render";

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

function Host({
  variant,
  answer = "合成答案",
  onChange,
}: {
  variant: "expand" | "crossfade";
  answer?: string | null;
  onChange?: (next: boolean) => void;
}) {
  const [revealed, setRevealed] = useState(false);
  return (
    <RecallCard
      variant={variant}
      motion="static"
      question="合成题目"
      answer={answer}
      revealed={revealed}
      onRevealChange={(next) => {
        onChange?.(next);
        setRevealed(next);
      }}
    />
  );
}

describe.each(["expand", "crossfade"] as const)(
  "RecallCard (%s)",
  (variant) => {
    it("keeps the hidden answer out of the DOM and the tab order", () => {
      render(<Host variant={variant} />);
      expect(screen.getByText("合成题目")).toBeTruthy();
      expect(document.body.textContent).not.toContain("合成答案");
      expect(screen.queryByRole("region", { name: "参考答案" })).toBeNull();
      const toggle = screen.getByRole("button", { name: "显示答案" });
      expect(toggle.getAttribute("aria-expanded")).toBe("false");
      expect(toggle.hasAttribute("aria-controls")).toBe(false);
    });

    it("is controlled: only the host's state reveals the answer", () => {
      const onRevealChange = vi.fn();
      const { rerender } = render(
        <RecallCard
          variant={variant}
          motion="static"
          question="合成题目"
          answer="合成答案"
          revealed={false}
          onRevealChange={onRevealChange}
        />,
      );
      fireEvent.click(screen.getByRole("button", { name: "显示答案" }));
      expect(onRevealChange).toHaveBeenCalledExactlyOnceWith(true);
      // Host has not applied the change yet, so nothing is revealed.
      expect(document.body.textContent).not.toContain("合成答案");

      rerender(
        <RecallCard
          variant={variant}
          motion="static"
          question="合成题目"
          answer="合成答案"
          revealed
          onRevealChange={onRevealChange}
        />,
      );
      const region = screen.getByRole("region", { name: "参考答案" });
      expect(region.textContent).toContain("合成答案");
      const toggle = screen.getByRole("button", { name: "隐藏答案" });
      expect(toggle.getAttribute("aria-expanded")).toBe("true");
      expect(toggle.getAttribute("aria-controls")).toBe(region.id);
    });

    it("states a missing answer or question explicitly", () => {
      render(
        <RecallCard
          variant={variant}
          motion="static"
          question={null}
          answer="   "
          revealed
          onRevealChange={() => undefined}
        />,
      );
      expect(screen.getByText("未提供题目。")).toBeTruthy();
      expect(
        screen.getByText("未提供参考答案。可以回到视频或讲义自行核对。"),
      ).toBeTruthy();
    });

    it("works from the keyboard and never shows progress claims", () => {
      const onChange = vi.fn();
      render(<Host variant={variant} onChange={onChange} />);
      const toggle = screen.getByRole("button", { name: "显示答案" });
      toggle.focus();
      // Native button: Enter/Space dispatch click.
      fireEvent.click(toggle);
      expect(onChange).toHaveBeenLastCalledWith(true);
      expect(document.activeElement).toBe(toggle);
      expect(document.body.textContent).toContain("合成答案");
      expect(document.body.textContent).not.toMatch(/正确率|掌握|已学会|%/);
      fireEvent.click(toggle);
      expect(onChange).toHaveBeenLastCalledWith(false);
      expect(document.body.textContent).not.toContain("合成答案");
    });
  },
);

describe("RecallCard motion", () => {
  it("falls back to static under reduced motion", () => {
    reducedMotion.mockReturnValue(true);
    render(
      <RecallCard
        question="合成题目"
        answer="合成答案"
        revealed={false}
        onRevealChange={() => undefined}
      />,
    );
    expect(
      document.querySelector(".slw-recall")?.getAttribute("data-motion"),
    ).toBe("static");
  });

  it("marks an exiting answer inert and hidden during light motion", () => {
    vi.useFakeTimers();
    try {
      const props = {
        question: "合成题目",
        answer: "合成答案",
        onRevealChange: () => undefined,
      };
      const { rerender } = render(<RecallCard {...props} revealed />);
      expect(
        document.querySelector(".slw-recall")?.getAttribute("data-motion"),
      ).toBe("light");
      rerender(<RecallCard {...props} revealed={false} />);
      const exiting = document.querySelector(".slw-recall-answer");
      expect(exiting).not.toBeNull();
      expect(exiting?.hasAttribute("inert")).toBe(true);
      expect(exiting?.getAttribute("aria-hidden")).toBe("true");
      expect(screen.queryByRole("region", { name: "参考答案" })).toBeNull();
      act(() => {
        vi.advanceTimersByTime(1000);
      });
    } finally {
      vi.useRealTimers();
    }
  });
});
