import { useEffect, useRef, useState } from "react";
import type { CourseVideoItem, SlidesPdfResult } from "@/components/VideosView";
import { createBoundedReadPool } from "@/lib/boundedReadPool";

/** Quiet, bounded, selected-video sync. Backend persists validated page checkpoints. */
export function useVideoPdfSync(
  video: CourseVideoItem | null,
  enabled: boolean,
  courseId: number | null | undefined,
  create:
    | ((
        video: CourseVideoItem,
      ) => Promise<void | SlidesPdfResult> | void | SlidesPdfResult)
    | undefined,
  retry: number,
) {
  const createRef = useRef(create);
  createRef.current = create;
  const cache = useRef(new Map<string, SlidesPdfResult>());
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  const pending = useRef(new Map<string, Promise<void | SlidesPdfResult>>());
  const lastRetry = useRef(0);
  const [wait] = useState(() => createBoundedReadPool(120000));
  const [result, setResult] = useState<{
    key: string;
    value: SlidesPdfResult | undefined;
  }>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const key = `${courseId ?? "unscoped"}:${video?.id ?? ""}`;
  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    if (retry !== lastRetry.current) cache.current.delete(key);
    lastRetry.current = retry;
    let timer: number | undefined;
    setResult({ key, value: cache.current.get(key) });
    setBusy(false);
    setError("");
    if (
      !enabled ||
      video?.source !== "video_space" ||
      !video.supportsSlidesPdf ||
      !createRef.current
    )
      return;
    if (cache.current.has(key)) return;
    let attempts = 0;
    const sync = async () => {
      setBusy(true);
      const action = createRef.current;
      if (!action) return;
      let request = pending.current.get(key);
      if (!request) {
        request = queue.current
          .catch(() => {})
          .then(() => {
            return action(video);
          });
        pending.current.set(key, request);
        queue.current = request;
        const raw = request;
        void raw.then(
          (completed) => {
            if (completed) cache.current.set(key, completed);
            if (pending.current.get(key) === raw) pending.current.delete(key);
          },
          () => {
            if (pending.current.get(key) === raw) pending.current.delete(key);
          },
        );
      }
      try {
        const raw = request;
        const completed = await wait(
          key,
          () => raw,
          controller.signal,
          "课件同步等待超时；已下载页面保留，可稍后重试同步。",
        );
        if (completed) cache.current.set(key, completed);
        if (active) {
          setResult({ key, value: completed || undefined });
          setError("");
        }
      } catch (reason) {
        if (active) {
          setError(
            reason instanceof Error ? reason.message : "课件同步暂未完成。",
          );
          if (++attempts < 3)
            timer = window.setTimeout(() => void sync(), 30000);
        }
      } finally {
        if (active) setBusy(false);
      }
    };
    void sync();
    return () => {
      active = false;
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [enabled, key, retry, Boolean(create), video?.supportsSlidesPdf]);
  return {
    result: result?.key === key ? result.value : undefined,
    busy,
    error,
  };
}
