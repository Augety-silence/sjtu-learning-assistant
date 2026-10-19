// @vitest-environment jsdom

import { useState } from "react";
import { afterEach, describe, expect, it } from "vitest";
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
  it("默认不渲染指示器（成绩、批改详情等保持原样）", () => {
    render(<Harness indicator={false} />);
    expect(indicators()).toHaveLength(0);
  });

  it("每个分组只有一个矩形，跟随受控与非受控的选中项移动", () => {
    render(<Harness indicator />);
    expect(indicators()).toHaveLength(2);
    expect(
      screen.getByRole("tab", { name: "甲" }).contains(indicators()[0]),
    ).toBe(true);

    fireEvent.mouseDown(screen.getByRole("tab", { name: "乙" }), { button: 0 });
    expect(
      screen.getByRole("tab", { name: "乙" }).contains(indicators()[0]),
    ).toBe(true);

    fireEvent.mouseDown(screen.getByRole("tab", { name: "丁" }), { button: 0 });
    expect(indicators()).toHaveLength(2);
    expect(
      screen.getByRole("tab", { name: "丁" }).contains(indicators()[1]),
    ).toBe(true);
    expect(indicators()[1].getAttribute("aria-hidden")).toBe("true");
  });
});
