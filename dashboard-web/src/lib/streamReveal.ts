import { useEffect, useLayoutEffect, useRef, useState } from "react";

/** 新出现文字从透明到不透明的时长（毫秒）。 */
export const STREAM_FADE_MS = 420;
/** 显示节奏：约 30 帧/秒更新一次，避免每帧重新解析 Markdown。 */
const REVEAL_FRAME_MS = 33;
/** 没有积压时的基础速度（字/秒）；积压按指数追赶，约 1 秒内追上任意积压。 */
const BASE_CHARS_PER_SECOND = 48;
const BACKLOG_CATCH_UP_PER_SECOND = 3;

/**
 * 下一帧应显示到第几个字符。积压越多步子越大，最终不落后于网络到达的文字；
 * 不切开代理对（emoji 等由两个 UTF-16 单元组成）。
 */
export function nextRevealLength(
  text: string,
  shown: number,
  elapsedMs: number,
): number {
  const goal = text.length;
  if (shown >= goal) return goal;
  const backlog = goal - shown;
  const speed = BASE_CHARS_PER_SECOND + backlog * BACKLOG_CATCH_UP_PER_SECOND;
  const step = Math.max(
    1,
    Math.round((speed * Math.min(elapsedMs, 120)) / 1000),
  );
  let next = Math.min(goal, shown + step);
  const code = text.charCodeAt(next - 1);
  if (code >= 0xd800 && code <= 0xdbff && next < goal) next += 1;
  return next;
}

/**
 * 流式输出时按平滑节奏逐步显示文字；非流式（或用户偏好减少动态效果）时直接显示全文。
 * 流式结束后由最终消息整体替换，剩余文字会一次性全部显现。
 */
export function useStreamReveal(
  text: string,
  active: boolean,
  reduceMotion: boolean,
): string {
  const animate = active && !reduceMotion;
  const [shown, setShown] = useState(() => (animate ? 0 : text.length));
  const textRef = useRef(text);
  textRef.current = text;

  useEffect(() => {
    if (!animate) return;
    let frame = 0;
    // 只用 rAF 回调给出的时间戳计时：不同环境中 performance.now() 与回调时间基准可能不同。
    let last: number | null = null;
    const tick = (now: number) => {
      if (last === null) last = now - REVEAL_FRAME_MS;
      const elapsed = now - last;
      if (elapsed >= REVEAL_FRAME_MS || elapsed < 0) {
        last = now;
        setShown((current) =>
          nextRevealLength(
            textRef.current,
            current,
            elapsed < 0 ? REVEAL_FRAME_MS : elapsed,
          ),
        );
      }
      frame = window.requestAnimationFrame(tick);
    };
    frame = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(frame);
  }, [animate]);

  if (!animate) return text;
  return text.slice(0, Math.min(shown, text.length));
}

export interface FadeSegment {
  start: number;
  end: number;
  at: number;
}

interface FadeOptions {
  segments: FadeSegment[];
  /** 已记录的渲染文字长度；超出部分是本次新出现的文字，从透明开始渐显。 */
  stableUntil: number;
  now: number;
  meter: { total: number };
}

interface HastNode {
  type: string;
  tagName?: string;
  value?: string;
  properties?: Record<string, unknown>;
  children?: HastNode[];
}

function fadeSpan(value: string, ageMs: number): HastNode {
  return {
    type: "element",
    tagName: "span",
    properties: {
      className: ["ai-stream-fresh"],
      // 负的延迟让同一段文字在多次重新渲染之间继续沿用同一条动画进度。
      style: `animation-delay:-${Math.max(0, Math.round(ageMs))}ms`,
    },
    children: [{ type: "text", value }],
  };
}

function splitText(
  node: HastNode,
  start: number,
  options: FadeOptions,
): HastNode[] {
  const value = node.value ?? "";
  const end = start + value.length;
  const ranges: Array<{ start: number; end: number; age: number }> = [];
  for (const segment of options.segments) {
    if (segment.end > start && segment.start < end) {
      ranges.push({
        start: Math.max(segment.start, start),
        end: Math.min(segment.end, end),
        age: options.now - segment.at,
      });
    }
  }
  if (options.stableUntil < end) {
    ranges.push({ start: Math.max(options.stableUntil, start), end, age: 0 });
  }
  if (ranges.length === 0) return [node];
  ranges.sort((a, b) => a.start - b.start);
  const parts: HastNode[] = [];
  let cursor = start;
  for (const range of ranges) {
    const from = Math.max(range.start, cursor);
    if (from >= range.end) continue;
    if (from > cursor) {
      parts.push({
        type: "text",
        value: value.slice(cursor - start, from - start),
      });
    }
    const piece = value.slice(from - start, range.end - start);
    // 纯空白不需要动画，也避免在块之间插入多余的行内元素。
    parts.push(
      piece.trim()
        ? fadeSpan(piece, range.age)
        : { type: "text", value: piece },
    );
    cursor = range.end;
  }
  if (cursor < end)
    parts.push({ type: "text", value: value.slice(cursor - start) });
  return parts;
}

/**
 * rehype 插件：按“渲染后文字”的偏移，把最近出现的文字包进渐显 span。
 * 代码块内部不改动（代码行号组件需要原始字符串）。必须放在 rehype-sanitize 之后。
 */
export function rehypeStreamFade(options: FadeOptions) {
  return (tree: HastNode) => {
    let offset = 0;
    const walk = (node: HastNode, inCode: boolean) => {
      if (!node.children) return;
      const next: HastNode[] = [];
      for (const child of node.children) {
        if (child.type === "text") {
          const start = offset;
          offset += (child.value ?? "").length;
          if (inCode) next.push(child);
          else next.push(...splitText(child, start, options));
          continue;
        }
        if (child.type === "element") {
          walk(
            child,
            inCode || child.tagName === "code" || child.tagName === "pre",
          );
        }
        next.push(child);
      }
      node.children = next;
    };
    walk(tree, false);
    options.meter.total = offset;
  };
}

/**
 * 为流式 Markdown 维护渐显片段。返回传给 rehypeStreamFade 的参数；
 * 渲染提交后记录本次新出现的文字区间，下次渲染继续其动画进度。
 */
export function useStreamFade(active: boolean): FadeOptions | null {
  const segmentsRef = useRef<FadeSegment[]>([]);
  const stableRef = useRef(0);
  const meterRef = useRef({ total: 0 });
  const renderAtRef = useRef(0);
  const now =
    typeof performance !== "undefined" ? performance.now() : Date.now();
  renderAtRef.current = now;
  segmentsRef.current = segmentsRef.current.filter(
    (segment) => now - segment.at < STREAM_FADE_MS,
  );

  useLayoutEffect(() => {
    if (!active) {
      segmentsRef.current = [];
      stableRef.current = 0;
      return;
    }
    const total = meterRef.current.total;
    if (total > stableRef.current) {
      segmentsRef.current.push({
        start: stableRef.current,
        end: total,
        at: renderAtRef.current,
      });
    }
    // Markdown 结构变化（如 “**粗” 闭合成粗体）可能让渲染长度变短，同步回退。
    stableRef.current = total;
  });

  if (!active) return null;
  return {
    segments: segmentsRef.current,
    stableUntil: stableRef.current,
    now,
    meter: meterRef.current,
  };
}
