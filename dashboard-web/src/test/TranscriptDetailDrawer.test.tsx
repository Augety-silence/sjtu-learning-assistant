// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TranscriptDetailDrawer } from "@/components/TranscriptDetailDrawer";
import {
  getTranscriptArtifacts,
  getTranscriptV2Artifacts,
  readTranscriptArtifact,
  readTranscriptV2Artifact,
} from "@/lib/api";
import type {
  Phase1ArtifactKind,
  Phase1ArtifactList,
  Phase1ArtifactRead,
  TranscriptJob,
} from "@/lib/types";
import { cleanup, fireEvent, render, screen, waitFor } from "@/test/render";

vi.mock("@/lib/api", () => ({
  getTranscriptArtifacts: vi.fn(),
  getTranscriptV2Artifacts: vi.fn(),
  readTranscriptArtifact: vi.fn(),
  readTranscriptV2Artifact: vi.fn(),
}));

const getV1 = vi.mocked(getTranscriptArtifacts);
const getV2 = vi.mocked(getTranscriptV2Artifacts);
const readV1 = vi.mocked(readTranscriptArtifact);
const readV2 = vi.mocked(readTranscriptV2Artifact);

const job: TranscriptJob = {
  id: "a".repeat(32),
  batch_id: "b".repeat(32),
  source_id: "sjtu-video:12:99",
  title: "管理会计 · 第三讲",
  status: "completed",
  stage: "completed",
  progress: 100,
  attempts: 1,
  message: "字幕已存储并完成 AI 规整。",
};

const phase1Kinds: Phase1ArtifactKind[] = [
  "corrected",
  "correction_diff",
  "uncertain",
  "quality",
];

function v2List(id = job.id): Phase1ArtifactList {
  return {
    available: true,
    status: "completed",
    pipeline_status: "completed",
    quality: null,
    warnings: ["低置信片段未自动修改"],
    items: phase1Kinds.map((kind, index) => ({
      id: `${id}:v2:${String(index + 1).padStart(32, "0")}`,
      kind,
      label: kind,
      content_type: kind === "corrected" ? "text/markdown" : "application/json",
      available: true,
      version: "v2",
      size: 32,
      sha256: "c".repeat(64),
    })),
  };
}

function v2Read(
  kind: Phase1ArtifactKind,
  data?: Record<string, unknown> | unknown[],
  content = JSON.stringify(data ?? {}),
): Phase1ArtifactRead {
  return {
    id: `${job.id}:v2:${"1".repeat(32)}`,
    kind,
    content,
    version: "v2",
    content_type: kind === "corrected" ? "text/markdown" : "application/json",
    size: content.length,
    sha256: "c".repeat(64),
    ...(data === undefined ? {} : { data }),
  };
}

function renderDrawer(currentJob = job) {
  return render(
    <TranscriptDetailDrawer
      job={currentJob}
      onClose={vi.fn()}
      onRetry={vi.fn()}
      onCancel={vi.fn()}
      onReveal={vi.fn()}
    />,
  );
}

function chooseTab(name: string) {
  fireEvent.mouseDown(screen.getByRole("tab", { name }), {
    button: 0,
    ctrlKey: false,
  });
}

