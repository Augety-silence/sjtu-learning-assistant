import { useEffect, useState } from "react";
import { Button } from "@/components/ui/Button";
import { invoke } from "@/lib/api";
import type { AppInstallationStatus } from "@/lib/types";

const active = new Set([
  "downloading",
  "verifying",
  "preparing",
  "waiting",
  "installing",
]);
export function AppUpdateStatus({
  initial,
}: {
  initial?: AppInstallationStatus;
}) {
  const [state, setState] = useState(initial);
  const [error, setError] = useState("");
  const [cancelling, setCancelling] = useState(false);
  useEffect(() => {
    if (initial) setState(initial);
  }, [initial]);
  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const next = await invoke<AppInstallationStatus>("update_status", {});
        if (disposed) return;
        setState(next);
        if (active.has(next.phase)) timer = setTimeout(() => void poll(), 1000);
      } catch {
        /* 旧桥接或开发环境仍可检查版本。 */
      }
    };
    void poll();
    return () => {
      disposed = true;
      clearTimeout(timer);
    };
  }, [initial]);
  const cancel = async () => {
    setCancelling(true);
    setError("");
    try {
      setState(await invoke<AppInstallationStatus>("update_cancel", {}));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "取消更新失败");
    } finally {
      setCancelling(false);
    }
  };
  if (!state || state.phase === "idle") return null;
  const downloading = state.phase === "downloading";
  return (
    <div className="app-update-status" role="status" aria-live="polite">
      <p>{error || state.message}</p>
      {downloading && (
        <>
          <progress
            aria-label="更新下载进度"
            max={state.total || 1}
            value={state.received || 0}
          />
          <span>
            {Math.min(
              100,
              Math.round(((state.received || 0) / (state.total || 1)) * 100),
            )}
            % · 下载完成后将自动安装并重启
          </span>
        </>
      )}
      {["downloading", "verifying", "preparing"].includes(state.phase) && (
        <div>
          <Button
            variant="outline"
            disabled={cancelling}
            onClick={() => void cancel()}
          >
            {cancelling ? "正在取消…" : "取消更新"}
          </Button>
        </div>
      )}
    </div>
  );
}
