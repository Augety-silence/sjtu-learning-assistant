// @vitest-environment jsdom

import { act, renderHook } from "@testing-library/react";
import rehypeSanitize from "rehype-sanitize";
import remarkParse from "remark-parse";
import remarkRehype from "remark-rehype";
import { unified } from "unified";
import { afterEach, describe, expect, it, vi } from "vitest";
import { liveStepText } from "@/components/AIExecutionCard";
import {
  nextRevealLength,
  rehypeStreamFade,
  useStreamReveal,
} from "@/lib/streamReveal";

afterEach(() => {
  vi.useRealTimers();
});

describe("smooth streaming reveal", () => {
  it("advances by readable chunks and speeds up with backlog", () => {
    const text = "甲".repeat(400);
    const calm = nextRevealLength(text.slice(0, 10), 0, 33);
    expect(calm).toBeGreaterThan(1);
    expect(calm).toBeLessThanOrEqual(10);
    // 积压 400 字时一帧前进远多于积压 10 字时，约 1 秒（≈30 帧）内追上。
    const behind = nextRevealLength(text, 0, 33);
    expect(behind).toBeGreaterThan(20);
    let shown = 0;
    let frames = 0;
    while (shown < text.length && frames < 100) {
      shown = nextRevealLength(text, shown, 33);
      frames += 1;
    }
    expect(frames).toBeLessThanOrEqual(32);
    expect(nextRevealLength(text, text.length, 33)).toBe(text.length);
  });

  it("never splits a surrogate pair", () => {
    const text = `ab${"😀".repeat(5)}`;
    for (let shown = 0; shown < text.length; ) {
      shown = nextRevealLength(text, shown, 16);
      const code = text.charCodeAt(shown - 1);
      expect(code >= 0xd800 && code <= 0xdbff).toBe(false);
    }
  });

  it("reveals progressively while streaming and shows everything once finished", () => {
    vi.useFakeTimers({
      toFake: ["requestAnimationFrame", "cancelAnimationFrame", "performance"],
    });
    const { result, rerender } = renderHook(
      ({ text, active }) => useStreamReveal(text, active, false),
      { initialProps: { text: "第一段回答内容。".repeat(10), active: true } },
    );
    expect(result.current).toBe("");
    act(() => {
      vi.advanceTimersByTime(200);
    });
    const partial = result.current.length;
    expect(partial).toBeGreaterThan(0);
    expect(partial).toBeLessThan(80);
    // 流式结束（最终消息替换）后余下文字一次全部显现。
    rerender({ text: "第一段回答内容。".repeat(10), active: false });
    expect(result.current).toHaveLength(80);
  });

  it("shows text immediately when reduced motion is requested", () => {
    const { result } = renderHook(() =>
      useStreamReveal("全部文字", true, true),
    );
    expect(result.current).toBe("全部文字");
  });
});

describe("stream fade rehype plugin", () => {
  type Node = {
    type: string;
    tagName?: string;
    value?: string;
    properties?: { className?: string[] };
    children?: Node[];
  };
  const render = (markdown: string, stableUntil: number) => {
    const meter = { total: 0 };
    const processor = unified()
      .use(remarkParse)
      .use(remarkRehype)
      .use(rehypeSanitize)
      .use(rehypeStreamFade, { segments: [], stableUntil, now: 0, meter });
    const tree = processor.runSync(processor.parse(markdown)) as Node;
    const fresh: string[] = [];
    const code: string[] = [];
    const strong: string[] = [];
    const text = (node: Node): string =>
      node.type === "text"
        ? (node.value ?? "")
        : (node.children ?? []).map(text).join("");
    const walk = (node: Node) => {
      if (
        node.type === "element" &&
        node.properties?.className?.includes("ai-stream-fresh")
      ) {
        fresh.push(text(node));
      }
      if (node.tagName === "code") code.push(text(node));
      if (node.tagName === "strong") strong.push(text(node));
      for (const child of node.children ?? []) walk(child);
    };
    walk(tree);
    return { fresh, code, strong, total: meter.total };
  };

  it("wraps only newly rendered text and leaves code untouched", () => {
    const result = render(
      "已显示的**重点**和新文字\n\n```\nconst a = 1\n```",
      6,
    );
    expect(result.total).toBeGreaterThan(6);
    expect(result.strong).toEqual(["重点"]);
    expect(result.fresh.join("")).toBe("和新文字");
    expect(result.code).toEqual(["const a = 1\n"]);
    expect(render("全部稳定", 100).fresh).toEqual([]);
  });
});

describe("live step text", () => {
  it("explains waiting, slow responses and automatic retries", () => {
    expect(liveStepText({ streaming: true, waitedMs: 0, hasTools: true })).toBe(
      "正在生成回答…",
    );
    expect(
      liveStepText({ streaming: false, waitedMs: null, hasTools: true }),
    ).toBe("正在规划下一步…");
    const call = {
      state: "model_call" as const,
      step: 2,
      attempt: 1,
      received_at: 0,
    };
    expect(
      liveStepText({
        streaming: false,
        status: call,
        waitedMs: 2000,
        hasTools: true,
      }),
    ).toBe("正在请求模型（第 2 步）…");
    expect(
      liveStepText({
        streaming: false,
        status: call,
        waitedMs: 12_000,
        hasTools: true,
      }),
    ).toBe("正在等待模型响应（第 2 步） · 已等待 12 秒");
    expect(
      liveStepText({
        streaming: false,
        status: call,
        waitedMs: 47_000,
        hasTools: true,
      }),
    ).toContain("响应较慢，超时会自动重试");
    const retry = {
      state: "retrying" as const,
      attempt: 2,
      max_attempts: 3,
      reason: "模型长时间没有响应",
      delay_seconds: 4,
      received_at: 0,
    };
    expect(
      liveStepText({
        streaming: false,
        status: retry,
        waitedMs: 1000,
        hasTools: false,
      }),
    ).toBe("模型长时间没有响应，3 秒后第 2/3 次请求模型…");
  });
});
