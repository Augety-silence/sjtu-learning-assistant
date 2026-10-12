/** Local synthetic fixture only: observes content entry, never changes app state. */
type RevealEvent = {
  event: "start" | "end" | "cancel";
  route: string;
  surface: string;
  clickedText: string | null;
  animationDuration: string;
  animationName: string;
  elapsedSeconds: number;
  observedAt: number;
};

const records: RevealEvent[] = [];
let clickedText: string | null = null;
let completed = 0;

document.addEventListener(
  "click",
  (event) => {
    if (!(event.target instanceof Element)) return;
    clickedText = event.target.closest("button")?.textContent?.trim() ?? null;
  },
  true,
);

function observe(event: AnimationEvent, phase: RevealEvent["event"]) {
  if (
    !["content-reveal-in", "page-block-enter", "page-block-fade"].includes(
      event.animationName,
    ) ||
    !(event.target instanceof HTMLElement)
  )
    return;
  records.push({
    event: phase,
    route: window.location.hash,
    surface: event.target.className,
    clickedText,
    animationDuration: window.getComputedStyle(event.target).animationDuration,
    animationName: event.animationName,
    elapsedSeconds: event.elapsedTime,
    observedAt: Math.round(performance.now()),
  });
  if (phase === "end") completed += 1;
  if (records.length > 200) records.shift();
  let output = document.getElementById("ui-reveal-audit");
  if (!(output instanceof HTMLScriptElement)) {
    output = document.createElement("script");
    output.id = "ui-reveal-audit";
    output.setAttribute("type", "application/json");
    document.body.append(output);
  }
  output.textContent = JSON.stringify({ records, completed });
  output.dataset.completed = String(completed);
  output.dataset.lastSurface = event.target.className;
  output.dataset.lastPhase = phase;
  output.dataset.lastRoute = window.location.hash;
  if (event.target.classList.contains("ai-chat-thread")) {
    output.dataset.threadPhase = phase;
  }
}

document.addEventListener("animationstart", (event) => observe(event, "start"));
document.addEventListener("animationend", (event) => observe(event, "end"));
document.addEventListener("animationcancel", (event) =>
  observe(event, "cancel"),
);
