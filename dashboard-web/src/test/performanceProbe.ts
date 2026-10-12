/**
 * Read-only, local-fixture performance probe. It observes user-driven clicks
 * and hash navigation; it never triggers an interaction or changes app state.
 */

type FrameMetrics = {
  available: boolean;
  meanMs: number | null;
  p95Ms: number | null;
  maxMs: number | null;
  over32ms: number | null;
  frameCount: number;
  unavailableReason?: string;
};

type UiPerformanceRecord = {
  id: number;
  trigger: "click" | "hashchange";
  clickedText: string | null;
  fromHash: string;
  toHash: string;
  locator: string;
  startedAt: string;
  durationMs: number;
  frames: FrameMetrics;
  routeFirstPaintMs: number | null;
  routeFirstPaintAvailable: boolean;
  routeTransitionObserved: boolean;
  animationSettleMs: number | null;
  animationSettleAvailable: boolean;
  activeFiniteAnimationsAtEnd: number | null;
  longTasks: { available: boolean; count: number | null };
  domNodes: number | null;
  visibleBackdropFilterLayers: number | null;
  environment: {
    viewport: { width: number; height: number };
    devicePixelRatio: number;
    userAgent: string;
    visibilityState: DocumentVisibilityState;
    reducedMotion: boolean | null;
  };
  incomplete: boolean;
};

type AuditSnapshot = {
  readonly version: 1;
  readonly available: boolean;
  readonly unavailableReasons: readonly string[];
  readonly records: readonly Readonly<UiPerformanceRecord>[];
};

declare global {
  interface Window {
    readonly __UI_PERFORMANCE_AUDIT__?: AuditSnapshot;
  }
}

const SAMPLE_WINDOW_MS = 450;
const MAX_RECORDS = 200;
const records: UiPerformanceRecord[] = [];
const unavailableReasons: string[] = [];
let recordId = 0;
let lastClick: { clickedText: string; at: number; attached: boolean } | null =
  null;
let unavailableReasonRecorded = false;

function addUnavailableReason(reason: string) {
  if (!unavailableReasons.includes(reason)) unavailableReasons.push(reason);
}

function rounded(value: number) {
  return Math.round(value * 10) / 10;
}

function percentile95(values: number[]) {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  return rounded(sorted[Math.ceil(sorted.length * 0.95) - 1]);
}

function clickLabel(target: EventTarget | null) {
  if (!(target instanceof Element)) return null;
  const control = target.closest<HTMLElement>(
    "nav button, nav a[href], button, a[href], [role='button']",
  );
  const label = (
    control?.innerText ||
    control?.getAttribute("aria-label") ||
    control?.title ||
    control?.textContent ||
    ""
  )
    .replace(/\s+/g, " ")
    .trim();
  return label ? label.slice(0, 120) : null;
}

function environmentSnapshot(): UiPerformanceRecord["environment"] {
  let reducedMotion: boolean | null = null;
  try {
    reducedMotion =
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? null;
  } catch {
    reducedMotion = null;
  }
  return {
    viewport: { width: window.innerWidth, height: window.innerHeight },
    devicePixelRatio: window.devicePixelRatio || 1,
    userAgent: navigator.userAgent,
    visibilityState: document.visibilityState,
    reducedMotion,
  };
}

function routeRoot() {
  return document.querySelector<HTMLElement>(".view-transition, .ai-workspace");
}

function visibleInViewport(element: HTMLElement) {
  const rect = element.getBoundingClientRect();
  if (rect.width <= 0 || rect.height <= 0) return false;
  if (
    rect.right <= 0 ||
    rect.bottom <= 0 ||
    rect.left >= window.innerWidth ||
    rect.top >= window.innerHeight
  ) {
    return false;
  }

  for (
    let current: HTMLElement | null = element;
    current;
    current = current.parentElement
  ) {
    const style = window.getComputedStyle(current);
    if (
      style.display === "none" ||
      style.visibility === "hidden" ||
      style.visibility === "collapse" ||
      Number.parseFloat(style.opacity) === 0
    ) {
      return false;
    }
  }
  return true;
}

function countVisibleBackdropFilters() {
  let count = 0;
  for (const candidate of document.querySelectorAll<HTMLElement>("*")) {
    if (!visibleInViewport(candidate)) continue;
    const style = window.getComputedStyle(candidate);
    const value =
      style.backdropFilter || style.getPropertyValue("-webkit-backdrop-filter");
    if (value && value !== "none") count += 1;
  }
  return count;
}

