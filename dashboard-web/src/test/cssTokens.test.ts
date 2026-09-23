import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("../index.css", import.meta.url), "utf8");

describe("CSS semantic tokens", () => {
  it("定义完整语义色源且不再使用半像素边界", () => {
    for (const token of [
      "--surface-page",
      "--text-primary",
      "--border-default",
      "--action-primary",
      "--status-danger",
      "--focus-ring",
    ]) {
      expect(css).toContain(token);
    }
    expect(css).not.toContain("0.5px");
  });

  it("关键移动热区与紧凑 KPI 使用 599px 断点", () => {
    expect(css).toContain("@media (max-width: 599px)");
    expect(css).toContain("grid-template-columns: repeat(3, minmax(0, 1fr))");
    expect(css).toContain("min-height: 44px");
  });
});
