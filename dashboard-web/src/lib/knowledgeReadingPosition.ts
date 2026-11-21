import { useCallback, useLayoutEffect, useRef } from "react";
import type { CompiledKnowledgePage } from "@/lib/types";

const PREFIX = "sjtu-knowledge-reading-v1:";
type Position = { version: string; top: number; offset: number };

export function savedKnowledgePosition(
  id: string,
  version: string,
): Position | null {
  try {
    const value = JSON.parse(localStorage.getItem(PREFIX + id) ?? "null");
    if (
      value?.version === version &&
      Number.isFinite(value.top) &&
      value.top >= 0 &&
      value.top <= 10_000_000 &&
      Number.isSafeInteger(value.offset) &&
      value.offset >= 0 &&
      value.offset <= 6_000_000
    )
      return value;
  } catch {
    /* 存储不可用时仍允许正常阅读。 */
  }
  return null;
}

export function useKnowledgeReadingPosition(
  page: CompiledKnowledgePage | null,
) {
  const ref = useRef<HTMLDivElement>(null);
  const active = useRef<{ id: string; position: Position } | null>(null);
  const save = useCallback(() => {
    if (!active.current) return;
    try {
      localStorage.setItem(
        PREFIX + active.current.id,
        JSON.stringify(active.current.position),
      );
    } catch {
      /* 隐私模式／配额耗尽不影响阅读。 */
    }
  }, []);
  const onScroll = useCallback(() => {
    if (!ref.current || !active.current) return;
    active.current.position.top = ref.current.scrollTop;
    save();
  }, [save]);
  // 切换前主动快照，不依赖程序性滚动是否派发 scroll 事件。
  const capture = useCallback(() => {
    if (ref.current && active.current)
      active.current.position.top = ref.current.scrollTop;
    save();
  }, [save]);

  useLayoutEffect(() => {
    if (!page) {
      save();
      active.current = null;
      return;
    }
    const version = page.content_sha256 ?? "";
    if (
      active.current?.id === page.id &&
      active.current.position.version === version &&
      active.current.position.offset === page.offset
    )
      return;
    save();
    const saved = savedKnowledgePosition(page.id, version);
    const top = saved?.offset === page.offset ? saved.top : 0;
    active.current = {
      id: page.id,
      position: { version, offset: page.offset, top },
    };
    if (ref.current) ref.current.scrollTop = top;
  }, [page, save]);

  useLayoutEffect(() => {
    window.addEventListener("pagehide", capture);
    return () => {
      capture();
      window.removeEventListener("pagehide", capture);
    };
  }, [capture]);

  const restart = useCallback(() => {
    if (active.current) active.current.position.top = 0;
    if (ref.current) ref.current.scrollTop = 0;
    save();
  }, [save]);
  return { ref, onScroll, restart, capture };
}
