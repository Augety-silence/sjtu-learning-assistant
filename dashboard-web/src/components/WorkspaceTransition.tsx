import {
  AnimatePresence,
  motion,
  useIsPresent,
  useReducedMotion,
} from "motion/react";
import { type ReactNode, useEffect, useRef } from "react";
import { motionDuration, motionEase } from "@/lib/motion";

type WorkspaceMode = "workbench" | "assistant";

function WorkspaceScene({
  children,
  focusOnEntry,
  reduced,
}: {
  children: ReactNode;
  focusOnEntry: boolean;
  reduced: boolean;
}) {
  const present = useIsPresent();
  const sceneRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!present || !focusOnEntry) return;
    sceneRef.current
      ?.querySelector<HTMLElement>(
        '.page-heading h1, textarea[aria-label="输入问题"]',
      )
      ?.focus({ preventScroll: true });
  }, [present, focusOnEntry]);

  return (
    <motion.div
      ref={sceneRef}
      className="workspace-scene"
      data-workspace-state={present ? "active" : "exiting"}
      inert={present ? undefined : true}
      aria-hidden={present ? undefined : true}
      initial={reduced ? false : { opacity: 0.78 }}
      animate={{ opacity: 1 }}
      exit={{
        opacity: 0,
        transition: { duration: reduced ? 0 : motionDuration.fast },
      }}
      transition={{
        duration: reduced ? 0 : motionDuration.enter,
        ease: motionEase.out,
      }}
    >
      {children}
    </motion.div>
  );
}

/** Keep normal navigation mounted; only bridge the two independent workspaces. */
export function WorkspaceTransition({
  mode,
  children,
}: {
  mode: WorkspaceMode;
  children: ReactNode;
}) {
  const reduced = Boolean(useReducedMotion());
  const previousMode = useRef(mode);
  const focusOnEntry = previousMode.current !== mode;
  useEffect(() => {
    previousMode.current = mode;
  }, [mode]);
  const scene = (
    <WorkspaceScene key={mode} focusOnEntry={focusOnEntry} reduced={reduced}>
      {children}
    </WorkspaceScene>
  );
  return (
    <div className="workspace-transition">
      <AnimatePresence initial={false} mode="sync">
        {scene}
      </AnimatePresence>
    </div>
  );
}