function finiteRunningAnimations(root: HTMLElement | null) {
  if (!root || typeof root.getAnimations !== "function") return null;
  return root.getAnimations({ subtree: true }).filter((animation) => {
    const timing = animation.effect?.getComputedTiming();
    return (
      timing &&
      Number.isFinite(timing.endTime) &&
      (animation.playState === "running" || animation.pending)
    );
  }).length;
}

function publishSnapshot() {
  if (!Object.getOwnPropertyDescriptor(window, "__UI_PERFORMANCE_AUDIT__")) {
    Object.defineProperty(window, "__UI_PERFORMANCE_AUDIT__", {
      configurable: false,
      enumerable: true,
      get: snapshotForRead,
    });
  }
  let output = document.getElementById("ui-performance-audit");
  if (!(output instanceof HTMLScriptElement)) {
    const script = document.createElement("script");
    script.id = "ui-performance-audit";
    script.type = "application/json";
    (document.body ?? document.documentElement).append(script);
    output = script;
  }
  output.textContent = JSON.stringify(snapshotForRead());
  output.dataset.recordCount = String(recordId);
}

function snapshotForRead(): AuditSnapshot {
  const freeze = <T>(value: T): T => {
    if (value && typeof value === "object" && !Object.isFrozen(value)) {
      Object.freeze(value);
      for (const child of Object.values(value as Record<string, unknown>))
        freeze(child);
    }
    return value;
  };
  return freeze({
    version: 1 as const,
    available: typeof window.requestAnimationFrame === "function",
    unavailableReasons: [...unavailableReasons],
    records: records.map(
      (record) => JSON.parse(JSON.stringify(record)) as UiPerformanceRecord,
    ),
  });
}

