import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createBoundedReadPool } from "@/lib/boundedReadPool";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
const signal = () => new AbortController().signal;
const outcome = <T>(promise: Promise<T>) =>
  promise.then(
    (value) => value,
    (reason: Error) => reason.message,
  );
beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(0);
});
afterEach(() => vi.useRealTimers());

describe("bounded read pool", () => {
  it("merges fresh same-key reads and allows different keys independently", async () => {
    const pool = createBoundedReadPool();
    const shared = deferred<string>();
    const read = vi.fn(() => shared.promise);
    const first = pool("a", read, signal(), "甲超时");
    const second = pool("a", read, signal(), "乙超时");
    const other = pool("b", async () => "另一资源", signal(), "丙超时");
    await Promise.resolve();
    expect(read).toHaveBeenCalledOnce();
    shared.resolve("共享结果");
    expect(await Promise.all([first, second, other])).toEqual([
      "共享结果",
      "共享结果",
      "另一资源",
    ]);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("aborts only one waiter without canceling the underlying read or other waiters", async () => {
    const pool = createBoundedReadPool();
    const pending = deferred<string>();
    const read = vi.fn(() => pending.promise);
    const controller = new AbortController();
    const aborted = outcome(pool("a", read, controller.signal, "甲超时"));
    const remaining = pool("a", read, signal(), "乙超时");
    controller.abort();
    expect(await aborted).toBe("读取已取消。");
    expect(vi.getTimerCount()).toBe(1);
    pending.resolve("仍完成");
    expect(await remaining).toBe("仍完成");
    expect(read).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("does not start a read for an already aborted waiter", async () => {
    const controller = new AbortController();
    controller.abort();
    const read = vi.fn(async () => "不应调用");
    expect(
      await outcome(
        createBoundedReadPool()("a", read, controller.signal, "超时"),
      ),
    ).toBe("读取已取消。");
    expect(read).not.toHaveBeenCalled();
  });

  it("preserves independent waiter deadlines and timeout messages on a shared read", async () => {
    const pool = createBoundedReadPool();
    const pending = deferred<string>();
    const read = vi.fn(() => pending.promise);
    const first = outcome(pool("a", read, signal(), "甲读取超时"));
    await vi.advanceTimersByTimeAsync(15_000);
    const second = outcome(pool("a", read, signal(), "乙读取超时"));
    await vi.advanceTimersByTimeAsync(15_000);
    expect(await first).toBe("甲读取超时");
    expect(vi.getTimerCount()).toBe(1);
    await vi.advanceTimersByTimeAsync(15_000);
    expect(await second).toBe("乙读取超时");
    expect(read).toHaveBeenCalledOnce();
  });

  it.each(["resolve", "reject"] as const)(
    "replaces an expired read once and a late old %s cannot delete the new entry",
    async (settlement) => {
      const pool = createBoundedReadPool();
      const old = deferred<string>();
      const fresh = deferred<string>();
      const read = vi
        .fn()
        .mockReturnValueOnce(old.promise)
        .mockReturnValueOnce(fresh.promise);
      const first = outcome(pool("a", read, signal(), "首次超时"));
      await vi.advanceTimersByTimeAsync(30_000);
      expect(await first).toBe("首次超时");
      const retry = pool<string>("a", read, signal(), "重试超时");
      await Promise.resolve();
      expect(read).toHaveBeenCalledTimes(2);
      if (settlement === "resolve") old.resolve("旧结果");
      else old.reject(new Error("旧读取迟到失败"));
      await vi.advanceTimersByTimeAsync(0);
      const joined = pool<string>("a", read, signal(), "跟随超时");
      fresh.resolve("新结果");
      expect(await Promise.all([retry, joined])).toEqual(["新结果", "新结果"]);
      expect(read).toHaveBeenCalledTimes(2);
    },
  );

  it("bounds stuck reads at two, shares a fresh replacement and explains recovery after both expire", async () => {
    const pool = createBoundedReadPool();
    const read = vi.fn(() => new Promise<string>(() => undefined));
    const first = outcome(pool("a", read, signal(), "第一次超时"));
    await vi.advanceTimersByTimeAsync(30_000);
    expect(await first).toBe("第一次超时");
    const retry = outcome(pool("a", read, signal(), "第二次超时"));
    const joined = outcome(pool("a", read, signal(), "合并等待超时"));
    await vi.advanceTimersByTimeAsync(30_000);
    expect(await retry).toBe("第二次超时");
    expect(await joined).toBe("合并等待超时");
    for (let index = 0; index < 3; index++)
      expect(await outcome(pool("a", read, signal(), "再试超时"))).toContain(
        "重新打开应用",
      );
    expect(read).toHaveBeenCalledTimes(2);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("reclaims capacity when a replacement finishes while the original remains stuck", async () => {
    const pool = createBoundedReadPool();
    const read = vi
      .fn()
      .mockImplementationOnce(() => new Promise<string>(() => undefined))
      .mockResolvedValueOnce("恢复结果")
      .mockResolvedValueOnce("下次结果");
    const original = outcome(pool("a", read, signal(), "首次超时"));
    await vi.advanceTimersByTimeAsync(30_000);
    expect(await original).toBe("首次超时");
    expect(await pool("a", read, signal(), "恢复超时")).toBe("恢复结果");
    expect(await pool("a", read, signal(), "下次超时")).toBe("下次结果");
    expect(read).toHaveBeenCalledTimes(3);
  });

  it("does not cache a rejected or fulfilled request and removes waiter listeners", async () => {
    const pool = createBoundedReadPool();
    const controller = new AbortController();
    const remove = vi.spyOn(controller.signal, "removeEventListener");
    const read = vi
      .fn()
      .mockRejectedValueOnce(new Error("服务失败"))
      .mockResolvedValueOnce("重新读取成功");
    expect(await outcome(pool("a", read, controller.signal, "超时"))).toBe(
      "服务失败",
    );
    expect(await pool("a", read, controller.signal, "超时")).toBe(
      "重新读取成功",
    );
    expect(read).toHaveBeenCalledTimes(2);
    expect(remove).toHaveBeenCalledTimes(2);
    expect(vi.getTimerCount()).toBe(0);
  });
});
