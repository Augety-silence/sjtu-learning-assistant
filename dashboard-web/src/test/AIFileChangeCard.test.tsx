// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { liveStepText } from "@/components/AIExecutionCard";
import { AIFileChangeCard, fileChangeIds } from "@/components/AIFileChangeCard";
import { AISkillManager } from "@/components/AISkillManager";
import {
  applyAiFileChange,
  getAiFileChange,
  listAiAttachments,
  saveAiSkill,
} from "@/lib/api";
import { cleanup, fireEvent, render, screen, waitFor } from "@/test/render";

const pending = {
  change_id: "a".repeat(32),
  path: "notes/第一章.md",
  workspace: "项目",
  mode: "modify" as const,
  summary: "更新小结",
  added: 1,
  removed: 1,
  diff: "--- a/notes/第一章.md\n+++ b/notes/第一章.md\n@@ -1 +1 @@\n-旧的小结\n+新的小结",
  diff_truncated: false,
  status: "pending" as const,
};

vi.mock("@/lib/api", () => ({
  getAiFileChange: vi.fn(async () => pending),
  applyAiFileChange: vi.fn(async () => ({ ...pending, status: "applied" })),
  rejectAiFileChange: vi.fn(async () => ({ ...pending, status: "rejected" })),
  listAiAttachments: vi.fn(async () => ({
    items: [
      {
        id: 7,
        name: "术语表.pdf",
        size: 1,
        sha256: "x",
        status: "local",
        cloud_ready: false,
        summary: null,
        tags: [],
        text_status: "ready",
      },
    ],
  })),
  pickAiAttachment: vi.fn(),
  saveAiSkill: vi.fn(async (skill) => ({
    default_preset_id: "general",
    items: [{ ...skill, id: "u-abcd1234", is_default: false, source: "user" }],
    skill: { id: "u-abcd1234" },
  })),
  deleteAiSkill: vi.fn(),
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("file change card", () => {
  it("collects unique change ids from proposal tool runs", () => {
    const run = (id: unknown, status = "ok") => ({
      tool_name: "propose_file_change",
      phase: "model" as const,
      status: status as "ok",
      arguments_summary: {},
      result_summary: { change_id: id },
    });
    expect(
      fileChangeIds([
        run("a".repeat(32)),
        run("a".repeat(32)),
        run("bad"),
        run("b".repeat(32), "rejected"),
      ]),
    ).toEqual(["a".repeat(32)]);
  });

  it("shows the diff on demand and applies only after the user confirms", async () => {
    render(<AIFileChangeCard changeId={"a".repeat(32)} />);
    expect(await screen.findByText("notes/第一章.md")).toBeTruthy();
    expect(screen.getByText("待确认")).toBeTruthy();
    expect(applyAiFileChange).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "查看差异" }));
    expect(screen.getByLabelText("修改差异").textContent).toContain(
      "+新的小结",
    );
    fireEvent.click(screen.getByRole("button", { name: "应用修改" }));
    expect(await screen.findByText("已写入")).toBeTruthy();
    expect(applyAiFileChange).toHaveBeenCalledWith("a".repeat(32));
    expect(screen.queryByRole("button", { name: "应用修改" })).toBeNull();
    expect(getAiFileChange).toHaveBeenCalledTimes(1);
  });
});

describe("skill manager", () => {
  it("creates a skill with chosen tools and reference files", async () => {
    const onSaved = vi.fn();
    render(<AISkillManager presets={[]} onClose={vi.fn()} onSaved={onSaved} />);
    await waitFor(() => expect(listAiAttachments).toHaveBeenCalled());
    fireEvent.change(screen.getByPlaceholderText(/论文精读/), {
      target: { value: "宏观复习" },
    });
    fireEvent.change(screen.getByPlaceholderText(/告诉助手/), {
      target: { value: "用术语表解释概念" },
    });
    fireEvent.click(screen.getByLabelText("生成文件修改"));
    fireEvent.click(await screen.findByLabelText("术语表.pdf"));
    fireEvent.click(screen.getByRole("button", { name: "创建技能" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    const draft = vi.mocked(saveAiSkill).mock.calls[0][0];
    expect(draft.name).toBe("宏观复习");
    expect(draft.allowed_tools).toContain("propose_file_change");
    expect(draft.reference_attachment_ids).toEqual([7]);
    expect(onSaved.mock.calls[0][1]).toBe("u-abcd1234");
  });

  it("requires a name and instructions", async () => {
    render(<AISkillManager presets={[]} onClose={vi.fn()} onSaved={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "创建技能" }));
    expect((await screen.findByRole("alert")).textContent).toContain(
      "请填写技能名称和指令",
    );
    expect(saveAiSkill).not.toHaveBeenCalled();
  });
});

describe("continuation status", () => {
  it("tells the user a long answer is being continued", () => {
    expect(
      liveStepText({
        streaming: true,
        status: { state: "continuing", part: 2, max_parts: 4, received_at: 0 },
        waitedMs: 0,
        hasTools: false,
      }),
    ).toBe("回答较长，正在继续生成第 2 段…");
  });
});
