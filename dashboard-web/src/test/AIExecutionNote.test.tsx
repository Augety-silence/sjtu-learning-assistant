// @vitest-environment jsdom
import { act, cleanup, render, screen } from "@testing-library/react";
import { Profiler } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TraceRun } from "@/components/AIExecutionCard";
import { ExecutionNote } from "@/components/AIExecutionNote";

const motion = vi.hoisted(() => ({ reduce: false }));
vi.mock("motion/react", () => ({ useReducedMotion: () => motion.reduce }));
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  motion.reduce = false;
});

describe("execution note rich text", () => {
  it("handles the extra closing-space shown in the screenshot, without changing code", () => {
    const { container } = render(
      <ExecutionNote
        text={
          "**课程进度： **\n\n`**保留代码： **`\n\n```\n**保留代码： **\n```"
        }
      />,
    );
    expect(container.querySelector("strong")?.textContent).toBe("课程进度：");
    expect(
      Array.from(
        container.querySelectorAll("code"),
        (node) => node.textContent,
      ),
    ).toEqual(["**保留代码： **", "**保留代码： **\n"]);
  });
  it("renders bold, lists, tables, links and code without plain Markdown markers", () => {
    const { container } = render(
      <ExecutionNote
        text={
          '**课程进度**\n\n- 已完成 43\n- 待处理 15\n\n| 类型 | 数量 |\n| --- | --- |\n| 课件 | 101 |\n\n```python\nprint("课程")\n```\n\n[出处](https://example.test/course)'
        }
      />,
    );
    expect(container.querySelector("strong")?.textContent).toBe("课程进度");
    expect(container.querySelectorAll("ul li")).toHaveLength(2);
    expect(
      screen.getByRole("region", { name: "表格内容" }).querySelector("td")
        ?.textContent,
    ).toBe("课件");
    expect(container.querySelector("pre code")?.textContent).toContain(
      'print("课程")',
    );
    expect(screen.getByRole("link", { name: "出处" }).getAttribute("rel")).toBe(
      "noreferrer",
    );
    expect(container.textContent).not.toContain("**");
  });

  it("does not load remote images, execute HTML, or expose unsafe navigation", () => {
    const { container } = render(
      <ExecutionNote
        text={
          "![课程](https://example.test/leak)\n\n[文件](file:///private/data)\n\n[危险](javascript:alert%281%29)\n\n<script>alert(1)</script>"
        }
      />,
    );
    expect(container.querySelector("img,script")).toBeNull();
    expect(container.querySelectorAll("a[href]")).toHaveLength(0);
    expect(container.textContent).toContain("[图片：课程]");
  });

  it("reveals at a bounded rate and completes without losing text", () => {
    vi.useFakeTimers({
      toFake: ["requestAnimationFrame", "cancelAnimationFrame", "performance"],
    });
    let commits = 0;
    const text = "正在核对课程正文。".repeat(50);
    const view = render(
      <Profiler
        id="note"
        onRender={() => {
          commits++;
        }}
      >
        <ExecutionNote text={text} streaming />
      </Profiler>,
    );
    expect(view.container.textContent).toBe("");
    act(() => {
      vi.advanceTimersByTime(200);
    });
    expect(view.container.textContent!.length).toBeGreaterThan(0);
    expect(view.container.textContent!.length).toBeLessThan(text.length);
    expect(commits).toBeLessThanOrEqual(5);
    view.rerender(<ExecutionNote text={text} streaming={false} />);
    expect(view.container.textContent).toBe(text);
  });

  it("renders immediately for reduced motion and skips empty notes", () => {
    motion.reduce = true;
    const view = render(<ExecutionNote text="**全部文字**" streaming />);
    expect(view.container.querySelector("strong")?.textContent).toBe(
      "全部文字",
    );
    view.rerender(<ExecutionNote text="  " />);
    expect(view.container.innerHTML).toBe("");
  });

  it("does not reparse a completed tool note on unrelated live updates", () => {
    const run = {
      tool_name: "list_courses",
      phase: "model" as const,
      status: "ok" as const,
      arguments_summary: {},
      result_summary: { count: 8 },
      progress_text: "**已读课程**",
    };
    const { container, rerender } = render(<TraceRun run={run} />);
    const strong = container.querySelector("strong");
    rerender(<TraceRun run={run} />);
    expect(container.querySelector("strong")).toBe(strong);
  });
});
