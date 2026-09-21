import { describe, expect, it, vi } from "vitest";
import { deadlineDistance, formatDateTime, formatSize } from "@/lib/format";

describe("format helpers", () => {
  it("formats bytes", () => {
    expect(formatSize(1024)).toBe("1.0 KB");
    expect(formatSize(null)).toBe("未知");
  });

  it("formats in Asia/Shanghai", () => {
    expect(formatDateTime("2026-09-21T00:00:00Z")).toContain("8:00");
  });

  it("computes deadline at runtime", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-21T00:00:00Z"));
    expect(deadlineDistance("2026-09-22T00:00:00Z")).toBe("1 天后截止");
    vi.useRealTimers();
  });
});
