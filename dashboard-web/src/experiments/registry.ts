import type { ComponentType } from "react";
import { EvidencePeekDemo } from "./EvidencePeekDemo";
import { ReadyRangeBarDemo } from "./ReadyRangeBarDemo";

export interface WidgetExperiment {
  id: string;
  title: string;
  summary: string;
  /** Where the widget is expected to live once a host page adopts it. */
  futureSlot: string;
  Demo: ComponentType;
}

/** Experiments are appended here, one per commit. */
export const widgetExperiments: WidgetExperiment[] = [
  {
    id: "ready-range-bar",
    title: "ReadyRangeBar · 学习材料可用时间带",
    summary:
      "显示播放位置和已有学习材料覆盖的区间。覆盖不代表质量通过；用户确认后才跳转。",
    futureSlot: "视频下方，或同步学习面板顶部",
    Demo: ReadyRangeBarDemo,
  },
  {
    id: "evidence-peek",
    title: "EvidencePeek · 可展开的字幕证据",
    summary:
      "默认只显示来源与时间，展开后查看引文并可主动跳转。缺少来源或时间时如实显示。",
    futureSlot: "讲义知识点、纠错说明、待核对项",
    Demo: EvidencePeekDemo,
  },
];
