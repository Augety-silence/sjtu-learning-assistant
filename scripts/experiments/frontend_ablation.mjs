import { performance } from "node:perf_hooks";
import { mkdir, writeFile } from "node:fs/promises";
import {
  mergeCalendarEvents,
  uniqueCalendarEventsById,
} from "../../dashboard-web/src/lib/calendarEvents.ts";

const OUTPUT = new URL("../../docs/main-audit-2026-10-24/frontend-results.json", import.meta.url);
const RENDER_COMMITS = 6;
const WARMUPS = 2;
const SAMPLES = 5;
const SIZES = [42, 500, 3000];

function makeFixtures(size) {
  const canvas = [];
  const courses = [];
  const uniqueCount = Math.max(1, Math.floor(size * 0.8));
  for (let index = 0; index < size; index += 1) {
    const event = {
      id: `event-${index % uniqueCount}`,
      title: `事件 ${index}`,
      courseName: "合成课程",
      // Missing times do not participate in identity or merge behavior.
      ...(index % 29 === 0 ? {} : { startAt: `2026-10-${String((index % 28) + 1).padStart(2, "0")}T09:00:00+08:00` }),
      eventType: index % 11 === 0 ? "assignment" : "course",
    };
    (index % 2 === 0 ? canvas : courses).push(event);
  }
  // Runtime data can omit an ID even though the TypeScript contract requires it.
  canvas.push({ title: "缺少 ID 的事件", eventType: "course" });
  courses.push({ title: "另一个缺少 ID 的事件", eventType: "assignment" });
  return { canvas, courses };
}

function baselineMerge(canvas, courses) {
  const all = [...canvas, ...courses];
  let identityComparisons = 0;
  const result = all.filter((event, index) =>
    all.findIndex((candidate) => {
      identityComparisons += 1;
      return candidate.id === event.id && candidate.eventType === event.eventType;
    }) === index,
  );
  return { result, identityComparisons };
}

function baselineUniqueById(monthEvents, upcomingEvents) {
  const events = [...monthEvents, ...upcomingEvents];
  let identityComparisons = 0;
  const result = events.filter((event, index) =>
    events.findIndex((candidate) => {
      identityComparisons += 1;
      return candidate.id === event.id;
    }) === index,
  );
  return { result, identityComparisons };
}

function sameOutput(left, right) {
  return left.length === right.length && left.every((event, index) => right[index] === event);
}

function median(values) {
  const sorted = [...values].sort((left, right) => left - right);
  return sorted[Math.floor(sorted.length / 2)];
}

function measureSamples(work) {
  for (let index = 0; index < WARMUPS; index += 1) work();
  const elapsed = [];
  let output;
  for (let index = 0; index < SAMPLES; index += 1) {
    const started = performance.now();
    output = work();
    elapsed.push(performance.now() - started);
  }
  return { output, medianMs: Number(median(elapsed).toFixed(3)), samples: elapsed.length };
}

async function mockBridge(canvas, courses) {
  let requests = 0;
  let calendarRequests = 0;
  return {
    requests: () => requests,
    calendarRequests: () => calendarRequests,
    async loadStatus() {
      requests += 1;
      return { state: "ready" };
    },
    async loadSchedule() {
      requests += 1;
      return { events: courses };
    },
    async loadCalendar() {
      calendarRequests += 1;
      return { monthEvents: canvas, upcomingEvents: courses };
    },
    canvas,
  };
}