function beginSample(
  trigger: "click" | "hashchange",
  clickedText: string | null,
) {
  const start = performance.now();
  const initialHash = window.location.hash;
  const frameIntervals: number[] = [];
  let lastFrameAt = start;
  let firstRouteFrameAt: number | null = null;
  let routeDomReadyAt: number | null = null;
  let animationSettleAt: number | null = null;
  let quietFrames = 0;
  let lastFiniteAnimationCount: number | null = null;
  let ended = false;
  let longTaskCount = 0;
  let longTaskObserver: PerformanceObserver | null = null;
  let longTasksAvailable = false;
  const initialRouteRoot = routeRoot();
  const initialRouteText = initialRouteRoot?.textContent ?? "";
  const startedAt = new Date().toISOString();
  const environment = environmentSnapshot();
  const toHashAtStart = initialHash;
  const frameApiAvailable = typeof window.requestAnimationFrame === "function";
  const animationApiAvailable =
    typeof Element.prototype.getAnimations === "function";

  if (!frameApiAvailable && !unavailableReasonRecorded) {
    addUnavailableReason(
      "requestAnimationFrame 不可用，帧间隔和切换时序无法测量。",
    );
    unavailableReasonRecorded = true;
  }

  try {
    if (typeof PerformanceObserver !== "undefined") {
      if (!PerformanceObserver.supportedEntryTypes?.includes("longtask")) {
        addUnavailableReason("当前浏览器未报告支持 longtask 性能条目。");
      } else {
        longTaskObserver = new PerformanceObserver((list) => {
          for (const entry of list.getEntries()) {
            if (entry.entryType === "longtask") longTaskCount += 1;
          }
        });
        longTaskObserver.observe({ type: "longtask" });
        longTasksAvailable = true;
      }
    } else {
      addUnavailableReason("PerformanceObserver/longtask 不可用。");
    }
  } catch {
    addUnavailableReason("当前浏览器不支持 longtask 性能观察。");
  }

  const finish = (endTime: number) => {
    if (ended) return;
    ended = true;
    longTaskObserver?.disconnect();
    const sorted = [...frameIntervals].sort((a, b) => a - b);
    const mean = sorted.length
      ? rounded(sorted.reduce((sum, value) => sum + value, 0) / sorted.length)
      : null;
    const p95 = percentile95(sorted);
    const max = sorted.length ? rounded(sorted[sorted.length - 1]) : null;
    const finalHash = window.location.hash;
    const record: UiPerformanceRecord = {
      id: ++recordId,
      trigger,
      clickedText,
      fromHash: initialHash,
      toHash: finalHash || toHashAtStart,
      locator: `${clickedText || (trigger === "hashchange" ? "hashchange" : "click")} · ${finalHash || toHashAtStart || "(无 hash)"}`,
      startedAt,
      durationMs: rounded(Math.max(0, endTime - start)),
      frames: {
        available: frameApiAvailable,
        meanMs: mean,
        p95Ms: p95,
        maxMs: max,
        over32ms: sorted.length
          ? frameIntervals.filter((value) => value > 32).length
          : null,
        frameCount: frameIntervals.length,
        ...(!frameApiAvailable
          ? { unavailableReason: "requestAnimationFrame 不可用" }
          : {}),
      },
      routeFirstPaintMs:
        firstRouteFrameAt === null ? null : rounded(firstRouteFrameAt - start),
      routeFirstPaintAvailable: frameApiAvailable,
      routeTransitionObserved: finalHash !== initialHash,
      animationSettleMs:
        animationSettleAt === null ? null : rounded(animationSettleAt - start),
      animationSettleAvailable: animationApiAvailable,
      activeFiniteAnimationsAtEnd: lastFiniteAnimationCount,
      longTasks: {
        available: longTasksAvailable,
        count: longTasksAvailable ? longTaskCount : null,
      },
      domNodes: document.getElementsByTagName("*").length,
      visibleBackdropFilterLayers: countVisibleBackdropFilters(),
      environment,
      incomplete:
        endTime - start < SAMPLE_WINDOW_MS - 8 ||
        endTime - start > SAMPLE_WINDOW_MS + 50,
    };
    records.push(record);
    if (records.length > MAX_RECORDS)
      records.splice(0, records.length - MAX_RECORDS);
    publishSnapshot();
  };

  if (!frameApiAvailable) {
    window.setTimeout(() => finish(performance.now()), SAMPLE_WINDOW_MS);
    return {
      attachHashChange: () => undefined,
    };
  }

  let observedHashChange = false;
  const sampleFrame = (timestamp: number) => {
    if (ended) return;
    frameIntervals.push(timestamp - lastFrameAt);
    lastFrameAt = timestamp;

    const hashChanged =
      observedHashChange || window.location.hash !== initialHash;
    if (routeDomReadyAt === null && hashChanged) {
      const currentRouteRoot = routeRoot();
      const routeTextChanged =
        currentRouteRoot !== initialRouteRoot ||
        (currentRouteRoot?.textContent ?? "") !== initialRouteText;
      if (currentRouteRoot && routeTextChanged) routeDomReadyAt = timestamp;
    } else if (routeDomReadyAt !== null && firstRouteFrameAt === null) {
      firstRouteFrameAt = timestamp;
    }

    const finiteCount = finiteRunningAnimations(document.body);
    if (finiteCount !== null) {
      lastFiniteAnimationCount = finiteCount;
      quietFrames = finiteCount === 0 ? quietFrames + 1 : 0;
      if (quietFrames >= 2 && animationSettleAt === null)
        animationSettleAt = timestamp;
    }

    if (timestamp - start >= SAMPLE_WINDOW_MS) {
      finish(timestamp);
    } else {
      window.requestAnimationFrame(sampleFrame);
    }
  };
  window.requestAnimationFrame(sampleFrame);

  return {
    attachHashChange: () => {
      observedHashChange = true;
    },
  };
}

const activeClickWindows: Array<{
  clickedText: string;
  at: number;
  attachHashChange: () => void;
}> = [];

document.addEventListener(
  "click",
  (event) => {
    const clickedText = clickLabel(event.target);
    if (!clickedText) return;
    const at = performance.now();
    const sample = beginSample("click", clickedText);
    activeClickWindows.push({
      clickedText,
      at,
      attachHashChange: sample.attachHashChange,
    });
    lastClick = { clickedText, at, attached: false };
    window.setTimeout(() => {
      const index = activeClickWindows.findIndex((item) => item.at === at);
      if (index >= 0) activeClickWindows.splice(index, 1);
    }, SAMPLE_WINDOW_MS + 25);
  },
  true,
);

window.addEventListener("hashchange", () => {
  const now = performance.now();
  const click = lastClick;
  const recentClick = click && now - click.at <= SAMPLE_WINDOW_MS + 100;
  if (click && recentClick && !click.attached) {
    const matchingWindow = activeClickWindows.find(
      (item) => item.at === click.at,
    );
    matchingWindow?.attachHashChange();
    click.attached = true;
    return;
  }
  beginSample("hashchange", null);
});

publishSnapshot();

export {};
