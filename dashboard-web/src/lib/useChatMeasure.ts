import { type CSSProperties, useLayoutEffect, useRef, useState } from "react";

/** 正文和输入框共用滚动区域实际宽度，避免桌面滚动条造成半个 gutter 的偏移。 */
export function useChatMeasure(enabled: boolean, visible: boolean) {
  const conversationRef = useRef<HTMLElement>(null);
  const [style, setStyle] = useState<CSSProperties>({});
  useLayoutEffect(() => {
    if (!enabled || !visible) return;
    const conversation = conversationRef.current;
    const thread = conversation?.querySelector<HTMLElement>(".ai-chat-thread");
    if (!conversation || !thread) return;
    const measure = () => {
      if (!thread.clientWidth) return;
      const inset = window.innerWidth < 1200 ? 40 : 64;
      const width = Math.max(0, Math.min(780, thread.clientWidth - inset));
      const left =
        thread.getBoundingClientRect().left -
        conversation.getBoundingClientRect().left +
        thread.clientLeft +
        (thread.clientWidth - width) / 2;
      const next = {
        "--ai-reading-width": `${width}px`,
        "--ai-reading-left": `${left}px`,
      } as CSSProperties;
      setStyle((previous) =>
        previous["--ai-reading-width" as keyof CSSProperties] ===
          `${width}px` &&
        previous["--ai-reading-left" as keyof CSSProperties] === `${left}px`
          ? previous
          : next,
      );
    };
    measure();
    window.addEventListener("resize", measure);
    const observer =
      typeof ResizeObserver !== "undefined"
        ? new ResizeObserver(measure)
        : null;
    observer?.observe(thread);
    observer?.observe(conversation);
    return () => {
      window.removeEventListener("resize", measure);
      observer?.disconnect();
    };
  }, [enabled, visible]);
  return { conversationRef, readingStyle: enabled ? style : undefined };
}
