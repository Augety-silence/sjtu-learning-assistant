/** 合成视觉验收，不读取真实课程、凭据或调用模型。 */
import { useState } from "react";
import ReactDOM from "react-dom/client";
import { ToastProvider } from "@/components/Toast";
import { TranscriptDetailDrawer } from "@/components/TranscriptDetailDrawer";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/Tabs";
import { VideosView } from "@/components/VideosView";
import type { BridgeResponse } from "@/lib/api";
import type { TranscriptJob } from "@/lib/types";
import "@/index.css";
import "@/aura-pages.css";
import "@/performance.css";
import "@/workspace-motion.css";
import "@/page-motion.css";
import "@/selection-indicator.css";

const job: TranscriptJob = {
  id: "a".repeat(32),
  batch_id: "b".repeat(32),
  source_id: "sjtu-video:12:99",
  title: "合成课程 · 第三讲",
  status: "failed",
  stage: "map_reduce",
  progress: 55,
  attempts: 1,
  message: "旧校对失败",
  lesson_notes_status: "completed",
};
const kinds = ["lesson_summary", "original"] as const;
window.pywebview = {
  api: {
    invoke: async <T,>(
      action: string,
      payload?: Record<string, unknown>,
    ): Promise<BridgeResponse<T>> => {
      const data =
        action === "transcript_artifacts"
          ? {
              items: kinds.map((kind) => ({
                id: `${job.id}:${kind}`,
                kind,
                label: kind,
                content_type: "text/markdown",
              })),
            }
          : action === "transcript_artifact_read"
            ? {
                id: payload?.artifact_id,
                kind: "original",
                content: String(payload?.artifact_id).endsWith(":original")
                  ? "# 原始字幕\n\n## 00:00:05\n\n    老师先讲机会成本，再讲决策条件。"
                  : "# 当堂老师讲了什么\n\n老师先介绍机会成本，再说明相关成本的适用条件。\n\n## 决策主线\n\n请连同条件一起阅读，文字可自由选择复制。",
              }
            : {};
      return { ok: true, data } as BridgeResponse<T>;
    },
  },
};

function Audit() {
  const [source, setSource] = useState("video");
  const [panel, setPanel] = useState("slides");
  const [open, setOpen] = useState(false);
  return (
    <main style={{ padding: 16 }}>
      <VideosView
        courseId={12}
        courseName="合成课堂：视频优先"
        videos={Array.from({ length: 18 }, (_, i) => ({
          id: `sjtu-video:12:${i + 1}`,
          title: `第${i + 1}节：课堂录像与长标题布局验收`,
          courseName: "合成课堂",
          source: "video_space" as const,
          playable: true,
          supportsSubtitle: true,
          supportsSlidesPdf: true,
          classroom: "合成教室",
          duration: 5400,
        }))}
      />
      <h1>字幕与页签隔离验收</h1>
      <Tabs value={source} onValueChange={setSource}>
        <TabsList indicator aria-label="视频来源">
          <TabsTrigger value="video">视频空间</TabsTrigger>
          <TabsTrigger value="canvas">Canvas</TabsTrigger>
        </TabsList>
      </Tabs>
      <Tabs value={panel} onValueChange={setPanel}>
        <TabsList indicator aria-label="视频内容">
          <TabsTrigger value="slides">讲义</TabsTrigger>
          <TabsTrigger value="transcript">字幕</TabsTrigger>
          <TabsTrigger value="ai">AI</TabsTrigger>
        </TabsList>
      </Tabs>
      <button type="button" onClick={() => setOpen(true)}>
        打开合成字幕
      </button>
      {open && (
        <TranscriptDetailDrawer job={job} onClose={() => setOpen(false)} />
      )}
    </main>
  );
}
const root = document.getElementById("root");
if (root)
  ReactDOM.createRoot(root).render(
    <ToastProvider>
      <Audit />
    </ToastProvider>,
  );
