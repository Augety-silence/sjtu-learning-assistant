// @vitest-environment jsdom

import { useState } from "react";
import { afterEach, describe, expect, it } from "vitest";
import { selectionIndicatorDuration } from "@/components/ui/SelectionIndicator";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/Tabs";
import { cleanup, fireEvent, render, screen } from "./render";

afterEach(cleanup);

function Harness({ indicator }: { indicator: boolean }) {
  const [value, setValue] = useState("a");
  return (
    <>
      <Tabs value={value} onValueChange={setValue}>
        <TabsList indicator={indicator} aria-label="first">
          <TabsTrigger value="a">甲</TabsTrigger>
          <TabsTrigger value="b">乙</TabsTrigger>
        </TabsList>
      </Tabs>
      <Tabs defaultValue="x">
        <TabsList indicator={indicator} aria-label="second">
          <TabsTrigger value="x">丙</TabsTrigger>
          <TabsTrigger value="y">丁</TabsTrigger>
        </TabsList>
      </Tabs>
    </>
  );
}

const indicators = () =>
  Array.from(document.querySelectorAll("[data-selection-indicator]"));

describe("Tabs sliding indicator", () => {
  it("按实际移动距离缩放时长，并限制在 120 到 280 毫秒", () => {
    expect(selectionIndicatorDuration(0)).toBe(0.12);
    expect(selectionIndicatorDuration(48)).toBeCloseTo(0.14);
    expect(selectionIndicatorDuration(72)).toBeCloseTo(0.15);
    expect(selectionIndicatorDuration(336)).toBeCloseTo(0.26);
    expect(selectionIndicatorDuration(384)).toBe(0.28);
    expect(selectionIndicatorDuration(1_000)).toBe(0.28);
    expect(selectionIndicatorDuration(Number.NaN)).toBe(0.12);
  });

  it("默认不渲染指示器（成绩、批改详情等保持原样）", () => {
    render(<Harness indicator={false} />);
    expect(indicators()).toHaveLength(0);
  });

  it("每组只有一个底层矩形，文字与点击目标始终在其上方", () => {
    render(<Harness indicator />);
    expect(indicators()).toHaveLength(2);
    const first = screen.getByRole("tablist", { name: "first" });
    expect(indicators()[0].parentElement).toBe(first);
    expect(indicators()[0].classList.contains("tabs-group-indicator")).toBe(
      true,
    );
    expect(first.firstElementChild).toBe(indicators()[0]);
    for (const tab of screen.getAllByRole("tab")) {
      expect(tab.classList.contains("tabs-sliding-trigger")).toBe(true);
      expect(tab.querySelector("[data-selection-indicator]")).toBeNull();
      expect(tab.querySelector(".selection-label")).not.toBeNull();
    }

    fireEvent.mouseDown(screen.getByRole("tab", { name: "乙" }), { button: 0 });
    expect(
      screen.getByRole("tab", { name: "乙" }).getAttribute("data-state"),
    ).toBe("active");

    fireEvent.mouseDown(screen.getByRole("tab", { name: "丁" }), { button: 0 });
    expect(indicators()).toHaveLength(2);
    expect(
      screen.getByRole("tab", { name: "丁" }).getAttribute("data-state"),
    ).toBe("active");
    expect(indicators()[1].parentElement).toBe(
      screen.getByRole("tablist", { name: "second" }),
    );
    expect(indicators()[1].getAttribute("aria-hidden")).toBe("true");
  });
});
