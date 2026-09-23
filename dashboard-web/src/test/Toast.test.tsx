// @vitest-environment jsdom

import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { useEffect } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { type ToastInput, ToastProvider, useToast } from "@/components/Toast";

function Trigger({ toast }: { toast: ToastInput }) {
  const { showToast } = useToast();
  return (
    <button type="button" onClick={() => showToast(toast)}>
      触发通知
    </button>
  );
}

function AutoToast({ toast }: { toast: ToastInput }) {
  const { showToast } = useToast();
  useEffect(() => {
    showToast(toast);
  }, [showToast, toast]);
  return null;
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("ToastProvider", () => {
  it("错误通知默认持久，并支持可执行 action", () => {
    vi.useFakeTimers();
    const retry = vi.fn();
    render(
      <ToastProvider>
        <Trigger
          toast={{
            kind: "error",
            message: "同步失败",
            action: { label: "重试", onClick: retry },
          }}
        />
      </ToastProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: "触发通知" }));
    vi.advanceTimersByTime(60_000);
    expect(screen.getByRole("alert").textContent).toContain("同步失败");
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(retry).toHaveBeenCalledOnce();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("同 id 更新会替换内容和 timer，不会产生重复通知", () => {
    vi.useFakeTimers();
    function UpdateToast() {
      const { showToast } = useToast();
      return (
        <button
          type="button"
          onClick={() => {
            showToast({
              id: "job",
              kind: "info",
              message: "处理中",
              duration: 500,
            });
            showToast({
              id: "job",
              kind: "success",
              message: "已完成",
              duration: 1000,
            });
          }}
        >
          更新
        </button>
      );
    }
    render(
      <ToastProvider>
        <UpdateToast />
      </ToastProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: "更新" }));
    expect(screen.getAllByRole("status")).toHaveLength(1);
    expect(screen.getByRole("status").textContent).toContain("已完成");
    act(() => vi.advanceTimersByTime(500));
    expect(screen.getByRole("status")).toBeTruthy();
    act(() => vi.advanceTimersByTime(500));
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("卸载 Provider 时清理未完成 timer", () => {
    vi.useFakeTimers();
    const clearTimeoutSpy = vi.spyOn(window, "clearTimeout");
    const toast = {
      kind: "info",
      message: "稍后关闭",
      duration: 5000,
    } as const;
    const { unmount } = render(
      <ToastProvider>
        <AutoToast toast={toast} />
      </ToastProvider>,
    );

    expect(vi.getTimerCount()).toBe(1);
    unmount();
    expect(clearTimeoutSpy).toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });
});
