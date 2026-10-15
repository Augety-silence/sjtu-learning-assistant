import type { ComponentType } from "react";

export interface WidgetExperiment {
  id: string;
  title: string;
  summary: string;
  /** Where the widget is expected to live once a host page adopts it. */
  futureSlot: string;
  Demo: ComponentType;
}

/** Experiments are appended here, one per commit. */
export const widgetExperiments: WidgetExperiment[] = [];
