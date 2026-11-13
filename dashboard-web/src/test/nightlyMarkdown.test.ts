import { afterEach, describe, expect, it, vi } from "vitest";
import { safeRemarkPlugins } from "@/components/AIChatMessageBubble";

afterEach(() => vi.restoreAllMocks());

describe("旧 WebKit Markdown 兼容性", () => {
  it("支持 lookbehind 时保留 GFM", () => {
    expect(safeRemarkPlugins()).toHaveLength(1);
  });

  it("不支持 lookbehind 时安全退回 CommonMark", () => {
    vi.spyOn(globalThis, "RegExp").mockImplementation(() => {
      throw new SyntaxError("旧 WebKit 探针");
    });
    expect(safeRemarkPlugins()).toEqual([]);
  });
});