const results = [];
for (const size of SIZES) {
  const { canvas, courses } = makeFixtures(size);
  const bridge = await mockBridge(canvas, courses);
  const [status, schedule] = await Promise.all([bridge.loadStatus(), bridge.loadSchedule()]);
  if (status.state !== "ready") throw new Error("mock bridge status mismatch");
  const calendar = await bridge.loadCalendar();

  const baselineSample = measureSamples(() => {
    let result;
    let identityComparisons = 0;
    for (let commit = 0; commit < RENDER_COMMITS; commit += 1) {
      const derived = baselineMerge(bridge.canvas, schedule.events);
      result = derived.result;
      identityComparisons += derived.identityComparisons;
    }
    return { result, identityComparisons };
  });
  const optimizedSample = measureSamples(() =>
    // Calls the production helper once for stable inputs, as useMemo does.
    mergeCalendarEvents(bridge.canvas, schedule.events),
  );
  const setOnlySample = measureSamples(() => {
    let result;
    for (let commit = 0; commit < RENDER_COMMITS; commit += 1) {
      result = mergeCalendarEvents(bridge.canvas, schedule.events);
    }
    return result;
  });
  const appBaselineSample = measureSamples(() =>
    baselineUniqueById(calendar.monthEvents, calendar.upcomingEvents),
  );
  const appOptimizedSample = measureSamples(() =>
    uniqueCalendarEventsById([
      ...calendar.monthEvents,
      ...calendar.upcomingEvents,
    ]),
  );
  const inputEventCount = canvas.length + courses.length;

  results.push({
    fixtureEventsPerSourceCombined: canvas.length + courses.length,
    uniqueScheduleIdentityCount: baselineSample.output.result.length,
    missingIdEvents: 2,
    missingTimeEvents: Math.floor(size / 29) + (size % 2 === 0 ? 1 : 0),
    mockBridgeRequests: bridge.requests(),
    appMockBridgeRequests: bridge.calendarRequests(),
    renderCommits: RENDER_COMMITS,
    scheduleBaseline: {
      derivations: RENDER_COMMITS,
      identityComparisons: baselineSample.output.identityComparisons,
      medianElapsedMs: baselineSample.medianMs,
      samples: baselineSample.samples,
    },
    scheduleOptimized: {
      productionHelper: "mergeCalendarEvents",
      derivations: 1,
      inputVisits: inputEventCount,
      medianElapsedMs: optimizedSample.medianMs,
      samples: optimizedSample.samples,
    },
    scheduleSetOnly: {
      productionHelper: "mergeCalendarEvents",
      derivations: RENDER_COMMITS,
      inputVisits: inputEventCount * RENDER_COMMITS,
      medianElapsedMs: setOnlySample.medianMs,
      samples: setOnlySample.samples,
      outputEquivalent: sameOutput(baselineSample.output.result, setOnlySample.output),
    },
    scheduleOutputEquivalent: sameOutput(
      baselineSample.output.result,
      optimizedSample.output,
    ),
    appIdDeduplication: {
      renderCommits: 1,
      baseline: {
        derivations: 1,
        identityComparisons: appBaselineSample.output.identityComparisons,
        medianElapsedMs: appBaselineSample.medianMs,
        samples: appBaselineSample.samples,
      },
      optimized: {
        productionHelper: "uniqueCalendarEventsById",
        derivations: 1,
        inputVisits: inputEventCount,
        medianElapsedMs: appOptimizedSample.medianMs,
        samples: appOptimizedSample.samples,
      },
      outputEquivalent: sameOutput(
        appBaselineSample.output.result,
        appOptimizedSample.output,
      ),
    },
  });
}

if (results.some((result) =>
  !result.scheduleOutputEquivalent || !result.scheduleSetOnly.outputEquivalent || !result.appIdDeduplication.outputEquivalent
)) {
  throw new Error("baseline and optimized outputs differ");
}

const report = {
  baselineRevision: "3aa54d1a76fd8603dcc43eef5629eb5ae3cc37dd",
  generatedAt: new Date().toISOString(),
  method: {
    baseline: "repeat the original [...canvas, ...course].filter(...findIndex(...)) on every render commit",
    optimized: "Map(id) -> Set(eventType) first-wins merge, memoized for stable source-array references",
    singleFactorControls: "scheduleBaseline versus scheduleSetOnly isolates the merge algorithm at six derivations; scheduleSetOnly versus scheduleOptimized isolates memo reuse with the same production helper",
    appIdDeduplication: "independent one-load factor: original month_events + upcoming_events findIndex(id) versus Set(id), preserving first event and strict equality regardless of eventType",
    fixture: "synthetic event arrays; IDs repeat across and within sources; some events omit startAt and two omit id at runtime; bridge returns fixed status and schedule",
    bridgeRequests: "two per run (status + schedule), equal in both variants",
    renderCommits: `${RENDER_COMMITS} simulated commits with stable source arrays; baseline derives each time and optimized derives once as useMemo would`,
    productionVariant: "the optimized variants import dashboard-web/src/lib/calendarEvents.ts using Node 22 experimental type stripping; iteration counts are inferred from the production helper's single pass, not runtime instrumentation",
    timingMethod: `${WARMUPS} warmup runs and ${SAMPLES} measured runs per variant; report median elapsed time in one Node process`,
    timingLimitations: "machine/JIT/load dependent; baseline timing includes comparison-counter instrumentation while production timing does not, so timing ratios are not an unbiased speedup estimate; the six commits are a useMemo model, not React Profiler measurements; deterministic comparison/iteration counts and output equivalence are primary",
  },
  results,
};

await mkdir(new URL("../../docs/main-audit-2026-10-24/", import.meta.url), {
  recursive: true,
});
await writeFile(OUTPUT, `${JSON.stringify(report, null, 2)}\n`);
process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
