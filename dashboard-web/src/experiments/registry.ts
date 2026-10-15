import type { ComponentType } from "react";
import { EvidencePeekDemo } from "./EvidencePeekDemo";
import { MaterialUpdateNoticeDemo } from "./MaterialUpdateNoticeDemo";
import { ReadyRangeBarDemo } from "./ReadyRangeBarDemo";
import { RecallCardDemo } from "./RecallCardDemo";

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
  {
    id: "recall-card",
    title: "RecallCard · 先想再看的复习卡",
    summary:
      "先显示题目，用户主动选择后才显示外部提供的参考答案。不记录正确率或掌握状态。",
    futureSlot: "视频讲义的复习题区",
    Demo: RecallCardDemo,
  },
  {
    id: "material-update-notice",
    title: "MaterialUpdateNotice · 材料更新轻提示",
    summary:
      "修订号真正变化时显示一次“材料有更新”。不推断是否全部完成，不抢焦点、不滚动。",
    futureSlot: "视频同步讲义面板的副标题旁",
    Demo: MaterialUpdateNoticeDemo,
  },
];