beforeEach(() => {
  getV1.mockResolvedValue({
    items: [
      {
        id: `${job.id}:summary`,
        kind: "summary",
        label: "本节要点",
        content_type: "text/markdown",
      },
    ],
  });
  readV1.mockResolvedValue({
    id: `${job.id}:summary`,
    kind: "summary",
    content: "# 本节要点",
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("TranscriptDetailDrawer Phase1", () => {
  it("shows the job message once and keeps low-frequency actions collapsed", async () => {
    getV2.mockResolvedValue({
      available: false,
      status: null,
      pipeline_status: null,
      quality: null,
      warnings: [],
      items: [],
    });

    renderDrawer({ ...job, status: "partial" });

    await screen.findByText(job.message as string);
    expect(screen.getAllByText(job.message as string)).toHaveLength(1);
    const more = screen.getByText("更多操作");
    expect(more.closest("details")?.hasAttribute("open")).toBe(false);
  });

  it("keeps failed at 100% failed when no artifact is available", async () => {
    getV1.mockResolvedValue({ items: [] });
    getV2.mockResolvedValue({
      available: false,
      status: "failed",
      pipeline_status: "failed",
      quality: null,
      warnings: [],
      items: [],
    });

    renderDrawer({ ...job, status: "failed", progress: 100 });

    expect(await screen.findByText("暂无可用结果")).toBeTruthy();
    expect(document.querySelector(".transcript-status-failed")).toBeTruthy();
    expect(screen.getByText("100%")).toBeTruthy();
    expect(screen.queryByText("已有可用结果")).toBeNull();
  });

  it("presents failed at 100% as partial only when an artifact is available", async () => {
    getV2.mockResolvedValue({
      available: false,
      status: "failed",
      pipeline_status: "failed",
      quality: null,
      warnings: [],
      items: [],
    });

    renderDrawer({ ...job, status: "failed", progress: 100 });

    expect(await screen.findByText("已有可用结果")).toBeTruthy();
    expect(document.querySelector(".transcript-status-partial")).toBeTruthy();
    expect(document.querySelector(".transcript-status-failed")).toBeNull();
    expect(screen.getByText("100%")).toBeTruthy();
    expect(screen.queryByText("暂无可用结果")).toBeNull();
  });

  it("adapts structured summaries into a knowledge-example-training flow", async () => {
    getV2.mockResolvedValue({
      available: false,
      status: null,
      pipeline_status: null,
      quality: null,
      warnings: [],
      items: [],
    });
    readV1.mockResolvedValue({
      id: `${job.id}:summary`,
      kind: "summary",
      content: JSON.stringify({
        knowledge_points: ["理解机会成本"],
        classroom_examples: [{ title: "定价决策", content: "比较相关成本" }],
        auxiliary_training: [{ question: "哪些成本与决策相关？" }],
      }),
    });

    renderDrawer();

    expect(await screen.findByRole("heading", { name: "知识点" })).toBeTruthy();
    expect(await screen.findByText("理解机会成本")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "课堂例子" })).toBeTruthy();
    expect(screen.getByText("比较相关成本")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "辅助训练" })).toBeTruthy();
    expect(screen.getByText("哪些成本与决策相关？")).toBeTruthy();
  });

  it("falls back from legacy summary text for all learning-flow sections", async () => {
    getV2.mockResolvedValue({
      available: false,
      status: null,
      pipeline_status: null,
      quality: null,
      warnings: [],
      items: [],
    });

    renderDrawer();

    expect(await screen.findByRole("heading", { name: "知识点" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "课堂例子" })).toBeTruthy();
    expect(screen.getByText(/旧版小结未单独标注课堂例子/)).toBeTruthy();
    expect(screen.getByRole("heading", { name: "辅助训练" })).toBeTruthy();
    expect(screen.getByText(/暂无配套训练/)).toBeTruthy();
  });

  it("keeps legacy records usable and explains that deep correction is absent", async () => {
    getV2.mockResolvedValue({
      available: false,
      status: null,
      pipeline_status: null,
      quality: null,
      warnings: [],
      items: [],
    });

    renderDrawer();

    expect(
      await screen.findByText("本节尚未生成深度校对，可重新执行字幕AI整理"),
    ).toBeTruthy();
    expect(screen.getByRole("tab", { name: "AI校对字幕" })).toHaveProperty(
      "disabled",
      true,
    );
    expect(
      await screen.findByRole("heading", { name: "本节要点" }),
    ).toBeTruthy();
    expect(readV2).not.toHaveBeenCalled();
  });

  it("lazily reads corrected, diff, uncertain and quality artifacts once per kind", async () => {
    getV2.mockResolvedValue(v2List());
    readV2.mockImplementation(async (artifactId) => {
      if (artifactId.endsWith("1".padStart(32, "0")))
        return v2Read(
          "corrected",
          undefined,
          "# AI 校对字幕\n\n本节介绍成本法。",
        );
      if (artifactId.endsWith("2".padStart(32, "0")))
        return v2Read("correction_diff", [
          {
            original: "成本",
            corrected: "成本法",
            type: "technical_term",
            confidence: 0.96,
            reason: "课程术语与上下文一致",
            start: 5,
            end: 7,
            cue_ids: ["cue-000001"],
            evidence: ["课件术语表"],
          },
        ]);
      if (artifactId.endsWith("3".padStart(32, "0")))
        return v2Read("uncertain", [
          {
            text: "量本利",
            confidence: 0.62,
            start: 9,
            end: 12,
            cue_ids: ["cue-000002"],
            candidates: [
              {
                original: "量本利",
                canonical: "量本利分析",
                category: "technical_term",
                confidence: 0.78,
                source: "课程记忆",
                cue_ids: ["cue-000002"],
                evidence: ["章节标题"],
              },
            ],
          },
        ]);
      return v2Read("quality", {
        score: 0.93,
        passed: true,
        metrics: {},
        warnings: ["保留一个待确认术语"],
        schema_pass: true,
        critic_pass_rate: 0.95,
        uncertain_rate: 0.05,
        numeric_change_count: 1,
        unsupported_change_count: 0,
        status: "completed_with_warnings",
      });
    });

    renderDrawer();
    await waitFor(() => expect(getV2).toHaveBeenCalledTimes(1));
    expect(readV2).not.toHaveBeenCalled();

    chooseTab("AI校对字幕");
    expect(
      await screen.findByText("原始字幕未被覆盖", { exact: false }),
    ).toBeTruthy();
    expect(screen.getByRole("heading", { name: "AI 校对字幕" })).toBeTruthy();

    chooseTab("修改对比");
    expect(await screen.findByText("成本法")).toBeTruthy();
    expect(screen.getByText("课程术语与上下文一致")).toBeTruthy();
    expect(screen.getByText("课件术语表")).toBeTruthy();

    chooseTab("待确认");
    expect(await screen.findByText("量本利分析")).toBeTruthy();
    expect(screen.getByText(/后续版本将支持逐项确认/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /保存|确认/ })).toBeNull();

    chooseTab("质量");
    expect(await screen.findByText("95%")).toBeTruthy();
    expect(screen.getByText("保留一个待确认术语")).toBeTruthy();
    expect(screen.getByText("低置信片段未自动修改")).toBeTruthy();

    chooseTab("本节要点");
    chooseTab("AI校对字幕");
    await waitFor(() => expect(readV2).toHaveBeenCalledTimes(4));
  });

  it("degrades malformed artifact data into a safe empty state", async () => {
    getV2.mockResolvedValue(v2List());
    readV2.mockResolvedValue(
      v2Read("correction_diff", [null, "bad", { unexpected: true }]),
    );

    renderDrawer();
    await screen.findByRole("tab", { name: "修改对比" });
    chooseTab("修改对比");

    expect(await screen.findByText("未发现可展示的字幕修改。")).toBeTruthy();
  });

  it("retries a failed artifact read and does not cache the rejection", async () => {
    getV2.mockResolvedValue(v2List());
    readV2
      .mockRejectedValueOnce(new Error("校对工件暂时不可读"))
      .mockResolvedValueOnce(
        v2Read("corrected", undefined, "重新读取后的校对字幕"),
      );

    renderDrawer();
    await screen.findByRole("tab", { name: "AI校对字幕" });
    chooseTab("AI校对字幕");
    expect(await screen.findByText("校对工件暂时不可读")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "重试加载" }));
    expect(await screen.findByText("重新读取后的校对字幕")).toBeTruthy();
    expect(readV2).toHaveBeenCalledTimes(2);
  });

  it("clears artifact caches when the drawer switches jobs", async () => {
    getV2.mockImplementation(async (jobId) => v2List(jobId));
    readV2.mockImplementation(async (artifactId) =>
      v2Read(
        "corrected",
        undefined,
        artifactId.startsWith(job.id) ? "第一份校对" : "第二份校对",
      ),
    );
    const view = renderDrawer();
    await screen.findByRole("tab", { name: "AI校对字幕" });
    chooseTab("AI校对字幕");
    expect(await screen.findByText("第一份校对")).toBeTruthy();

    const nextJob = { ...job, id: "d".repeat(32), title: "下一讲" };
    view.rerender(
      <TranscriptDetailDrawer
        job={nextJob}
        onClose={vi.fn()}
        onRetry={vi.fn()}
        onCancel={vi.fn()}
        onReveal={vi.fn()}
      />,
    );
    await waitFor(() => expect(getV2).toHaveBeenLastCalledWith(nextJob.id));
    chooseTab("AI校对字幕");

    expect(await screen.findByText("第二份校对")).toBeTruthy();
    expect(screen.queryByText("第一份校对")).toBeNull();
    expect(readV2).toHaveBeenCalledTimes(2);
  });

  it("shows a restrained Phase1 warning without turning completed v1 into failure", async () => {
    getV2.mockResolvedValue({
      ...v2List(),
      status: "partial",
      pipeline_status: "partial",
    });
    renderDrawer({
      ...job,
      phase1_status: "partial",
      pipeline_status: "partial",
      partial_warning: true,
      phase1_warning: "模型超时",
    });

    expect(await screen.findByText("深度校对部分完成")).toBeTruthy();
    expect(document.querySelector(".transcript-status-partial")).toBeTruthy();
    expect(document.querySelector(".transcript-status-failed")).toBeNull();
  });
});
