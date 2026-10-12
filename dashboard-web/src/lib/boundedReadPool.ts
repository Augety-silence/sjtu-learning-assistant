export type BoundedReadRequest = <T>(
  key: string,
  read: () => Promise<T>,
  signal: AbortSignal,
  timeoutMessage: string,
) => Promise<T>;

type Entry = { startedAt: number; promise: Promise<unknown> };
type KeyReads = { current?: Entry; pending: Set<Entry> };

/** Shares in-flight reads without canceling bridge work. Each waiter owns its timeout and abort. */
export function createBoundedReadPool(waitMs = 30_000): BoundedReadRequest {
  if (!Number.isFinite(waitMs) || waitMs <= 0)
    throw new Error("读取等待时间必须为正数。");
  const reads = new Map<string, KeyReads>();

  return <T>(
    key: string,
    read: () => Promise<T>,
    signal: AbortSignal,
    timeoutMessage: string,
  ): Promise<T> => {
    if (signal.aborted) return Promise.reject(new Error("读取已取消。"));
    let state = reads.get(key);
    if (!state) {
      state = { pending: new Set() };
      reads.set(key, state);
    }
    let entry = state.current;
    if (!entry || Date.now() - entry.startedAt >= waitMs) {
      // An uncancelable old read may still finish. Never stack a third read for this key.
      if (state.pending.size >= 2)
        return Promise.reject(
          new Error("同一资源的两次读取均未响应，请重新打开应用后重试。"),
        );
      entry = { startedAt: Date.now(), promise: Promise.resolve().then(read) };
      state.current = entry;
      state.pending.add(entry);
      const trackedState = state;
      const trackedEntry = entry;
      const release = () => {
        trackedState.pending.delete(trackedEntry);
        if (trackedState.current === trackedEntry)
          trackedState.current = undefined;
        if (!trackedState.pending.size && reads.get(key) === trackedState)
          reads.delete(key);
      };
      void entry.promise.then(release, release);
    }

    const request = entry.promise;
    return new Promise<T>((resolve, reject) => {
      let settled = false;
      const cleanup = () => {
        clearTimeout(timer);
        signal.removeEventListener("abort", abort);
      };
      const finish = (action: () => void) => {
        if (settled) return;
        settled = true;
        cleanup();
        action();
      };
      const abort = () => finish(() => reject(new Error("读取已取消。")));
      const timer = setTimeout(
        () => finish(() => reject(new Error(timeoutMessage))),
        waitMs,
      );
      signal.addEventListener("abort", abort, { once: true });
      if (signal.aborted) abort();
      request.then(
        (value) => finish(() => resolve(value as T)),
        (reason: unknown) => finish(() => reject(reason)),
      );
    });
  };
}
