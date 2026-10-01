// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { type GradingSubmission, GradingView } from "@/components/GradingView";
import { cleanup, fireEvent, render, screen, waitFor } from "@/test/render";

afterEach(cleanup);

const submissions: GradingSubmission[] = [
  {
    id: "sub-1",
    studentId: "student-1",
    studentName: "王同学",
    submittedAt: "2026-09-28T10:00:00+08:00",
    status: "submitted",
    score: 80,
    attempt: 2,
    body: "<p>在线答案</p>",
    attachments: [
      {
        id: "file-1",
        name: "answer.pdf",
        downloadStatus: "ready",
        preview: { kind: "text", content: "答案预览" },
      },
    ],
    comments: [{ id: "c1", author: "助教", content: "请补充引用" }],
  },
];

describe("GradingView", () => {
  it("shows submission detail, comments and reference status", () => {
    render(
      <GradingView
        submissions={submissions}
        referenceFiles={[
          { id: "r1", name: "评分标准.pdf", status: "downloaded" },
        ]}
        maxScore={100}
      />,
    );
    expect(screen.getByText("在线答案")).toBeTruthy();
    fireEvent.mouseDown(screen.getByRole("tab", { name: "评论" }), {
      button: 0,
      ctrlKey: false,
    });
    expect(screen.getByText("请补充引用")).toBeTruthy();
    fireEvent.mouseDown(screen.getByRole("tab", { name: "参考文件" }), {
      button: 0,
      ctrlKey: false,
    });
    expect(screen.getByText("评分标准.pdf")).toBeTruthy();
    expect(screen.getByText("已下载")).toBeTruthy();
  });

  it("saves a score and adds a comment", async () => {
    const saveScore = vi.fn(async () => undefined);
    const addComment = vi.fn(async () => undefined);
    render(
      <GradingView
        submissions={submissions}
        maxScore={100}
        onSaveScore={saveScore}
        onAddComment={addComment}
      />,
    );
    fireEvent.change(screen.getByLabelText("评分（满分 100）"), {
      target: { value: "95" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存评分" }));
    await waitFor(() =>
      expect(saveScore).toHaveBeenCalledWith({
        submissionId: "sub-1",
        studentId: "student-1",
        score: 95,
      }),
    );
    fireEvent.mouseDown(screen.getByRole("tab", { name: "评论" }), {
      button: 0,
      ctrlKey: false,
    });
    fireEvent.change(screen.getByLabelText("添加评论"), {
      target: { value: "完成得很好" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发布评论" }));
    await waitFor(() =>
      expect(addComment).toHaveBeenCalledWith({
        submissionId: "sub-1",
        studentId: "student-1",
        comment: "完成得很好",
      }),
    );
  });
});
