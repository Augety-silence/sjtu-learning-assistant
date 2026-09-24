// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { EmptyState, ErrorState, LoadingState } from "@/components/States";
import { Button } from "@/components/ui/Button";
import { cleanup, fireEvent, render, screen, waitFor } from "@/test/render";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("shared states", () => {
  it.each([
    ["loading", <LoadingState label="读取中" />],
    ["empty", <EmptyState title="暂无内容" description="稍后再试" />],
    ["error", <ErrorState message="请求失败" />],
  ])("%s 使用克制的状态入场标记", (_name, state) => {
    const { container } = render(state);
    expect(
      container.querySelector('[data-motion-surface="state"]'),
    ).toBeTruthy();
  });

  it("EmptyState 呈现场景 action", () => {
    const clear = vi.fn();
    render(
      <EmptyState
        title="没有匹配结果"
        description="请调整条件"
        action={<Button onClick={clear}>清除筛选</Button>}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "清除筛选" }));
    expect(clear).toHaveBeenCalledOnce();
  });

  it("ErrorState 在异步重试期间 busy 且防重复提交", async () => {
    let resolveRetry: (() => void) | undefined;
    const retry = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          resolveRetry = resolve;
        }),
    );
    render(<ErrorState message="加载失败详情" retry={retry} />);

    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    const button = screen.getByRole("button", { name: "正在重试…" });
    await waitFor(() => expect(button.getAttribute("aria-busy")).toBe("true"));
    expect((button as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(button);
    expect(retry).toHaveBeenCalledOnce();
    resolveRetry?.();
    await waitFor(() =>
      expect((button as HTMLButtonElement).disabled).toBe(false),
    );
  });
});

describe("Button loading", () => {
  it("暴露 aria-busy、禁用点击并保留明确文案", () => {
    const click = vi.fn();
    render(
      <Button loading loadingLabel="保存中…" onClick={click}>
        保存
      </Button>,
    );
    const button = screen.getByRole("button", { name: "保存中…" });
    expect(button.getAttribute("aria-busy")).toBe("true");
    expect((button as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(button);
    expect(click).not.toHaveBeenCalled();
  });
});
